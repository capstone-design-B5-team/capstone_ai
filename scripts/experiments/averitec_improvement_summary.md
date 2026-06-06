# AVeriTeC 성능 개선 요약

## 현재 보존한 실험 결과

`scripts/experiments`에는 현재 비교에 필요한 최종 30개 실험 결과만 남긴다.

### dev_30

- Gold: `scripts/averitec_dev_30.json`
- Prediction: `scripts/experiments/local_dev_30_v4.json`
- 평가 개수: 30/30
- QA HU-METEOR: 0.3217
- Label accuracy: 0.7000
- Veracity scores (meteor @ 0.25): 0.5333

### holdout_30

- Gold: `scripts/averitec_holdout_30.json`
- Prediction: `scripts/experiments/local_holdout_30_v4.json`
- 평가 개수: 30/30
- QA HU-METEOR: 0.3406
- Label accuracy: 0.6667
- Veracity scores (meteor @ 0.25): 0.5667

dev_30에서는 30개 중 16개, holdout_30에서는 30개 중 17개가 AVeriTeC의 두 조건을 동시에 통과했다는 뜻이다.

1. 최종 라벨이 gold label과 같아야 한다.
2. 예측 QA evidence의 HU-METEOR가 0.25를 넘어야 한다.

즉 평가식은 사실상 아래와 같다.

```text
score@0.25 = count(label_correct AND qa_hu_meteor > 0.25) / total_examples
```

따라서 라벨 정확도만 높아도 부족하고, QA evidence만 좋아도 부족하다. 같은 항목에서 라벨과 QA가 동시에 맞아야 점수가 오른다.

## 초기 상태의 문제

초기 `averitec_dev_30.json` 기준 baseline은 대략 다음 상태였다.

- Label accuracy: 18/30
- 주요 실패:
  - Numerical Claim: 0/4
  - Not Enough Evidence: 0/3
  - Conflicting Evidence/Cherrypicking: 0/3

초기 구조의 핵심 문제는 관련 있어 보이는 evidence를 너무 쉽게 신뢰한다는 점이었다. claim의 정확한 범위, 날짜, 수치, 출처 맥락, evidence 부재 여부가 확인되기 전에 `Supported` 또는 `Refuted`로 결론을 내리는 경향이 있었다.

이후 로컬 HU-METEOR scorer를 추가하면서 목표를 단순 label accuracy가 아니라 `Veracity scores (meteor @ 0.25)`로 맞췄다.

## 실제로 점수 향상에 기여한 변경

### 1. Verifier intent 기반 검색 랭킹

검색 결과 랭킹에 verifier intent를 넘기도록 바꿨다.

- `fact`
- `numeric`
- `source`
- `recency`

이 변경으로 verifier별로 선호해야 할 evidence가 달라졌다.

- fact/numeric/recency는 fact-check, refutation, official, 날짜가 있는 evidence를 더 선호한다.
- source는 primary source, transcript, statement, original-source evidence를 더 선호한다.
- Facebook, Reddit, YouTube, NamuWiki, 일반 블로그 같은 낮은 품질의 도메인은 감점한다.

관련 파일:

- `src/ai_backend/core/search_policy.py`
- `src/ai_backend/core/verification.py`
- `src/ai_backend/graph/nodes/fact_check.py`
- `src/ai_backend/graph/nodes/source_check.py`
- `src/ai_backend/graph/nodes/numeric_check.py`
- `src/ai_backend/graph/nodes/recency_check.py`

이 변경은 최종 라벨을 억지로 밀지 않고 evidence retrieval 품질을 올리는 방향이라 일반화 가능성이 높다.

### 2. 답변별 source URL attribution

이전에는 한 claim의 모든 answer가 top-ranked URL을 공유하는 경우가 많았다. 그러면 answer text는 다른 evidence에서 나온 것처럼 보이는데 source URL은 첫 번째 결과를 가리키는 문제가 생겼다.

현재는 각 answer마다 가장 잘 맞는 source URL을 고른다. 비교에는 다음 텍스트를 사용한다.

- answer text
- boolean explanation
- question text
- 후보 검색 결과의 title/snippet

관련 파일:

- `src/ai_backend/core/verification.py`

관련 함수:

- `select_answer_source_url(...)`

AVeriTeC는 최종 라벨뿐 아니라 QA evidence를 평가하므로, answer와 source URL이 맞는 것이 중요하다. 이 변경은 QA 패키지의 일관성을 높였고 HU-METEOR gate 통과에 기여했다.

### 3. source check의 영어 검색 쿼리 강제

영어 claim에서는 `source_check`도 검색 쿼리를 영어로 만들도록 공통 language instruction을 붙였다.

관련 파일:

- `src/ai_backend/graph/nodes/source_check.py`

이전에는 fact/numeric 쪽보다 source check의 검색 쿼리가 약해지는 경우가 있었다. 특히 quote/source verification에서 원문, transcript, statement를 찾는 성능에 영향을 줬다.

### 4. aggregate에 source quality hint 전달

aggregate node가 answer source에 대해 내부 힌트를 받도록 했다.

- `official_domain`
- `primary_source_domain`
- `fact_check_domain`
- `low_quality_domain`

prediction JSON schema는 그대로 유지한다. 다만 aggregate가 최종 라벨을 고를 때 fact-check evidence인지, primary source인지, 낮은 품질의 repost인지 참고할 수 있다.

관련 파일:

- `src/ai_backend/graph/nodes/aggregate.py`
- `src/ai_backend/core/search_policy.py`

이 변경은 특히 fact-check/refutation evidence와 social media snippet을 구분하는 데 도움이 된다.

### 5. `max_results_per_query = 5`

가장 최근의 큰 점수 상승은 각 verifier node의 `max_results_per_query`를 3에서 5로 올린 뒤 나왔다.

관련 파일:

- `src/ai_backend/graph/nodes/fact_check.py`
- `src/ai_backend/graph/nodes/source_check.py`
- `src/ai_backend/graph/nodes/numeric_check.py`
- `src/ai_backend/graph/nodes/recency_check.py`

중요한 점은 검색 쿼리 수를 넓힌 것이 아니라, 기존의 집중된 쿼리마다 가져오는 결과 수를 늘렸다는 것이다.

- 좋은 방향: 같은 focused query set에서 후보 evidence recall을 올린다.
- 나쁜 방향: broad query를 많이 추가해서 claim에서 멀어진 evidence를 끌어온다.

성공한 `local_dev_30_v4`는 첫 3개의 deduplicated query cap은 유지하고, query당 result만 5개로 늘린 상태다.

## 실패한 방향

### broad CE/NEE query diversification

한 번은 CE/NEE를 잡기 위해 verifier prompt와 query selection을 넓게 바꿨다.

넣었던 방향은 대략 다음과 같았다.

- 항상 direct refutation/counterexample query를 포함한다.
- 항상 omitted context/exception query를 포함한다.
- 항상 official/statistical evidence query를 포함한다.
- exact scope/time/entity가 안 맞으면 `Unanswerable`로 답하게 한다.

그 결과:

- 예측 결과: `local_dev_30_v2.json`
- QA HU-METEOR: 0.2777
- Label accuracy: 0.5667
- Veracity scores (meteor @ 0.25): 0.4000
- CE F1: 0.0000

이는 단순히 `max_results_per_query=5`를 적용한 방향보다 나빴다.

진단:

- prompt 압력이 너무 넓었다.
- gold QA와 잘 맞지 않는 배경/반례 질문이 늘었다.
- CE를 더 잘 맞추지 못하면서 Supported/Refuted 판정만 흔들었다.
- CE gold example은 여전히 제대로 CE로 분류하지 못했다.

결론:

- 모든 verifier에 CE/NEE 압력을 넓게 넣으면 안 된다.
- 모든 claim에 generic exception/counterexample query를 넣으면 안 된다.
- QA는 exact claim 중심으로 유지하고, CE/NEE 구분은 aggregate에서 좁게 처리해야 한다.

## 현재 v4 breakdown

### dev_30

`local_dev_30_v4.json`의 meteor threshold 0.25 기준 breakdown:

- label correct + QA pass: 16 (53.3%)
- label correct + QA fail: 5 (16.7%)
- label wrong + QA pass: 8 (26.7%)
- label wrong + QA fail: 1 (3.3%)

Confusion matrix:

- Supported: Supported=2, Refuted=1, Conflicting Evidence/Cherrypicking=1
- Refuted: Supported=1, Refuted=18, Conflicting Evidence/Cherrypicking=1
- Not Enough Evidence: Refuted=2, Not Enough Evidence=1
- Conflicting Evidence/Cherrypicking: Supported=1, Refuted=2

### holdout_30

`local_holdout_30_v4.json`의 meteor threshold 0.25 기준 breakdown:

- label correct + QA pass: 17 (56.7%)
- label correct + QA fail: 3 (10.0%)
- label wrong + QA pass: 4 (13.3%)
- label wrong + QA fail: 6 (20.0%)

Confusion matrix:

- Supported: Supported=4, Refuted=3
- Refuted: Refuted=16, Not Enough Evidence=1
- Not Enough Evidence: Refuted=1
- Conflicting Evidence/Cherrypicking: Supported=2, Refuted=3

점수는 목표였던 0.4~0.5를 넘었지만 모든 라벨이 해결된 것은 아니다. 특히 CE가 아직 약하다.

- dev_30 Conflicting Evidence/Cherrypicking F1: 0.0000
- holdout_30 Conflicting Evidence/Cherrypicking F1: 0.0000
- holdout_30 Not Enough Evidence F1: 0.0000

이번 점수 상승은 주로 다음에서 나왔다.

- Refuted label이 강해졌다.
- QA HU-METEOR가 올라갔다.
- label이 맞은 항목 중 더 많은 항목이 QA threshold 0.25를 넘었다.

반대로 CE를 잘 맞춰서 오른 점수는 아니다.

## AVeriTeC가 시스템에 요구하는 것

AVeriTeC는 자유로운 설명문을 원하는 것이 아니라, scorer가 gold QA와 매칭할 수 있는 QA evidence를 원한다.

좋은 QA evidence:

- claim의 정확한 내용을 묻는다.
- 핵심 entity, action, date, number, source, quoted wording을 포함한다.
- retrieved evidence에 근거한 짧고 구체적인 answer를 만든다.
- gold QA와 겹칠 만한 표현을 충분히 포함한다.
- 맥락이 중요한 경우에만 배경 질문을 넣는다.

나쁜 QA evidence:

- broad topic question을 많이 만든다.
- 모든 claim에 generic counterexample question을 추가한다.
- claim과 직접 맞지 않는 context를 answer로 쓴다.
- 관련은 있지만 indirect evidence인 내용을 확정 근거처럼 사용한다.
- QA 수를 늘려 matching 품질을 희석한다.

따라서 실전 목표는 다음 순서가 맞다.

1. QA evidence가 0.25를 넘는 항목을 늘린다.
2. 그 항목들의 label accuracy를 유지한다.
3. rare label을 맞추려고 Supported/Refuted를 대량으로 흔들지 않는다.

## 현재 권장 방향

현재 v4 방향은 유지한다.

- `max_results_per_query=5` 유지
- verifier-intent-aware search ranking 유지
- answer-level source attribution 유지
- aggregate source-quality hint 유지
- source-check English query enforcement 유지
- 검색 query cap은 focused query 3개로 유지
- verifier prompt에 broad CE/NEE 압력은 넣지 않음

다음 개선은 좁고 측정 가능해야 한다.

- 먼저 `QA above threshold + label wrong` 케이스만 본다.
- verifier question generation을 넓게 바꾸지 않는다.
- aggregate의 CE/NEE 판단만 좁게 개선한다.
- CE는 다음 경우에만 사용한다.
  - credible evidence가 실제로 양쪽으로 갈림
  - omitted context가 의미를 바꿈
  - 특정 scope/time/jurisdiction에서는 맞지만 일반 claim으로는 과장됨
- NEE는 다음 경우에만 사용한다.
  - exact claim evidence가 없음
  - 관련 evidence는 있지만 entity/time/scope/number가 정확히 맞지 않음

## 재현 명령

dev_30 예측 실행:

```powershell
.\.venv\Scripts\python.exe scripts\run_averitec_predictions.py --input scripts\averitec_dev_30.json --output scripts\experiments\local_dev_30_v4.json --limit 30
```

dev_30 점수 계산:

```powershell
.\.venv\Scripts\python.exe scripts\score_averitec_meteor.py --gold scripts\averitec_dev_30.json --predictions scripts\experiments\local_dev_30_v4.json
```

holdout_30 예측 실행:

```powershell
.\.venv\Scripts\python.exe scripts\run_averitec_predictions.py --input scripts\averitec_holdout_30.json --output scripts\experiments\local_holdout_30_v4.json --limit 30
```

holdout_30 점수 계산:

```powershell
.\.venv\Scripts\python.exe scripts\score_averitec_meteor.py --gold scripts\averitec_holdout_30.json --predictions scripts\experiments\local_holdout_30_v4.json
```

현재 scorer는 gate breakdown을 출력하므로, 각 실패를 다음처럼 분리해서 볼 수 있다.

- label 문제
- QA 문제
- label과 QA 모두 문제
- 둘 다 통과
