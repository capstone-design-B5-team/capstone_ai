# B5tween — AI 팀 프로젝트 자료 검증 시스템

## 최종 발표 정리

---

## 목차

1. [프로젝트 개요](#1-프로젝트-개요)
2. [시스템 아키텍처](#2-시스템-아키텍처)
3. [AI 검증 파이프라인 설계](#3-ai-검증-파이프라인-설계)
4. [5가지 Claim 유형 처리](#4-5가지-claim-유형-처리)
5. [검증 노드 상세 설계](#5-검증-노드-상세-설계)
6. [AVeriTeC 평가 연동](#6-averitec-평가-연동)
7. [주요 설계 결정](#7-주요-설계-결정)
8. [테스트 결과](#8-테스트-결과)
9. [한계 및 향후 과제](#9-한계-및-향후-과제)

---

## 1. 프로젝트 개요

### 배경 및 문제 정의

팀 프로젝트에서 팀원이 제출한 자료(보고서, 발표 슬라이드 등)는 사실 오류, 수치 과장, 오래된 정보, 출처 불명 내용을 포함할 수 있다. 이를 팀원이 일일이 검증하는 것은 시간과 전문성이 필요하고, 결국 대부분 생략된다.

### 목표

> **팀원이 자료를 제출하면 AI가 자동으로 사실관계·수치·출처·최신성을 검증하고, 팀원들이 함께 리뷰·투표하여 자료의 신뢰성을 확정하는 서비스**

### 핵심 기능

| 기능              | 설명                                                                       |
| ----------------- | -------------------------------------------------------------------------- |
| **AI 자동 검증**  | 제출 자료에서 검증 가능한 주장(Claim)을 추출하고 4개 전문 노드로 병렬 검증 |
| **팀원 리뷰**     | AI 검증 결과를 기반으로 팀원이 드래그 리뷰·투표 진행                       |
| **자료 보관함**   | 검증 완료·거절·리뷰 중 상태로 자료 관리                                    |
| **AVeriTeC 호환** | 국제 팩트체킹 벤치마크(AVeriTeC)와 동일한 QA 근거 형식 출력                |

---

## 2. 시스템 아키텍처

### 전체 구성

```
┌─────────────────────────────────────────────────────┐
│                  Django 프론트엔드                    │
│  (Python 3.13 / Django 6.0.4 / SQLite)               │
│                                                      │
│  자료 검증 탭  │  자료 보관함  │  팀 관리  │  내보내기  │
└──────────────────────┬──────────────────────────────┘
                       │  POST /verify
                       │  (project_file_id, text, topic)
                       ▼
┌─────────────────────────────────────────────────────┐
│              FastAPI AI 백엔드 (port 8001)            │
│  (Python 3.13 / FastAPI / LangGraph)                 │
│                                                      │
│  POST /verify         → 202 Accepted (비동기 접수)    │
│  GET  /verify/{id}/status  → accepted/processing/... │
│  GET  /verify/{id}/result  → VerifyResponse          │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│              LangGraph 검증 파이프라인                 │
│                                                      │
│  preprocess → [fact / numeric / source / recency]   │
│            병렬 실행          │                       │
│                               ▼                      │
│                          aggregate                   │
└─────────────────────────────────────────────────────┘
                       │
                       ▼
          Tavily 웹 검색 API  +  OpenAI LLM
```

### 기술 스택

| 계층       | 기술                                     |
| ---------- | ---------------------------------------- |
| 프론트엔드 | Django 6.0.4, Vanilla JS, SQLite         |
| AI 백엔드  | FastAPI, LangGraph, Python 3.13          |
| LLM        | OpenAI GPT-4o (검증 판정)                |
| 웹 검색    | Tavily Search API (증거 수집)            |
| 병렬 처리  | LangGraph 노드 병렬 + ThreadPoolExecutor |

---

## 3. AI 검증 파이프라인 설계

### 전체 흐름

```
원문 텍스트 (raw_text)
        │
        ▼
   [preprocess_node]
   - LLM으로 검증 가능한 sub-claim 추출
   - 각 claim에 타입 부여: FACT / NUMERIC / SOURCE / RECENCY
   - AVeriTeC 입력이면 claim_types 매핑으로 LLM 추출 생략
        │
        ├─────────────┬─────────────┬─────────────┐
        ▼             ▼             ▼             ▼
 [fact_check]  [numeric_check] [source_check] [recency_check]
  FACT claim    NUMERIC claim   SOURCE claim   RECENCY claim
  3단계 흐름     3단계 흐름       3단계 흐름      3단계 흐름
  (5~7 Q)       (4~6 Q)         (4~6 Q)        (3~5 Q)
        │             │             │             │
        └─────────────┴─────────────┴─────────────┘
                                   │
                                   ▼
                           [aggregate_node]
                           - claim별로 questions 필터 (claim_id 매칭)
                           - claim마다 LLM 호출 → per-claim label + justification 생성

[각 검증 노드 공통 3단계]
  Step 1: LLM → 유형 분류 + N개 검증 질문 + 질문별 search_queries 생성
  Step 2: 전체 search_queries 중복 제거 후 최대 3개로 제한 → Tavily 1회 검색 (공유)
  Step 3: LLM × N → 질문별 answer 생성 (Extractive 최우선)
  ※ 모든 Question에 claim_id를 부여하여 aggregate에서 claim별 필터링 가능
```

### GraphState 핵심 구조

| 필드 | 타입 | 설명 |
|---|---|---|
| `claims` | `list[Claim]` | preprocess가 추출한 sub-claim (id, text, type, context) |
| `questions` | `list[Question]` | 각 노드가 생성한 검증 Q&A (claim_id로 소속 추적) |
| `*_results` | `list[VerificationResult]` | fact/numeric/source/recency 노드별 검색 메타데이터 |
| `claim_labels` | `list[ClaimLabel]` | aggregate가 생성한 claim별 label + justification |

### 병렬 처리 구조

```
preprocess (순차)
    │
    ├─ fact_check_node     ┐
    ├─ numeric_check_node  ├── LangGraph 레벨 병렬
    ├─ source_check_node   │
    └─ recency_check_node  ┘
                │
                │ 각 노드 내부: ThreadPoolExecutor
                │ (claim 최대 4개 동시 처리)
                ▼
         aggregate (순차)
```

---

## 4. 5가지 Claim 유형 처리

AVeriTeC 데이터셋의 5가지 claim_types를 내부 ClaimType으로 매핑하여 처리한다.

| AVeriTeC `claim_types`   | 내부 `ClaimType`   | 검증 노드                            | 노드 내 분류              |
| ------------------------ | ------------------ | ------------------------------------ | ------------------------- |
| **Event/Property Claim** | `FACT`             | fact_check_node                      | EPC (사건·제도·발생 여부) |
| **Causal Claim**         | `FACT`             | fact_check_node                      | EPC (인과관계 포함)       |
| **Numerical Claim**      | `FACT` + `NUMERIC` | fact_check_node + numeric_check_node | EPC + Numerical           |
| **Position Statement**   | `SOURCE`           | source_check_node                    | PS (출처 지지 여부)       |
| **Quote Verification**   | `SOURCE`           | source_check_node                    | QV (인용구 정확성)        |

### 설계 원칙

- `builder.py`가 모든 claim을 4개 노드에 **동시 전달**
- 각 노드가 `claim["type"]`으로 내부 필터링
- Numerical Claim은 FACT+NUMERIC 두 노드에서 독립 검증 후 aggregate에서 병합

---

## 5. 검증 노드 상세 설계

### 공통 처리 패턴 (3단계)

4개 검증 노드는 모두 동일한 3단계 구조로 동작한다.

```
Step 1 [질문 생성]: LLM → N개 검증 질문 + 질문별 search_queries 생성
Step 2 [증거 수집]: 전체 search_queries 중복 제거 후 최대 3개 제한 → Tavily 1회 검색 (questions 공유)
Step 3 [답변 생성]: 공유 evidence pool에서 질문별 LLM answer 호출 (N회)
※ 모든 Question에 claim_id 부여 → aggregate에서 claim별 필터링
```

- **비용 절감**: 질문이 5~7개여도 검색은 항상 1회, 쿼리 수 최대 3개로 제한
- **answer_type 우선순위 (전 노드 공통)**: Extractive > Boolean > Abstractive > Unanswerable
    - Extractive: AVeriTeC gold와 METEOR 매칭에 가장 유리
    - Abstractive: 직접 인용 불가 시만 허용 (numeric은 완전 불허)
- **병렬 처리**: 각 노드 내 `ThreadPoolExecutor(max_workers=4)` — claim 최대 4개 동시 처리

---

### 5.1 사실관계 노드 (fact_check_node)

**검증 대상**: `Event/Property Claim`, `Causal Claim` → 내부 타입 `FACT`

사건의 발생·존재 여부, 제도·법령 시행 여부, 인과관계, 개념 정의를 검증한다.
수치를 제거해도 핵심 검증이 가능한 claim이 대상이다.

**내부 분류** (질문 생성 시 참조, Q-슬롯 구성에 영향):

- `EPC` (Empirical/Process/Causal): 역사적 사실, 제도·법령, 인과관계
- `CC` (Conceptual/Categorical): 정의·개념, 분류

**처리 흐름**:

```
claim
  │
  ▼ Step 1: _request_fact_questions(claim, llm, claim_date)
         프롬프트: FACT_CHECK_SYSTEM + FACT_QUESTION_USER + lang_instruction(claim)
         EPC|CC 분류 후 5~7개 검증 질문 + 질문별 search_queries 생성
           Q1 — 핵심 사실 (사건 발생·존재 여부)
           Q2 — 구체적 시점·주체·조건 확인
           Q3 — 인과·맥락 확인
           Q4 — 배경·전제 확인
           Q5 — 반례·예외 확인
           Q6 — claim_date 시점 유효성 (해당 시점에도 사실이 유효했나?)
           Q7 — 대안적 관점·반론 (선택)
         * claim_date 연도를 search_queries에 포함하도록 프롬프트에서 강제
         │
         ▼ Step 2: search_verification_evidence(claim, all_queries[:3])
           SearchProfile 기반 검색 결과 재정렬 (공식 도메인 우선)
           저품질 도메인(블로그·위키·유튜브) 자동 하위 순위
           ※ 쿼리 수 최대 3개 제한으로 Tavily 호출 비용 절감
         │
         └─ Step 3: _request_fact_answer(claim, question, evidence, claim_type) × N
             프롬프트: FACT_CHECK_SYSTEM + FACT_ANSWER_USER
             answer_type (우선순위 순):
               Extractive  — evidence 원문 직접 인용 (최우선)
               Boolean     — Yes/No 명확히 판단 가능 시 + boolean_explanation 필수
               Abstractive — 직접 인용 불가 시만 허용
               Unanswerable — evidence 없거나 불충분
```

**검증 전략 포인트**:

- Q5 반례·Q6 시점 유효성·Q7 반론까지 포함 → 단방향 확증 편향 방지
- CC 유형은 개념 정의 질문 형태 반드시 포함
- claim_date 연도가 쿼리에 들어가면 시점이 맞는 evidence 확보에 유리

---

### 5.2 수치 검증 노드 (numeric_check_node)

**검증 대상**: `Numerical Claim` → 내부 타입 `NUMERIC`

수치(숫자·비율·통계·금액)의 정확성을 검증한다. 수치 자체가 주장의 핵심인 경우만 해당한다.
단순 수치 언급(예: "5개국이 참여했다")은 FACT 노드 담당.

`Numerical Claim`은 FACT + NUMERIC 두 노드에서 **동시** 검증되며, aggregate에서 Q&A가 합산된다.

**처리 흐름**:

```
claim
  │
  ▼ Step 1: _request_numeric_questions(claim, llm, claim_date)
         프롬프트: NUMERIC_CHECK_SYSTEM + NUMERIC_QUESTION_USER + lang_instruction(claim)
         4~6개 검증 질문 + 질문별 search_queries 생성
           Q1 — 핵심 수치 검증 ("주체의 연도 지표는 얼마였나요?")
           Q2 — 비교 대상 또는 맥락 질문
           Q3 — 단위·기준·출처 확인
           Q4 — 시점·맥락 추가 확인
           Q5 — 공식 출처·발표 날짜 확인 (선택)
           Q6 — 인접 연도와의 비교 (선택)
         * claim_date 연도를 search_queries에 포함하도록 프롬프트에서 강제
         │
         ▼ Step 2: search_verification_evidence(claim, all_queries[:3])
           SearchProfile 기반 결과 재정렬 (공식 통계 DB 우선)
         │
         └─ Step 3: _request_numeric_answer(claim, question, evidence) × N
             프롬프트: NUMERIC_CHECK_SYSTEM + NUMERIC_ANSWER_USER
             answer_type:
               Extractive  — 원문 수치 그대로 인용, 계산 결과도 수식 포함 (최우선)
               Boolean     — 수치 비교가 Yes/No로 판단 가능 시 + 수치 근거 명시
               Unanswerable — evidence에서 수치 없음
             ※ Abstractive 사용 불허 — 수치는 반드시 원문 인용
```

**검증 전략 포인트**:

- Q6 인접 연도 비교로 수치의 시계열 맥락 확보
- Abstractive 완전 차단 — 수치 답변은 원문 인용 또는 수식 포함 계산 결과만 허용
- Q2가 비교 대상 수치를 별도 질문으로 다루므로, Comparative claim 자동 대응

---

### 5.3 출처 검증 노드 (source_check_node)

**검증 대상**: `Position Statement`, `Quote Verification` → 내부 타입 `SOURCE`

명시된 출처(발언·인용·보고서)의 내용 일치 여부를 검증한다.

**Citation 유형**:

- `PS (Position Statement)`: 출처 내용을 요약·의역하여 주장의 근거로 사용 → 의미상 지지 여부 판단
- `QV (Quote Verification)`: 따옴표로 직접 인용 → 인용구 실제 존재 여부 확인

**처리 흐름**:

```
claim + state.document_citations
  │
  ▼ _claim_sources(claim, state)
    claim.citations + state.document_citations 합산 → key 중복 제거
    citation_text = 첫 번째 citation.raw_text
    * citations 없으면 claim["text"]를 citation_text로 대체
  │
  ▼ Step 1: _request_source_questions(claim, citation_text, llm, claim_date)
    프롬프트: SOURCE_CHECK_SYSTEM + SOURCE_QUESTION_USER
    ※ lang_instruction 미적용 (citation 언어 ≠ claim 언어일 수 있음)
    PS|QV 분류 + 4~6개 검증 질문 + 질문별 search_queries 생성
      Q1 — 핵심 확인
           PS: "화자/기관이 [핵심 주장]을 밝혔나요?"
           QV: "화자가 실제로 '[인용구]'라고 말했나요?"
      Q2 — 발언·보고의 맥락·배경 확인
      Q3 — 공식 발표·보고 여부 확인
      Q4 — claim_date 시점 유효성 ("이 발언은 claim_date에 유효했나요?")
      Q5 — 다른 기관·전문가 입장 (선택)
      Q6 — 공식 반박·수정 발표 여부 (선택)
    PS 쿼리: Claim 핵심 주장 검증 쿼리 2~3개
    QV 쿼리: "화자명 인용구 키워드", "기관명 해당 발언 원문" 형태
  │
  ▼ Step 2: search_verification_evidence(claim, all_queries)
  │
  └─ Step 3: _request_source_answer(claim, question, evidence, citation_type) × N
      프롬프트: SOURCE_CHECK_SYSTEM + SOURCE_ANSWER_USER
      PS: 출처가 의미상 지지하는지 종합 판단, 직접 인용 가능 시 Extractive 우선
      QV: 인용구를 evidence에서 직접 찾아 Extractive 답변
      answer_type (우선순위 순):
        Extractive  — PS/QV 공통 최우선
        Boolean     — 존재 여부 Yes/No 판단 가능 시
        Abstractive — 직접 인용 불가 시만
        Unanswerable — evidence 없음
```

**검증 전략 포인트**:

- QV는 인용구 존재 확인이 목적이므로 Extractive가 자연스럽게 최우선
- Q4 claim_date 유효성으로 "당시 유효한 발언이었는지" 검증
- Q6 공식 반박 질문으로 나중에 번복된 발언 탐지

---

### 5.4 최신성 검증 노드 (recency_check_node)

**검증 대상**: 과거 수치가 현재 맥락의 근거로 오용(cherry-picking)되는 경우

> `numeric_check`와의 역할 구분:
>
> - numeric_check: "2019년에 그 수치가 정확했나?" → **과거 사실 정확성**
> - recency_check: "그 과거 수치를 지금 근거로 써도 되나?" → **현재 오용 탐지**

**트리거 조건** (두 조건 모두 충족 시 5개 질문, 미충족 시 3개):

1. 과거 시점 명시: "2019년 기준", "당시", "△년 전" 등
2. 현재·지속 함의: "현재도", "여전히", "OECD 최고 수준", "만성적", "고착화" 등

**처리 흐름**:

```
claim
  │
  ▼ Step 1: _request_recency_questions(claim, llm, claim_date)
         프롬프트: RECENCY_CHECK_SYSTEM + RECENCY_QUESTION_USER + lang_instruction(claim)
         출력: {
           "time_indicators": ["2019년", "기준"],
           "cherry_pick_direction": "과장|축소|해당없음",
           "questions": [...]
         }
         생성 질문:
           [트리거 충족 — 5개]
             Q1 — 과거 수치 검증 (주장 연도 포함 필수)
             Q2 — 현재(2024~2026) 최신 수치 조회
             Q3 — 현재 맥락 유효성 ("과거 수치가 현재를 대표할 수 있나?")
             Q4 — claim_date 당시 수치 (claim_date 연도 포함)
             Q5 — 최근 3년 추세 파악 (선택)
           [트리거 미충족 — 3개]
             Q1 ~ Q3만 생성
         │
         ▼ Step 2: search_verification_evidence(claim, all_queries, days=730)
           * recent_days=730: Tavily 최근 2년 이내 문서 우선 필터
         │
         └─ Step 3: _request_recency_answer(claim, question, evidence) × N
             프롬프트: RECENCY_CHECK_SYSTEM + RECENCY_ANSWER_USER
             answer_type (우선순위 순):
               Extractive  — 수치·날짜 직접 인용 (최우선)
               Boolean     — 현재 유효성이 Yes/No로 판단 가능 시
               Abstractive — 추세·맥락 서술 필요 시
               Unanswerable — evidence 없음
         cherry_pick_direction 메타데이터로 저장 (aggregate 참조 가능)
```

**검증 전략 포인트**:

- Q1(과거) + Q2(현재) + Q3(유효성) 3문항이 cherry-picking 판단의 핵심
- Q4 claim_date 당시 수치로 "주장 시점 기준 맥락" 별도 확인
- `recent_days=730`으로 최신 evidence와 과거 evidence를 같은 pool에서 비교 가능

---

### 5.5 집계 노드 (aggregate_node)

**역할**: 4개 노드 Q&A를 claim별로 집계하여 claim마다 label + justification 생성

**처리 흐름**:

```
4개 노드의 questions 전체 수집
  │
  ▼ 각 claim에 대해 반복:
      1. questions에서 claim_id가 일치하는 것만 필터
      2. [claim 1개] + [해당 questions] → LLM 호출
         → label + justification 생성
         label 후보:
           "Supported"
           "Refuted"
           "Not Enough Evidence"
           "Conflicting Evidence/Cherrypicking"
      3. ClaimLabel(claim_id, label, justification) 생성
      * LLM 실패 / 파싱 오류 → "Not Enough Evidence" (안전 기본값)
  │
  └─ 반환: {"claim_labels": [ClaimLabel, ...]}
```

**설계 원칙**:

- 노드별 판정(PASS/WARNING/FAIL) 없음 — 순수 Q&A evidence만으로 LLM이 최종 판정
- claim별 독립 판정 — 각 claim이 자신의 Q&A만으로 label을 가짐
- `Numerical Claim`은 FACT + NUMERIC 두 노드 Q&A가 모두 해당 claim_id로 집계 → 더 많은 evidence
- justification은 LLM이 해당 claim의 Q&A를 보고 자연어로 서술 (증거 기반)

---

## 6. AVeriTeC 평가 연동

### 평가 방식

AVeriTeC는 label 정확도만 보는 것이 아니라, 예측한 **QA evidence가 gold QA와 얼마나 유사한지**를 함께 평가한다.

```
AVeriTeC Score = label이 맞고 METEOR(예측 QA, gold QA) ≥ 0.25인 경우 인정
```

주 지표: **Veracity Score @ METEOR 0.25**

### 실행 방법

```bash
# 예측 생성 → 채점
python capstone_ai/scripts/run_averitec_predictions.py \
  --input data/system_inputs.json --output predictions.json --limit 500
python eval.py --predictions predictions.json --references data/averitec_dev_gold.json
```

재시작 지원: 기존 predictions.json이 있으면 완료된 `eval_id`를 자동으로 건너뛴다.

## 7. 주요 설계 결정

### 7.1 Q&A 전용 구조 + Per-Claim 판정

**노드별 판정 제거**: 각 노드가 PASS/WARNING/FAIL 판정을 LLM으로 생성했지만, aggregate가 Q&A만으로 label을 결정하므로 불필요한 LLM 호출이었음. 제거하여 노드당 LLM 1회 절약.

```
각 노드:  질문 생성 → 검색 → 답변 생성 (판정 단계 없음)
aggregate: claim별 questions 필터 (claim_id) → claim마다 LLM → label + justification
```

**Per-Claim Label/Justification**: 문서 수준 단일 `label`/`justification` 대신 claim별 독립 판정.
- `Question`에 `claim_id` 부여 → aggregate에서 claim별 questions 필터
- claim마다 `ClaimLabel(claim_id, label, justification)` 생성
- justification은 claim과 동일 언어로 생성 (한국어 claim → 한국어 justification)

---

### 7.2 검색 비용 최적화

**문제**: Tavily 호출 ~120회/문서로 비용 과다. 비공식 출처(블로그·나무위키·유튜브)가 상위 evidence.

**해결**:
- 노드당 검색 쿼리 최대 3개 제한 (`all_queries[:3]`) — 질문 5~7개여도 검색은 1회
- 공식 도메인 retry 로직 제거
- `SearchProfile` 기반 검색 결과 재정렬: 공식 도메인 > 언어 적합 > 저품질
- 문서당 Tavily 호출: ~120회 → ~30회 (약 75% 감소)

---

## 8. 테스트 결과

### 채점 결과 (v7, 10-claim 샘플)

> **측정 조건**: AVeriTeC dev 0~9번 claim (10건), gpt-4o-mini, Tavily dev key, 전체 정상 처리

```
AVeriTeC evaluation (10 claims, v7):
======================================
Question-only score  (HU-meteor):  0.3685
Question-answer score (HU-meteor): 0.2801
======================================
Veracity F1 scores:
 * Supported:                       0.4000
 * Refuted:                         0.8571
 * Not Enough Evidence:             0.0000
 * Conflicting Evidence/Cherry:     0.0000
 * macro F1:                        0.3143
 * accuracy:                        0.7000  (7/10 정답)
Justification score (meteor):       0.0234
----------------------------------------------
Averitec scores:
 * Veracity scores (meteor @ 0.10): 0.7000
 * Veracity scores (meteor @ 0.20): 0.4000
 * Veracity scores (meteor @ 0.25): 0.4000   ← ★ 주 지표
 * Veracity scores (meteor @ 0.30): 0.2000
 * Veracity scores (meteor @ 0.40): 0.2000
 * Veracity scores (meteor @ 0.50): 0.0000
----------------------------------------------
유형별 (@ 0.20):
 * Event/Property Claim:  0.2158
 * Position Statement:    0.4925
 * Causal Claim:          0.2737
 * Numerical Claim:       0.1830
```

> 참고: FEVER 2024 shared task 공식 baseline = **0.11**, 중위권 = **0.20~0.40**, 우승팀 (TUDA_MAI) = **0.63**
> v7 측정값 0.40은 중위권 상단. 10건 샘플 기준이며 전체 500건 실행 시 수치 변동 가능

---

## 9. 한계 및 향후 과제

### 현재 한계

| 항목                      | 내용                                                                                                                                                                                     |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **검색 provider 의존성**  | Tavily가 한국 대상 claim에서도 해외 영문 기사만 반환하는 경우 있음                                                                                                                       |
| **단일 검색 전략**        | 각 노드가 질문별 쿼리를 합산해 **1회** 검색 — 이전 검색 결과를 보고 추가 검색하는 multi-hop 미적용. FEVER 2024 상위팀(Papelo, SynApSe, IKR3)은 multi-hop이 recall 향상에 핵심이라고 보고 |
| **질문 수 한계**          | v7 기준 노드당 4~7개 Q&A 생성. FEVER 2024 상위 시스템은 평균 9~12개 제출. gold Q&A 평균 2.57개보다는 충분하지만 상위 시스템 대비 질문 수 여전히 부족                                     |
| **Abstractive 답변 품질** | LLM이 일부 Abstractive 답변 생성 → 출처 문서와 모순 가능성. 우승팀 TUDA_MAI는 100% Extractive 답변으로 신뢰도 확보                                                                       |
| **NEE / CE 레이블 취약**  | Not Enough Evidence·Conflicting 레이블 판정이 어려움 — FEVER 2024 전 시스템 공통 문제 (평균 점수 ~0.06)                                                                                  |
| **numeric 시점 민감도**   | "최근 기준금리 3.5%"에서 numeric이 과거 2023년 자료로 PASS 판정                                                                                                                          |
| **DB 미연동**             | `storage.py` 현재 in-memory. Django는 API 응답의 `claim_labels`를 `FileReviewItem`에 claim별 저장하는 구조로 전환 완료                                                                   |
| **expanded query 중복**   | "한국 최근 한국 기준금리..." 같은 중복 단어 생성                                                                                                                                         |
| **출처 없는 문서**        | document_citations가 없으면 SOURCE claim 검증 생략                                                                                                                                       |

### 향후 과제

**1순위: AVeriTeC METEOR 점수 향상**

- Multi-Q&A 구현 완료 (1개 → 4~7개, v7에서 추가 확대)
- 목표: 질문 품질 고도화 + 추가 확대 (FEVER 2024 상위팀 기준 9~12개)
- evidence에서 직접 추출하는 Extractive 답변 비율 향상 (우승팀 TUDA_MAI: 100% Extractive)

**2순위: 검색 품질 고도화**

- 공식 도메인 allowlist 설정 파일 분리
- 한국어 claim의 한국 공식 도메인 우선순위 강화
- evidence 상위 N개 제한으로 LLM 입력 길이 제어

**3순위: 서비스 연동 완성**

- `storage.py` DB insert 구현 (현재 in-memory)
- Django AI 처리 상태 필드 (`ai_status`, `ai_completed_at`) 추가
- ※ Django `views.py`의 per-claim `FileReviewItem` 저장은 구현 완료 (`claim_labels` 매핑)

**4순위: numeric/fact 시점 민감도**

- "최근", "현재", "올해" 표현 있는 claim에 최신성 힌트 강제 추가
- recency FAIL 시 fact/numeric PASS를 보조 결과로 하향 처리

---

## 부록: 프로젝트 구조

```
b5_dev_package/
├── capstone_ai/                   # AI 백엔드
│   ├── src/ai_backend/
│   │   ├── main.py                # FastAPI 진입점
│   │   ├── api/routes/verify.py   # /verify API
│   │   ├── storage.py             # job 상태 관리
│   │   ├── graph/
│   │   │   ├── builder.py         # LangGraph 조립 (lazy LLM 초기화)
│   │   │   ├── state.py           # GraphState TypedDict
│   │   │   └── nodes/
│   │   │       ├── preprocess.py  # Claim 추출
│   │   │       ├── fact_check.py  # FACT 검증 + Multi-Q&A
│   │   │       ├── numeric_check.py # NUMERIC 검증 + Multi-Q&A
│   │   │       ├── source_check.py  # SOURCE 검증
│   │   │       ├── recency_check.py # RECENCY 검증
│   │   │       └── aggregate.py   # 집계 + AVeriTeC label
│   │   └── core/
│   │       ├── search.py          # Tavily/OpenAI 검색
│   │       ├── search_policy.py   # 검색 정책·rerank
│   │       └── verification.py    # 검증 공통 helper
│   ├── tests/                     # 88 unit + 2 integration
│   └── scripts/
│       └── run_averitec_predictions.py
│
├── capstone_demo/                 # Django 프론트엔드
│   ├── core/models.py             # 8개 모델
│   └── templates/core/
│       ├── review.html            # 3패널 자료 검증
│       └── archive.html           # 자료 보관함
│
├── data/
│   ├── system_inputs.json         # AVeriTeC dev 500건 (정답 제거)
│   └── averitec_dev_gold.json     # 채점 기준 (gold)
│
├── eval.py                        # AVeriTeC 공식 채점 스크립트
├── adapter.py                     # 우리 출력 → eval.py 형식 변환
└── run_eval.sh                    # 원클릭 채점 실행
```
