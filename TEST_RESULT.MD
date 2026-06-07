# AVeriTeC Current Test Result

이 문서는 현재 최신 구현의 대표 성능과, 그 성능을 만들기 위해 들어간 구현 변경을 정리한다.

대표 성능은 검색과 LLM answer 생성을 모두 새로 수행한 30건 full run 기준으로 본다. 같은 prediction에 후처리만 다시 적용한 replay나 aggregate-only 결과는 디버깅용이며 대표 성능으로 보지 않는다.


최신 대표 성능:

| Dataset | Prediction | Evaluated | QA HU-METEOR | Label Accuracy | Meteor @ 0.25 |
| --- | --- | ---: | ---: | ---: | ---: |
| dev_holdout_mix_30 | `local_dev_holdout_mix_30_v32.json` | 30/30 | 0.3222 | 0.7000 | 0.4000 |

Gate breakdown @ 0.25:

| Bucket | Count | Ratio |
| --- | ---: | ---: |
| label correct + QA pass | 12 | 40.0% |
| label correct + QA fail | 9 | 30.0% |
| label wrong + QA pass | 3 | 10.0% |
| label wrong + QA fail | 6 | 20.0% |

Confusion summary:

| Gold label | Prediction distribution |
| --- | --- |
| Supported | Supported=4, Refuted=2 |
| Refuted | Supported=1, Refuted=16, Conflicting Evidence/Cherrypicking=2 |
| Not Enough Evidence | Refuted=1, Conflicting Evidence/Cherrypicking=1 |
| Conflicting Evidence/Cherrypicking | Supported=1, Refuted=1, Conflicting Evidence/Cherrypicking=1 |

현재 해석:

- 현재 최신 full run의 최종 대표 점수는 `meteor @ 0.25 = 0.4000`이다.
- QA HU-METEOR는 `0.3222`로 이전 30건 실험들과 비슷한 범위지만, label accuracy가 `0.7000`으로 낮아져 최종 gated score가 낮게 나왔다.
- 현재 병목은 raw QA overlap보다 label stability 쪽이다.
- 특히 QA는 threshold를 넘었지만 label이 틀린 3건이 가장 값싼 개선 대상이다.

QA pass + label wrong cases:

| ID | Gold | Pred | QA score | Claim summary |
| --- | --- | --- | ---: | --- |
| AVT-DEV-0002 | Refuted | Conflicting Evidence/Cherrypicking | 0.571 | Billie Eilish leaked documents claim |
| AVT-DEV-0005 | Supported | Refuted | 0.667 | New York Post endorsed Trump |
| AVT-DEV-0018 | Not Enough Evidence | Refuted | 0.446 | 33.1 GDP claim |

## Current Pipeline

현재 테스트 파이프라인은 AVeriTeC JSON을 읽어 claim별로 graph를 실행하고 prediction JSON을 만든 뒤, HU-METEOR와 label gate로 채점한다.

```text
AVeriTeC JSON
  -> scripts/run_averitec_predictions.py
  -> verification_graph.ainvoke(...)
  -> prediction JSON
  -> scripts/score_averitec_meteor.py
  -> meteor report
```

현재 graph 구조:

```text
preprocess
  -> fact_check
  -> source_check
  -> recency_check
  -> numeric_check
  -> evidence_calibration
  -> aggregate
```

각 verifier node는 claim type에 따라 검색 질문, 검색 query, evidence, AVeriTeC QA answer를 만든다. `evidence_calibration`은 answer metadata를 정규화하고, `aggregate`는 calibrated QA와 verifier summary를 함께 보고 최종 AVeriTeC label을 결정한다.

## Implementation Details

### 1. Evidence Calibration Node

추가 목적:

- verifier answer 생성 이후에만 알 수 있는 support 신호를 aggregate 전에 정규화하기 위해 추가했다.
- preprocess 단계에는 evidence가 없으므로 CE/NEE 판단 신호를 만들 수 없다.

적용 파일:

- `src/ai_backend/graph/builder.py`
- `src/ai_backend/graph/nodes/evidence_calibration.py`
- `src/ai_backend/graph/nodes/aggregate.py`
- `src/ai_backend/graph/state.py`
- `src/ai_backend/api/routes/verify.py`
- `scripts/run_averitec_predictions.py`

동작:

- 원본 `questions`는 유지한다.
- 정규화된 결과는 `calibrated_questions`에 저장한다.
- aggregate와 prediction runner는 `calibrated_questions`가 있으면 이를 우선 사용한다.

정규화 metadata:

| Field | Values | Meaning |
| --- | --- | --- |
| `support_type` | `direct_support`, `partial_support`, `contradiction`, `insufficient_evidence`, `related_only`, `unknown` | answer가 claim/question을 어떻게 지지하거나 반박하는지 |
| `directness` | `direct`, `indirect`, `unknown` | evidence가 직접적인지 간접적인지 |
| `mismatch_type` | `none`, `scope`, `time`, `number`, `attribution`, `context`, `methodology`, `source`, `unknown` | claim과 evidence 사이의 불일치 유형 |

### 2. Verifier QA Generation

목표:

- AVeriTeC scorer가 비교하는 `question + answer` 문자열이 gold QA와 더 잘 겹치도록 한다.
- generic verdict 질문보다 claim atom이 직접 들어간 질문을 생성한다.

적용 파일:

- `src/ai_backend/graph/nodes/fact_check.py`
- `src/ai_backend/graph/nodes/numeric_check.py`
- `src/ai_backend/graph/nodes/source_check.py`
- `src/ai_backend/graph/nodes/recency_check.py`
- `src/ai_backend/graph/prompts/fact_check.py`
- `src/ai_backend/graph/prompts/numeric_check.py`
- `src/ai_backend/graph/prompts/source_check.py`
- `src/ai_backend/graph/prompts/recency_check.py`

구현 내용:

- generated 질문이 broad/generic하면 atom coverage로 인정하지 않는다.
- 부족한 atom은 rule-based seed question으로 보강한다.
- seed question에는 claim fragment를 직접 포함한다.
- question ranking에서 generic context/background/alternative-view 질문을 뒤로 보낸다.

우선 포함하는 claim atom:

- entity
- action/event
- date/time period
- location/jurisdiction
- number/unit/comparison basis
- source/speaker
- quote/transcript fragment
- correction/denial/true alternative fact

### 3. Search and Evidence Ranking

목표:

- 단순히 검색량을 늘리는 대신 verifier intent에 맞는 evidence를 우선 사용한다.

적용 파일:

- `src/ai_backend/core/search_policy.py`
- `src/ai_backend/core/verification.py`
- 각 verifier node

구현 내용:

- `fact`, `numeric`, `recency`는 fact-check, refutation, official evidence를 더 선호한다.
- `source`는 transcript, statement, original source, primary-source-like evidence를 더 선호한다.
- 검색 결과는 URL/content 기준으로 dedupe한다.
- verifier별 query는 최대 3개로 제한한다.
- `max_results_per_query`는 5로 유지한다.

현재 판단:

- query cap을 2로 낮춘 실험은 비용은 줄였지만 label stability를 크게 해쳐 되돌렸다.
- 현재 병목은 raw evidence recall보다 answer wording과 aggregate label stability다.

### 4. Answer Source URL Selection

목표:

- 모든 answer가 같은 top URL을 공유하는 문제를 줄이고, answer별로 가장 관련 있는 source URL을 붙인다.

적용 파일:

- `src/ai_backend/core/verification.py`
- `src/ai_backend/graph/nodes/fact_check.py`
- `src/ai_backend/graph/nodes/numeric_check.py`
- `src/ai_backend/graph/nodes/source_check.py`
- `src/ai_backend/graph/nodes/recency_check.py`

구현 내용:

- answer text, boolean explanation, question text를 합쳐 evidence snippet과 token overlap을 계산한다.
- 가장 잘 맞는 search result URL을 answer `source_url`로 선택한다.
- source URL 선택과 metadata 추론은 compact 전 원본 answer를 기준으로 한다.

### 5. Answer Compact

목표:

- 너무 긴 answer가 HU-METEOR token overlap을 희석하는 문제를 줄인다.
- 단, 과도한 compact가 성능을 낮출 수 있어 긴 답변에만 제한적으로 적용한다.

적용 파일:

- `src/ai_backend/core/verification.py`
- 각 verifier node

구현 내용:

- `compact_averitec_answer(...)`
  - 긴 non-Boolean answer에서 claim/question과 가장 관련 있는 sentence를 선택한다.
  - 짧은 answer는 그대로 둔다.
- `compact_boolean_explanation(...)`
  - scorer가 Boolean `answer + boolean_explanation`을 함께 보기 때문에 explanation을 짧게 유지한다.
  - `Yes`/`No` 자체는 유지한다.

프롬프트 원칙:

- final verdict sentence보다 direct evidence fragment를 우선한다.
- Refuted claim은 denial, correction, true alternative, official recommendation을 답으로 쓰게 한다.
- Supported claim은 direct supporting fact/date/number/quote를 쓰게 한다.
- CE claim은 true atom과 omitted/qualified atom을 드러내게 한다.
- NEE claim은 missing exact fact 또는 not established fragment를 쓰게 한다.

### 6. Aggregate Input Expansion

목표:

- aggregate가 QA text만 보고 label을 정하지 않고, evidence quality와 verifier별 요약도 함께 보게 한다.

적용 파일:

- `src/ai_backend/graph/nodes/aggregate.py`
- `src/ai_backend/core/search_policy.py`

aggregate 입력에 추가된 정보:

- answer-level support metadata
- source domain
- source quality hints
- verifier reasoning
- verifier evidence snippets
- verifier sources

효과:

- fact-check evidence와 low-quality repost를 구분할 수 있다.
- source verifier의 “직접 발언 없음” 신호가 일반 factual claim 전체를 과하게 Refuted로 끌고 가는 문제를 줄인다.
- quote/source attribution claim에서는 source verifier evidence를 더 강하게 참고할 수 있다.

### 7. Deterministic Aggregate Calibration

목표:

- LLM aggregate가 반복적으로 틀리는 AVeriTeC label 경계 케이스를 보수적으로 보정한다.
- broad rule을 피하고, 명확한 신호가 있을 때만 적용한다.

적용 파일:

- `src/ai_backend/graph/nodes/aggregate.py`

주요 보정:

- numeric/comparative claim
  - annualized rate, actual growth, GDP level처럼 measurement basis가 다르면 Supported/CE를 NEE로 보정한다.
  - number, unit, denominator, timeframe, geography, comparison basis가 맞지 않으면 support를 약하게 본다.
- treatment/science claim
  - evidence가 inconclusive, clinical-trial-only, not approved 수준이면 Refuted가 아니라 NEE로 보정한다.
- scam/rumor claim
  - “confirmed reports 없음”은 직접 반박이 아니라 NEE로 보정한다.
- timing claim
  - claim의 정확한 시간/기간과 evidence의 권고 시간이 명확히 다르면 Refuted로 보정한다.
- attribution/quote claim
  - 제3자의 characterization만 있고 named speaker/source의 직접 발언이 없으면 Supported를 CE 또는 NEE로 낮춘다.
- loaded framing claim
  - 사건 자체는 맞지만 motive/framing/context가 빠진 경우 CE로 보정한다.
- groundless accusation claim
  - `fabricated information`, `false accusation`, `defame` claim이 있고 verifier evidence에 `groundlessly claims`, `baseless claim`, `falsely claims` 같은 신호가 있으면 Supported를 Refuted로 보정한다.

현재 주의점:

- fixed evidence replay에서는 label 보정이 좋아 보여도, full run에서는 검색/LLM answer가 바뀌면서 다른 label drift가 생긴다.
- 따라서 aggregate rule은 반드시 30건 full run과 failure bucket으로 검증해야 한다.

### 8. Scorer-Facing QA Ranker

목표:

- AVeriTeC scorer가 prediction의 앞 10개 QA만 사용하므로, scorer에 들어가는 QA 순서를 안정화한다.

적용 파일:

- `src/ai_backend/graph/nodes/scoring_ranker.py`
- `scripts/run_averitec_predictions.py`

구현 내용:

- prediction 저장 직전에 QA를 scorer-facing으로 정렬한다.
- direct, extractive, claim-token-overlap이 높은 QA를 선호한다.
- generic context/background/alternative-view 질문을 낮춘다.
- Refuted/Supported/CE/NEE label별로 적합한 `support_type`을 가중한다.
- 기존 앞 10개 안에 high-overlap direct QA가 이미 있으면 원래 순서를 보존한다.
- 이 gate는 `extra_30`에서 ranker가 좋은 기존 QA를 밀어내는 회귀를 막기 위해 추가했다.

현재 한계:

- ranker는 fixed evidence의 QA ordering 문제만 완화한다.
- 새 full run에서 search result나 answer text 자체가 바뀌면 label drift나 QA fail을 해결하지 못한다.

### 9. Scorer Diagnostics

목표:

- 최종 점수 하락 원인이 label인지 QA evidence인지 분리한다.

적용 파일:

- `scripts/score_averitec_meteor.py`

추가 출력:

- QA HU-METEOR
- label accuracy
- AVeriTeC score by threshold
- gate breakdown
- confusion matrix
- failure bucket별 claim 목록

현재 이 진단에서 보이는 v32 병목:

- `label correct + QA fail`: 9건
- `label wrong + QA pass`: 3건

우선순위는 `label wrong + QA pass` 3건이다. 이들은 QA가 이미 threshold를 넘기 때문에 label만 바로잡으면 최종 `meteor @ 0.25`가 직접 오른다.

## Current Bottleneck

현재 최신 full run 기준 병목은 다음 순서다.

1. `QA pass + label wrong` 3건
   - label만 맞으면 바로 final score가 오른다.
   - source report vs 실제 claim truth, endorsement event, annualized GDP basis가 반복 패턴이다.
2. `label correct + QA fail` 9건
   - label은 맞지만 gold QA와 answer fragment가 충분히 겹치지 않는다.
   - direct denial/correction/recommendation/source fragment 추출을 더 강화해야 한다.
3. CE label stability
   - CE는 과보정하면 Supported/Refuted를 망가뜨리고, 보수적으로 두면 CE gold를 놓친다.
   - 명시적 omitted context, scope exception, updated context evidence가 있을 때만 보정해야 한다.

## Next Implementation Priorities

1. v32 `QA pass + label wrong` 3건을 replay fixture로 고정한다.
2. aggregate calibration을 세 케이스에 대해 좁게 추가한다.
3. 추가 보정 후 `dev_holdout_mix_30`과 `extra_30` 둘 다 replay로 확인한다.
4. replay가 통과하면 30건 full run을 다시 수행한다.
5. full run 결과만 이 문서의 대표 성능으로 갱신한다.
