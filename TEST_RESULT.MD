# AVeriTeC Test Result

## 요약

현재 구현은 AVeriTeC 로컬 30개 dev split과 30개 holdout split에서 모두 `meteor @ 0.25` 기준 0.5 이상을 기록했다.

| Dataset | Prediction | QA HU-METEOR | Label Accuracy | Veracity Score (meteor @ 0.25) |
| --- | --- | ---: | ---: | ---: |
| `scripts/averitec_dev_30.json` | `scripts/experiments/local_dev_30_v4.json` | 0.3217 | 0.7000 | 0.5333 |
| `scripts/averitec_holdout_30.json` | `scripts/experiments/local_holdout_30_v4.json` | 0.3406 | 0.6667 | 0.5667 |

AVeriTeC 점수는 최종 라벨이 맞고 QA evidence의 HU-METEOR가 threshold를 넘는 경우에만 올라간다. 현재 개선은 단순 label accuracy보다 evidence retrieval과 QA evidence 품질을 함께 올리는 방향으로 작동했다.

## 실행 결과

### dev_30

명령:

```powershell
.\.venv\Scripts\python.exe scripts\run_averitec_predictions.py --input scripts\averitec_dev_30.json --output scripts\experiments\local_dev_30_v4.json --limit 30
.\.venv\Scripts\python.exe scripts\score_averitec_meteor.py --gold scripts\averitec_dev_30.json --predictions scripts\experiments\local_dev_30_v4.json
```

결과:

- evaluated: 30/30
- question-answer score (HU-METEOR): 0.3217
- label accuracy: 0.7000
- Veracity scores (meteor @ 0.25): 0.5333

Gate breakdown:

- label correct + QA pass: 16 (53.3%)
- label correct + QA fail: 5 (16.7%)
- label wrong + QA pass: 8 (26.7%)
- label wrong + QA fail: 1 (3.3%)

### holdout_30

명령:

```powershell
.\.venv\Scripts\python.exe scripts\run_averitec_predictions.py --input scripts\averitec_holdout_30.json --output scripts\experiments\local_holdout_30_v4.json --limit 30
.\.venv\Scripts\python.exe scripts\score_averitec_meteor.py --gold scripts\averitec_holdout_30.json --predictions scripts\experiments\local_holdout_30_v4.json
```

결과:

- evaluated: 30/30
- question-answer score (HU-METEOR): 0.3406
- label accuracy: 0.6667
- Veracity scores (meteor @ 0.25): 0.5667

Gate breakdown:

- label correct + QA pass: 17 (56.7%)
- label correct + QA fail: 3 (10.0%)
- label wrong + QA pass: 4 (13.3%)
- label wrong + QA fail: 6 (20.0%)

## 주요 변경점

### 검색 결과 수 확대

각 verifier node의 `max_results_per_query` 기본값을 3에서 5로 조정했다.

대상:

- `src/ai_backend/graph/nodes/fact_check.py`
- `src/ai_backend/graph/nodes/source_check.py`
- `src/ai_backend/graph/nodes/numeric_check.py`
- `src/ai_backend/graph/nodes/recency_check.py`

효과:

- 검색 쿼리 수를 넓히지 않고, 기존 focused query에서 더 많은 후보 evidence를 확보한다.
- QA evidence가 gold QA와 매칭될 가능성이 높아졌다.
- dev_30과 holdout_30 모두에서 `meteor @ 0.25`가 0.5 이상으로 상승했다.

### Verifier intent 기반 검색 랭킹

검색 랭킹에 verifier intent를 반영했다.

대상:

- `src/ai_backend/core/search_policy.py`
- `src/ai_backend/core/verification.py`

적용 intent:

- `fact`
- `numeric`
- `source`
- `recency`

효과:

- fact/numeric/recency 검증은 fact-check, refutation, official evidence를 더 선호한다.
- source 검증은 transcript, statement, primary-source-like evidence를 더 선호한다.
- 낮은 품질의 도메인은 final evidence에서 우선순위가 내려간다.

### 답변별 source URL 선택

각 QA answer가 가장 잘 맞는 source URL을 갖도록 `select_answer_source_url(...)`를 추가했다.

대상:

- `src/ai_backend/core/verification.py`
- `src/ai_backend/graph/nodes/fact_check.py`
- `src/ai_backend/graph/nodes/source_check.py`
- `src/ai_backend/graph/nodes/numeric_check.py`
- `src/ai_backend/graph/nodes/recency_check.py`

효과:

- 모든 answer가 동일한 top URL을 공유하던 문제를 줄였다.
- answer text, boolean explanation, question text와 검색 결과 snippet을 비교해 더 적절한 source를 고른다.
- AVeriTeC QA evidence의 일관성이 좋아졌다.

### Source check 영어 검색 쿼리 보정

영어 claim에서는 `source_check`도 영어 검색 쿼리를 생성하도록 공통 language instruction을 적용했다.

대상:

- `src/ai_backend/graph/nodes/source_check.py`

효과:

- quote/source verification에서 원문, 발언, transcript 검색 품질이 안정화됐다.

### Aggregate source quality hint

aggregate 단계에서 source domain과 quality hint를 볼 수 있게 했다.

대상:

- `src/ai_backend/graph/nodes/aggregate.py`
- `src/ai_backend/core/search_policy.py`

힌트:

- `official_domain`
- `primary_source_domain`
- `fact_check_domain`
- `low_quality_domain`

효과:

- final label 판단 시 fact-check evidence, primary source, low-quality repost를 구분하는 데 도움이 된다.
- prediction JSON schema는 유지했다.

### Scorer 진단 출력 추가

`scripts/score_averitec_meteor.py`에 gate breakdown과 confusion matrix를 추가했다.

추가된 진단:

- label correct + QA pass
- label correct + QA fail
- label wrong + QA pass
- label wrong + QA fail
- label confusion matrix
- failure bucket별 claim 목록

효과:

- 점수 하락 원인이 label 문제인지 QA evidence 문제인지 분리해서 볼 수 있다.
- 이후 30개 단위 실험에서 회귀 원인 분석이 쉬워졌다.

## 현재 남은 개선점

현재 `meteor @ 0.25`는 목표였던 0.4~0.5를 넘었지만, rare label은 아직 약하다.

- dev_30 Conflicting Evidence/Cherrypicking F1: 0.0000
- holdout_30 Conflicting Evidence/Cherrypicking F1: 0.0000
- holdout_30 Not Enough Evidence F1: 0.0000

다음 개선은 verifier prompt를 넓게 바꾸기보다 aggregate 판단을 좁게 개선하는 방향이 적절하다.

권장 방향:

- `QA above threshold + label wrong` 케이스를 우선 분석한다.
- CE는 실제로 credible evidence가 갈리거나, omitted context가 의미를 바꾸는 경우에만 사용한다.
- NEE는 exact claim evidence가 없거나 entity/time/scope/number가 맞지 않는 경우에만 사용한다.
- broad counterexample/query prompt를 모든 claim에 추가하지 않는다.

## 보존된 실험 산출물

현재 `scripts/experiments`에는 최종 비교에 필요한 파일만 남겨 두었다.

- `scripts/experiments/local_dev_30_v4.json`
- `scripts/experiments/local_holdout_30_v4.json`
- `scripts/experiments/averitec_improvement_summary.md`

