# AVeriTeC 파이프라인 개선 — 최종 결과 보고서

> 대상: `capstone_ai` AVeriTeC 예측 파이프라인 (`run_averitec_predictions.py`)
> 브랜치: `test_sy` (working tree, 미커밋)
> 주 지표: **AVeriTeC Score @ 0.25** (공식 `eval.py`, dev_gold 첫 30건)
> 종합 출처: `averitec_changes_log.md`(Phase 1, 06-06) + `averitec_changes_log_3.md`(Phase 2, 06-07)
> 대표 결과 산출본: `capstone_ai/scripts/predictions_changes_log_4.json`

---

## 1. 요약 (TL;DR)

라이브 웹검색 기반 AVeriTeC 파이프라인을 **증거(QA) 품질 중심**으로 개선했다.
프롬프트·라우팅·검색 레벨 레버를 모두 적용한 결과, 주 지표 @0.25가
**0.30 → 0.53** 으로 올랐고 연속 지표 Q-A HU-meteor가 **0.280 → 0.340** 으로 단조 상승했다.

| 단계 | N | @0.25 | macro F1 | Q-A HU-meteor | 라벨 acc |
|------|---|-------|----------|---------------|----------|
| Phase 1 시작 baseline (clean v7) | 10 | 0.30 | — | 0.280 | 0.60 |
| Phase 1 종료 (검색·라우팅 강화) | 30 | **0.467** | 0.278 | 0.306 | 0.567 |
| **Phase 2 종료 = 최종 (#1+#2+#B)** | **30** | **0.533** | **0.346** | **0.340** | **0.70** |

> Phase 1 종료의 @0.25 **0.467은 30건 3회(0.467 / 0.533 / 0.500) 중 최악(하단) 샘플**이며 평균은 ~0.50이다.
> 보수적으로 최악값을 표기했다. (단일 측정 노이즈 ±~2건 — §8)

- 개선의 형태는 "최고치 갱신"이 아니라 **밴드 상단 고정 + 변동성 감소**.
- 프롬프트/모델 레벨 레버는 #B에서 사실상 소진. **~0.53은 라이브 웹검색 셋업의 구조적 상한**으로 판단되며,
  그 위를 넘으려면 검색 대상을 AVeriTeC knowledge store로 바꾸는 아키텍처 교체가 필요하다(§7).

---

## 2. 최종 결과 (대표값, 공식 eval.py)

> 대표 산출본 = `predictions_changes_log_4.json` (#1+#2+#B 적용 최종 코드의 한 run, N=30).
> @0.25·Q-A는 재현 run(`predictions.json`)과 동일(0.533 / 0.340) — §8 노이즈 참조.

**증거(QA) 점수 (HU-meteor)**
- Question-only score: **0.418**
- Question-Answer score: **0.340**

**Veracity F1 (라벨 분류)**

| 라벨 | F1 | support(N=30) |
|------|----|---------------|
| Supported | 0.545 | 4 |
| Refuted | 0.837 | 20 |
| Not Enough Evidence | **0.000** | 3 |
| Conflicting Evidence/Cherrypicking | **0.000** | 3 |
| **macro F1** | **0.346** | |
| **accuracy** | **0.700 (21/30)** | |

**AVeriTeC Score (게이트 레벨별, 증거가 통과 AND 라벨 정답인 비율)**

| 게이트 | @0.1 | @0.2 | **@0.25** | @0.3 | @0.4 | @0.5 |
|--------|------|------|-----------|------|------|------|
| Veracity | 0.700 | 0.667 | **0.533** | 0.400 | 0.100 | 0.067 |

핵심: **주 지표 @0.25 = 0.533**. 단, **NEE·CE F1 = 0.0** — 희귀 라벨은 끝까지 못 살림(§6, §7).

---

## 3. 출발점 대비 발전 과정

| 시점 | 구성 | @0.25 | macro F1 | Q-A | 비고 |
|------|------|-------|----------|-----|------|
| 최초 clone | 원본 코드 | — | 0.314 | — | acc 0.70 (N=10), CE·NEE=0 |
| Phase 1 시작 | clean v7 baseline | 0.30 | — | 0.280 | `.env` 버그로 실행 자체 불가했던 상태 수정 후 |
| Phase 1 종료 | +라우팅 +검색강화 | **0.467** (밴드 0.467~0.533, 평균 ~0.50) | 0.278 | 0.306 | max_results 3→5가 최대 기여(+0.16~0.20) |
| **Phase 2 종료** | **+#1 +#2 +#B** | **0.533** | **0.346** | **0.340** | 최종. Q-A 단조 상승이 가장 깨끗한 개선 증거 |

> 두 측정 축을 섞지 말 것: **Veracity F1**(라벨 일치만, 채점기 불필요)과
> **AVeriTeC @0.25**(증거가 HU-meteor 0.25 게이트 통과 AND 라벨 정답)는 다른 지표다.

---

## 4. 적용·유지 중인 변경

### Phase 1 — 실행 정상화 + 검색/라우팅 강화 (averitec_changes_log.md)

1. **`.env` 경로 버그 수정** — `config.py`의 `_ENV_FILE` 경로가 `capstone_ai/src/.env`를 가리켜
   `OPENAI_API_KEY` 미로드 → fact_check 즉시 사망. `.parent` 한 단계 추가로 수정. (선행 차단 요인)
2. **source_check 언어 일치** — fact/numeric/recency는 `lang_instruction`을 적용하는데 source_check만 누락 →
   영어 claim에 한국어 질문 생성, METEOR 손실. 프롬프트에 `lang_instruction(claim)` 추가.
3. **Event/Property → FACT + SOURCE 라우팅** — provenance("어디서 published됐나") 앵글 채점 대응.
   `preprocess.py` `_AVERITEC_TYPE_MAP`에서 `["FACT"]` → `["FACT","SOURCE"]`. (Causal/Numerical 미변경)
4. **검색 강화 `max_results_per_query` 3 → 5** — Tavily 요청 수 불변(쿼리당 결과만 증가)이라
   거의 무비용으로 증거 다양성↑. **@0.25 0.30→0.50의 주 기여.**

### Phase 2 — 증거(QA) 품질 레버 (averitec_changes_log_3.md)

- **#1. Verifier-intent 기반 검색 랭킹** — 랭킹에 의도(fact/numeric/source/recency)를 전달해
  의도별 선호 증거를 가점(source→primary source, fact/numeric/recency→fact-check 우대).
  facebook/reddit을 low-quality 도메인으로. *파일:* `core/search_policy.py`, `core/verification.py` + 4노드.
- **#2. 답변별 source URL 매칭** (`select_answer_source_url`) — 기존엔 모든 답변이 top 결과 URL을
  공유 → 답변–URL 불일치. 답변 텍스트와 검색 결과의 토큰 F1로 best URL 선택. `Unanswerable`→`url=""` 버그픽스.
- **#B. gold 스타일 fact 질문 프롬프트 (test 전용)** — gold 질문(구체 entity/date/source factoid +
  provenance) 스타일로 fact_check 질문 생성. `state.averitec_mode` 플래그(run_averitec_predictions만 True,
  **production 무변경**)로 `FACT_QUESTION_USER_AVERITEC` 선택, fact_check 노드에만 적용.
  *검증:* @0.25 0.533/0.533(구 0.500), Q-A 0.313→0.340, 2/2 일관.

---

## 5. 기각·원복된 시도 (현재 코드에 없음 — 재시도 말 것)

- **답변별 stance 태깅 + CE 가드:** gold 라벨별 stance가 CE→supports 81%·NEE→supports 42%로
  도입 목적이던 CE/NEE에서 무의미, stance-only 예측 0.60 < 다수클래스 0.67. 데이터 검정 후 원복.
- **답변 다양화(질문별 distinct answer):** unique-ratio vs evidence 상관 0.022(무효).
- **질문 순서/10개 cap 조정:** cap=10 / 전체 / 오라클재배치 세 값 동일 → 헤드룸 0.
- **aggregate 프롬프트 라벨 규칙 강화:** 격리 @0.25 −1, 지배 라벨 Refuted 파괴 → 원복.
- **gpt-4o aggregation:** CE 과예측 역효과(맞는 Refuted/Supported를 깸). `llm_model_aggregation`은 gpt-4o-mini 유지.

> 교훈: **라벨은 프롬프트·stance·aggregation 모델 교체로 안 움직인다.** 라벨 정확도는 결국 증거 품질로 귀결.

---

## 6. 진단으로 확인한 사실

- **질문 과소생성 아님:** claim당 평균 9.4개(min 7, max 13) 생성. 답변 드롭은 병목 아님.
- **손실 구조 = 증거 ≈ 라벨 (대등):** baseline 10건에서 게이트 손실 3건 ≈ 라벨 손실 3건.
- **증거 손실 원인 = 질문 "내용" 불일치(포맷 아님):** 답변은 이미 87% Extractive. gold는 출처·절차를 묻는데
  우리는 주장 표면을 물었음 → 포맷 튜닝은 헤드룸 없음, 질문 content를 gold화하는 것만 유효.
- **NEE·CE F1 = 0.0:** 전 구간 못 살림. 희귀 라벨이 구조적 천장.

---

## 7. 한계 및 향후 방향 — retrieval 아키텍처 수술

> 프롬프트/모델 레벨 레버는 #B가 한계선. ~0.53은 라이브 웹검색 셋업의 구조적 평탄지대.

1. **남은 레버는 한 뿌리(증거 품질)로 수렴** — evidence-conditioned 질문 생성(증거 속 엔티티를 질문으로),
   retrieval 커버리지, 라벨 정확도가 전부 "우리 증거가 gold와 안 맞는다"는 하나의 병목.
2. **구조적 천장 = 소스 풀 불일치** — 우리는 라이브 웹검색(Tavily/OpenAI), gold QA는 AVeriTeC 고정
   knowledge store + 인간 작성. HU-meteor는 gold 특정 증거 문장과의 어휘 겹침을 보므로, 내용이 맞아도
   소스·표현이 다르면 겹침이 구조적으로 제한됨(idx 4/8/16: gold 엔티티 Chemed/Vitas 등이 우리 증거에 부재).
   오라클 상한(0.521)과 실현 이득(+0.016)의 큰 격차도 같은 원인.
3. **천장을 깨는 길 = store 기반 retrieval(BM25/hybrid)** — gold QA가 그 store에서 나오므로 같은 풀에서
   검색하면 어휘 겹침이 근본적으로 상승. 단 라이브 웹검색 → store retrieval은 검색 클라이언트·증거 수집
   경로 전반을 바꾸는 **아키텍처 교체급 작업**.

---

## 8. 재현 방법 & 측정 주의

```bash
# 1) 예측 생성 (N=30)
cd /Users/siyoung/capstone/capstone_ai
rm -f scripts/predictions.json          # ⚠️ 이어쓰기 방지(안 지우면 전부 skip)
bash scripts/run_test.sh                # START=0, LIMIT=30

# 2) 공식 채점
cd /Users/siyoung/capstone
PY=capstone_ai/.venv/bin/python
$PY -c "import json;g=json.load(open('capstone_ai/scripts/averitec_dev_gold.json'));json.dump(g[:30],open('/tmp/gold30.json','w'),ensure_ascii=False)"
$PY adapter.py --system_output capstone_ai/scripts/predictions.json --gold /tmp/gold30.json --out /tmp/pred30_eval.json
$PY eval.py --predictions /tmp/pred30_eval.json --references /tmp/gold30.json
# 주 지표: "Veracity scores (meteor @ 0.25)"
```

- `eval.py` · `adapter.py` · `utils.py` · `run_eval.sh`는 리포 루트(`/Users/siyoung/capstone/`)에 위치.
- ⚠️ **부분 실행(N건) 시 gold도 반드시 N건으로 자를 것** — 안 그러면 나머지가 NEE로 패딩돼 가짜 저점(@0.25=0.034 류).
- **LLM 비결정성 노이즈:** 같은 코드 30건 3회 = 0.467 / 0.533 / 0.500 (평균 ~0.50, ±~2건). 게이트 통과는 21~22로 안정.
  회차마다 라벨이 뒤집히는 idx가 있음(예: idx 5/13/17). 대표값 log_4와 재현본 predictions.json도
  텍스트는 거의 전부 다르지만(완전동일 0/30) @0.25·Q-A는 동일 → **둘은 동일 설정의 리런, 우열 없음.**
- **효과 인정 기준:** 단일 측정 +1~2는 노이즈와 구분 불가. **2~3회 평균이 노이즈 밴드를 넘을 때만 채택.**

---

## 9. 비용 메모

- `max_results=5`: Tavily 요청 수 불변, LLM 입력 토큰만 소폭 증가 → 거의 무비용.
- EP 라우팅(FACT+SOURCE): 해당 claim 노드 비용 ~2배(검색·LLM 한 세트 추가).
- 증거 풀 확대는 답변 호출 7~13회에 곱해지므로, 추후 모델 업그레이드 시 입력 토큰 비용 동반 상승.
