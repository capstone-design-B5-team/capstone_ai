# AVeriTeC Test Result

---

## 최종 성능 (v36)

| 항목 | 값 |
| --- | ---: |
| 평가 건수 | 30/30 |
| QA HU-METEOR | 0.3288 |
| Label Accuracy | 0.7000 |
| **Meteor @ 0.25** | **0.5000** |


Veracity F1 scores:
 * Supported:                       0.6154
 * Refuted:                         0.8421
 * Not Enough Evidence:             0.0000
 * Conflicting Evidence/Cherry:     0.2857
 * macro F1:                        0.4358
 * accuracy:                        0.7000  (21/30 정답)
 * Justification score (meteor):       0.3288

AVeriTeC score (meteor @ 0.25):     0.5000

---

## 점수 계산 방식

AVeriTeC 공식 평가 기준을 따른다.

1. 예측 결과에서 질문+답변 쌍(QA pair)을 최대 10개 추출한다.
2. 각 QA pair를 `"{질문} {답변}"` 형태의 문자열로 만든다.
3. Gold QA pair들과 Hungarian matching으로 최적 매칭을 찾아 HU-METEOR를 계산한다.
4. HU-METEOR > 0.25이고 label이 gold와 일치하면 그 claim은 **pass**다.
5. 최종 점수 = pass 건수 / 전체 건수

즉, label이 맞아도 QA 어휘 겹침이 0.25 미만이면 0점, QA가 아무리 좋아도 label이 틀리면 0점이다.


---

## 파이프라인 구조

```
AVeriTeC JSON (claim, claim_date, claim_types)
  └── run_averitec_predictions.py
        └── verification_graph.ainvoke(state)
              preprocess          # claim 추출, 유형 분류
              ├── fact_check      # 사건·인과 검증
              ├── source_check    # 출처·인용 검증
              ├── recency_check   # 시점·최신성 검증
              └── numeric_check   # 수치·비교 검증
                    └── evidence_calibration   # answer별 support metadata 정규화
                          └── aggregate        # LLM이 label 최종 판정
        └── scoring_ranker        # scorer 친화적 QA 순서 정렬
        └── prediction JSON
  └── score_averitec_meteor.py
        └── HU-METEOR + gate breakdown 리포트
```

각 verifier 노드는 다음 순서로 동작한다:

1. LLM이 claim을 분석해 검증 질문 5~7개와 검색 쿼리를 생성한다.
2. 검색 API로 근거 문서를 수집하고 verifier intent에 맞게 rerank한다.
3. LLM이 질문별로 답변을 생성한다 (Extractive 최우선, Boolean, Abstractive 순).
4. `evidence_calibration`이 각 답변에 `support_type`, `directness`, `mismatch_type`을 부여한다.
5. `aggregate`가 calibrated QA 전체를 보고 AVeriTeC label을 판정한다.
6. `scoring_ranker`가 claim-token overlap, support_type, answer_type 기준으로 QA를 정렬해 scorer에 유리한 순서로 출력한다.

---

## 주요 구현 내용

### 1. Evidence Calibration

파일: `src/ai_backend/graph/nodes/evidence_calibration.py`, `src/ai_backend/core/verification.py`

각 verifier가 생성한 answer에 구조적 메타데이터를 붙인다.

| 필드 | 주요 값 | 의미 |
| --- | --- | --- |
| `support_type` | `direct_support`, `partial_support`, `contradiction`, `insufficient_evidence`, `related_only` | answer가 claim을 어떻게 지지/반박하는지 |
| `directness` | `direct`, `indirect` | evidence가 직접적인지 간접적인지 |
| `mismatch_type` | `none`, `scope`, `time`, `number`, `attribution`, `context`, `methodology` | claim과 evidence가 어긋나는 지점 |

LLM이 명시적으로 출력한 값을 우선 사용하고, 없으면 answer 텍스트에서 키워드 기반으로 추론한다. 이 메타데이터는 aggregate LLM 프롬프트와 label calibration에서 모두 활용된다.

### 2. Verifier QA 생성

파일: `src/ai_backend/graph/nodes/fact_check.py` 외 3개 verifier

AVeriTeC scorer는 prediction QA와 gold QA의 어휘 겹침을 측정하므로, 질문이 claim의 핵심 atom을 직접 물어야 한다.

우선적으로 커버하는 atom:
- entity, action, date/time, location
- number/unit/comparison
- source/speaker, quote/transcript
- correction/denial/true alternative fact

LLM이 생성한 질문이 너무 일반적이면 rule-based seed question으로 보강하고, context/background/alternative-view 질문은 뒤로 배치한다.

### 3. Aggregate Label 판정 및 Calibration

파일: `src/ai_backend/graph/nodes/aggregate.py`

LLM이 calibrated QA 전체와 verifier summary를 보고 AVeriTeC label을 판정한다. LLM 판정 후 일반적인 AVeriTeC 규칙에 맞는 보정을 추가로 적용한다.

유지하는 보정 방향:

| Pattern | 보정 방향 |
| --- | --- |
| numeric claim에서 근거 기준값(denominator, comparison basis)이 없음 | Supported → NEE |
| numeric/comparative claim에서 partial support + 방법론 불일치 | Supported → CE |
| treatment/science claim에서 evidence가 inconclusive 수준 | Refuted → NEE |
| 직접 인용 claim인데 제3자 설명만 있음 | Supported → CE |
| accusation claim에서 evidence가 groundless/false로 명시 | Supported → Refuted |
| Refuted인데 direct support만 있고 contradiction·refutation 신호 없음 | Refuted → Supported |


### 4. 검색 및 Evidence Ranking

파일: `src/ai_backend/core/search_policy.py`, `src/ai_backend/core/verification.py`

verifier별 intent profile을 기반으로 검색 결과를 rerank한다.
- `fact`, `numeric`, `recency`: fact-check, refutation, official evidence 우선
- `source`: transcript, original statement, primary source 우선

query는 verifier별 최대 3개, `max_results_per_query`는 5로 운용한다. URL/content 기준으로 중복 제거한다.

### 5. Answer Compact

파일: `src/ai_backend/core/verification.py`

HU-METEOR는 unigram 기반이므로 답변이 너무 길면 precision이 낮아진다. 반대로 너무 짧으면 gold QA에 필요한 token이 빠진다.

답변 생성 후 claim/question과 관련성 높은 문장을 중심으로 줄인다.
- `compact_averitec_answer`: 최대 **70단어** (기존 55에서 완화)
- `compact_boolean_explanation`: 최대 **55단어** (기존 45에서 완화)

### 6. Scorer-Facing QA Ranker

파일: `src/ai_backend/graph/nodes/scoring_ranker.py`

AVeriTeC scorer는 prediction의 앞쪽 QA 최대 10개만 평가한다. 좋은 QA가 뒤에 있으면 점수에 반영되지 않으므로, 저장 직전에 정렬한다.

정렬 기준:
- claim-token overlap이 높은 QA 우선
- label에 맞는 support_type 가중
- Extractive > Boolean > Abstractive > Unanswerable 순
- generic context/background/alternative-view 질문은 뒤로

기존 앞 10개에 high-overlap direct QA가 충분하면 원래 순서를 보존한다.

variant question(랭커가 추가하는 보조 질문)은 **55자 이내**의 짧은 형태로 생성한다. 긴 claim 전체를 질문에 넣으면 candidate string이 길어져 METEOR precision이 떨어진다.

### 7. Scorer Diagnostics

파일: `scripts/score_averitec_meteor.py`

점수 하락 원인을 label 문제와 QA 문제로 분리하기 위해 상세 리포트를 출력한다.

출력 항목:
- QA HU-METEOR, label accuracy, threshold별 AVeriTeC score
- gate breakdown (label × QA 2×2 matrix)
- confusion matrix
- failure bucket별 claim 목록

---


## 현재 한계 및 향후 방향

**근본적 한계:**
- QA METEOR 병목의 핵심은 검색 소스 불일치다. Gold annotator가 사용한 소스를 우리 검색이 찾지 못하면 어휘가 달라져 METEOR가 낮아진다. 코드 개선만으로 해결하기 어렵다.
- Label 오류의 나머지는 CE/NEE/Supported 경계 판단으로, 일반 규칙으로 정의하기 어렵다. 샘플을 타겟해 규칙을 추가하면 과적합이 된다.

**추가로 시도해볼 수 있는 방향:**
- `scripts/rewrite_averitec_scoring_qa.py`: 이미 구현된 포스트프로세싱 단계. 기존 QA를 LLM이 더 짧고 atomic하게 재작성한다. gold를 보지 않으므로 과적합 위험이 낮다.
