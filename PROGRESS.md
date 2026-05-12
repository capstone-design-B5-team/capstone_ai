# AI Backend Progress

작성일: 2026-05-11  
작업 경로: `C:\Users\defqw\ai-backend`

## 현재 구현 상태

AI 자료 검증 파이프라인의 핵심 흐름은 구현되어 있다.

- FastAPI 앱 진입점 구현
- `/health` 엔드포인트 구현
- `/verify` 비동기 접수 엔드포인트 구현
- `/verify/{job_id}/status` 개발용 상태 조회 엔드포인트 구현
- `/verify/{job_id}/result` 개발용 결과 조회 엔드포인트 구현
- LangGraph 기반 검증 파이프라인 조립
- 전처리, 사실 검증, 출처 검증, 최신성 검증, 수치 검증, 종합 노드 구현
- API/Pydantic 모델 구현
- 공유 DB 연동을 위한 `storage.py` 경계 모듈 추가
- Django 메인 백엔드의 `ProjectFile` / `FileReviewItem` 구조에 맞춰 `project_file_id` 입력 지원
- 로컬 디버깅용 단계별 로그 추가
- OpenAI web_search와 Tavily 검색 provider 둘 다 지원
- 검증 노드 단위 병렬 실행 및 노드 내부 claim 단위 병렬 처리 구현
- `aggregate_node` 기본 경로를 rule-based로 전환하여 LLM 호출 병목 제거
- 같은 claim에 대한 노드별 판정 충돌을 claim 단위 issue로 병합

최근 검증 결과:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit -q
# 88 passed

.\.venv\Scripts\python.exe -m pytest tests\integration -q
# 2 passed

.\.venv\Scripts\ruff.exe check src tests
# All checks passed
```

## 메인 백엔드 연동 확인

메인 Django 백엔드 레포:

```text
https://github.com/dangyulee/capstone_demo
```

확인한 Django 모델:

- `core.ProjectFile`: 검증 대상 문서
- `core.FileReviewItem`: AI 검증 결과 표시용 테이블
- `core.ProjectReference`: 프로젝트 참고자료
- `core.TeamReview`, `core.TeamReviewItem`: 팀원 리뷰

AI 백엔드가 맞춰야 할 핵심 테이블은 다음과 같다.

```text
ProjectFile.id          -> VerifyRequest.project_file_id
ProjectFile.content     -> VerifyRequest.text
ProjectFile.topic       -> VerifyRequest.topic
FileReviewItem          -> AI 결과 저장 대상
```

`FileReviewItem` 매핑 예정:

```text
project_file_id         <- VerifyResponse.project_file_id
highlighted_text        <- FinalIssue.original_text
problem                 <- FinalIssue.reason
suggestion              <- FinalIssue.suggestion
order                   <- issue index
```

주의 사항:

- Django의 `ProjectFile.status`는 `pending / verified / rejected`이다.
- 이 값은 AI 처리 상태가 아니라 팀/리더 검토 상태로 보인다.
- AI 처리 상태는 별도 필드나 별도 테이블을 Django 쪽에서 추가하는 것이 안전하다.
- 추천 필드: `ai_status`, `ai_requested_at`, `ai_completed_at`, `ai_error`.

## Verify API 현재 동작

### POST `/verify`

요청을 접수하고 즉시 `202 Accepted`를 반환한다. 실제 검증은 FastAPI `BackgroundTasks`에서 실행한다.

입력 예시:

```json
{
  "project_file_id": 123,
  "request_id": "verify-project-file-123",
  "project_id": 1,
  "topic": "자료 제목",
  "text": "검증할 본문 전체 텍스트",
  "document_citations": []
}
```

응답 예시:

```json
{
  "job_id": "verify-project-file-123",
  "project_file_id": 123,
  "document_id": "123",
  "request_id": "verify-project-file-123",
  "status": "accepted"
}
```

`request_id`가 있으면 `job_id`로 사용한다. 없으면 `verify-{uuid}` 형식으로 생성한다.

### GET `/verify/{job_id}/status`

현재는 개발용 in-memory 상태 조회다.

가능 상태:

```text
accepted | processing | completed | failed
```

### GET `/verify/{job_id}/result`

DB 연결 전 결과 확인을 위한 개발용 endpoint다. 완료된 job의 `VerifyResponse` 전체를 반환한다.

운영에서는 이 endpoint보다 Django가 공유 DB를 직접 조회하는 흐름이 목표다.

## 전체 데이터 흐름

```text
Django ProjectFile
  |
  | project_file_id, text, topic
  v
POST /verify
  |
  | 202 accepted
  v
BackgroundTasks
  |
  v
GraphState 초기화
  |
  v
preprocess_node
  |
  | claims 추출 및 타입 분류
  v
fact_check_node / source_check_node / recency_check_node / numeric_check_node
  |
  | verifier별 VerificationResult 생성
  v
aggregate_node
  |
  | rule-based final_grade, final_report 생성
  | 같은 claim의 충돌 판정은 하나의 issue로 병합
  v
storage.py
  |
  | DB 규격 확정 후 FileReviewItem 등에 저장
  v
Django polls/reads DB
```

## 병렬 처리 상태

LangGraph 레벨에서 네 검증 노드는 병렬로 실행된다.

```text
preprocess
  -> fact_check
  -> source_check
  -> recency_check
  -> numeric_check
  -> aggregate
```

2026-05-08 작업으로 각 검증 노드 내부의 claim 처리도 병렬화했다.

```text
fact_check_node:    FACT claim 최대 4개 병렬 처리
numeric_check_node: NUMERIC claim 최대 4개 병렬 처리
recency_check_node: RECENCY claim 최대 4개 병렬 처리
source_check_node:  SOURCE claim 최대 4개 병렬 처리
```

결과 순서는 원래 claim 순서대로 유지한다.

## 검색 Provider 상태

현재 두 provider를 지원한다.

```dotenv
SEARCH_PROVIDER=openai
```

- `OpenAIWebSearchClient` 사용
- OpenAI Responses API `web_search` 사용
- 최근 최적화로 claim 하나에 대해 검색과 판정을 한 번에 수행하는 `verify_claim_once()` 경로 추가
- 같은 claim이 FACT/NUMERIC/RECENCY 여러 노드에 걸리면 in-memory cache로 중복 OpenAI 호출을 막는다

```dotenv
SEARCH_PROVIDER=tavily
```

- `TavilySearchClient` 사용
- 검색 결과 수집은 Tavily가 담당
- 판단은 기존 검증 LLM이 담당
- 현재 실제 테스트 기준 OpenAI web_search보다 훨씬 빠르고 디버깅하기 쉽다

현재 판단:

- 속도와 검색 API 역할에는 Tavily가 더 적합하다.
- OpenAI web_search는 구현되어 있으나 latency 변동이 커서 다수 claim 검증에는 느릴 수 있다.

## 실제 로컬 테스트 결과

### 테스트 1: 명백한 오류 포함 문서

테스트 입력: `test-verify-wrong-001`

본문 요약:

```text
2024년 한국의 기준금리는 연중 1.0%로 유지되었다.
한국은행은 2024년 한국의 경제성장률을 5.5%로 발표했다.
통계청에 따르면 2024년 한국의 총인구는 1억 명을 넘었다.
최근 OECD 자료에 따르면 한국의 합계출산율은 OECD 국가 중 가장 높은 수준이다.
```

결과:

- claim 4개 추출
- verification result 9개 생성
- 최종 등급: `확인 필요`
- final_report issue 4개 생성
- 네 개 주장을 모두 `FAIL`로 탐지

성능 로그:

```text
preprocess_node: 8.89s
numeric_check_node: 6.60s
recency_check_node: 9.37s
fact_check_node: 9.66s
aggregate_node: 19.53s
total verify graph: 38.09s
```

관찰:

- Tavily 검색은 대부분 0.5~1.7초, 느린 경우 4.22초였다.
- 검색 병목은 상당히 해소됐다.
- 위 성능 로그는 `aggregate_node`가 LLM을 호출하던 최적화 전 기록이다.
- 2026-05-11 기준 `aggregate_node` 기본 경로는 LLM을 호출하지 않고 rule-based로 즉시 종합한다.

검증 결과 품질:

- 기준금리 1.0% 유지: `FAIL`
- 경제성장률 5.5%: `FAIL`
- 총인구 1억 명 초과: `FAIL`
- 합계출산율 OECD 최고 수준: `FAIL`

### 테스트 2: 환경 정책/초미세먼지 문서

테스트 입력: `project_file_id=102`

본문 요약:

```text
환경부 보도자료에 따르면 2023년 전국 초미세먼지(PM2.5) 연평균 농도는 18㎍/㎥로 2015년 대비 약 30% 감소했다.
서울의 2023년 초미세먼지 농도는 WHO 권고 기준인 5㎍/㎥보다 여전히 높다.
수도권 미세먼지 배출량이 20% 이상 감소할 것으로 예상된다.
document_citations: https://www.me.go.kr
```

결과:

- claim 3개 추출
- verification result 7개 생성
- 최종 등급: `확인 필요`
- final_report issue 2개 생성

claim 추출:

```text
1. 2023년 전국 초미세먼지(PM2.5) 연평균 농도는 18㎍/㎥로 2015년 대비 약 30% 감소했다
   -> FACT, NUMERIC, SOURCE
2. 서울의 2023년 초미세먼지 농도는 WHO 권고 기준인 5㎍/㎥보다 여전히 높다
   -> FACT
3. 수도권 미세먼지 배출량이 20% 이상 감소할 것으로 예상된다
   -> FACT, RECENCY
```

판정 요약:

```text
claim 1 fact:    PASS
claim 1 source:  WARNING for "환경부 보도자료" reference
claim 1 source:  FAIL for https://www.me.go.kr 접근 실패
claim 1 numeric: FAIL
claim 2 fact:    PASS
claim 3 fact:    WARNING
claim 3 recency: WARNING
```

관찰:

- `document_citations`의 `https://www.me.go.kr`가 claim 1 SOURCE 검증에 사용됐다.
- `https://www.me.go.kr` 직접 접근이 `ConnectError`로 실패했고, source verifier가 `FAIL`로 처리했다.
- 같은 claim 1에 대해 fact verifier는 `PASS`, numeric verifier는 `FAIL`을 냈다.
- fact verifier는 "18㎍/㎥ 및 약 30% 감소"를 대체로 인정했지만, numeric verifier는 2023년 수치를 19.2㎍/㎥로 잡아 감소율 26.9%라며 `FAIL` 처리했다.
- claim 3 recency 검색 결과에 해외 대기오염 기사들이 많이 섞였다. 한국 수도권 정책/통계와 무관한 결과가 포함되어 검색 품질 개선이 필요하다.
- 최적화 전 최종 report는 source 접근 실패와 numeric 오류 2개를 issue로 뽑았다.
- 2026-05-11 변경 후 같은 claim의 `fact=PASS`, `numeric=FAIL` 같은 충돌은 claim 단위로 하나의 대표 issue로 병합하고, reason에 노드별 판정과 충돌 감지를 남긴다.

품질 이슈:

- numeric/fact verifier 간 판정 충돌은 `aggregate_node`에서 claim 단위로 병합하도록 1차 처리했다.
- `https://www.me.go.kr` 루트 URL은 실제 보도자료 상세 URL이 아니라 접근/검증 품질이 낮다.
- reference citation `"환경부 보도자료"`는 구체 문서가 아니라서 source verifier가 `WARNING`을 반환했다.
- recency verifier가 한국/수도권 도메인에 충분히 제한되지 않아 해외 smog 기사들이 evidence로 들어왔다.

### 테스트 3: recency 검색 범위 개선 후 재검증

테스트 입력: `project_file_id=102`

본문 요약:

```text
환경부는 수도권 미세먼지 배출량을 2030년까지 20% 줄일 계획이다.
최근 한국의 기준금리는 3.5%로 유지되고 있다.
```

결과:

- claim 2개 추출
- 두 claim 모두 `FACT`, `NUMERIC`, `RECENCY`로 분류
- verification result 6개 생성
- 최종 등급: `확인 필요`
- final_report issue 2개 생성

claim 1 판정 요약:

```text
claim: 수도권 미세먼지 배출량을 2030년까지 20% 줄일 계획이다
fact:    PASS
numeric: PASS
recency: WARNING
final issue: WARNING
```

관찰:

- `recency_profile`은 의도대로 생성됐다.
  - `language`: `ko`
  - `country_hint`: `KR`
  - `region_terms`: `["수도권"]`
  - `institution_terms`: `["환경부"]`
  - `time_terms`: `["2030년"]`
- recency search query도 지역/목표연도를 보존했다.
  - `수도권 미세먼지 배출량 2030년 20% 감소 계획`
  - `환경부 미세먼지 정책 2025 2026`
- 다만 Tavily의 recency 검색 결과가 Guardian, ABC News, ColoradoBiz, Greenwich Time 등 해외 배출/스모그 기사 위주로 들어왔다.
- rerank는 검색 결과 후보 안에서 순서만 바꿀 수 있으므로, 한국 공식/국내 후보가 애초에 충분히 반환되지 않으면 품질 개선에 한계가 있다.
- 최종 recency 판단은 “2030년 목표는 있으나 최신 evidence가 해당 정책의 구체적 갱신/변경 사항을 제공하지 않는다”는 이유로 `WARNING`이었다.

claim 2 판정 요약:

```text
claim: 한국의 기준금리는 3.5%로 유지되고 있다
fact:    PASS
numeric: PASS
recency: FAIL
final issue: FAIL
```

관찰:

- `recency_profile`은 의도대로 생성됐다.
  - `language`: `ko`
  - `country_hint`: `KR`
  - `region_terms`: `["한국"]`
  - `time_terms`: `["최근"]`
- recency search query:
  - `2026년 한국 기준금리`
  - `2025년 한국 기준금리`
- recency evidence는 CNBC 등 해외 영문 기사였지만, 한국 기준금리가 2.50%라는 최신 정보와 claim의 3.5%가 충돌한다고 판단해 `FAIL`을 냈다.
- 반면 fact/numeric verifier는 과거 2023~2024년의 3.5% 동결 자료, 블로그, 위키/나무위키/유튜브 등을 근거로 `PASS`를 냈다.
- aggregate는 같은 claim의 `fact=PASS`, `numeric=PASS`, `recency=FAIL`을 하나의 claim issue로 병합했고, reason에 노드별 판정 충돌을 남겼다.

종합 분석:

- `RecencyProfile` 기반 언어/지역/기관/시점 추출은 동작한다.
- aggregate의 claim 단위 충돌 병합도 동작한다.
- recency는 최신 정보 충돌을 잡아내는 데 효과가 있었지만, 검색 provider 결과 품질에는 아직 취약하다.
- 특히 “한국어 + 한국 대상 claim”에서도 Tavily가 해외 영문 최신 뉴스만 반환할 수 있다.
- fact/numeric 노드도 공식 출처 우선순위가 약해 블로그, 위키, 나무위키, 유튜브를 근거로 삼는 문제가 남아 있다.
- 따라서 다음 개선은 recency만이 아니라 공통 검색 계층에서 공식 도메인 query 확장과 result filtering/rerank를 강화하는 방향이 적절하다.

### 테스트 4: 공통 검색 정책 계층 적용 후 재검증

테스트 입력: `project_file_id=102`

본문 요약:

```text
환경부는 수도권 미세먼지 배출량을 2030년까지 20% 줄일 계획이다.
최근 한국의 기준금리는 3.5%로 유지되고 있다.
```

결과:

- claim 2개 추출
- 두 claim 모두 `FACT`, `NUMERIC`, `RECENCY`로 분류
- verification result 6개 생성
- 최종 등급: `확인 필요`
- final_report issue 1개 생성

claim 1 판정 요약:

```text
claim: 수도권 미세먼지 배출량을 2030년까지 20% 줄일 계획이다
fact:    PASS
numeric: PASS
recency: PASS
final issue: 없음
```

개선 확인:

- fact evidence의 1순위가 환경부 공식 정책정보 페이지로 올라왔다.
- fact metadata:
  - `official_retry`: `false`
  - 기존 일반 검색 결과 안에 이미 `me.go.kr` 공식 후보가 있어 보강 검색이 필요 없었다.
  - ranking 1순위 reason: `official_domain`, `korean_text`, `korea_signal`, `region:수도권`, `institution:환경부`
- recency는 공식 보강 검색이 작동했다.
  - `official_retry`: `true`
  - `expanded_queries`:
    - `수도권 환경부 2030년 수도권 미세먼지 배출량 2025 2026 site:me.go.kr`
    - `수도권 환경부 2030년 수도권 미세먼지 배출량 2025 2026 site:korea.kr`
  - ranking 상위가 `me.go.kr`, `korea.kr` 공식 자료로 채워졌다.
  - AP, Guardian 등 해외 기사는 `foreign_or_non_korean_for_kr_claim`으로 낮은 점수를 받아 뒤로 밀렸다.
- 이전 테스트 3에서 같은 claim의 recency가 해외 evidence 위주라 `WARNING`이었는데, 공통 검색 정책 적용 후 `PASS`로 개선됐다.

claim 2 판정 요약:

```text
claim: 한국의 기준금리는 3.5%로 유지되고 있다
fact:    WARNING
numeric: PASS
recency: FAIL
final issue: FAIL
```

개선 확인:

- fact는 공식 보강 검색을 수행했다.
  - `official_retry`: `true`
  - `expanded_queries`:
    - `한국 최근 한국 기준금리 3.5% site:bok.or.kr`
    - `한국 최근 한국 기준금리 3.5% site:korea.kr`
  - ranking 상위가 한국은행(`bok.or.kr`)과 정책브리핑(`korea.kr`) 자료로 채워졌다.
  - `blog.naver.com`, `www.youtube.com`, `namu.wiki`는 `filtered_domains`에 잡혔고 ranking 하위로 밀렸다.
- recency도 공식 보강 검색을 수행했다.
  - `expanded_queries`:
    - `한국 최근 2025년 한국 기준금리 site:bok.or.kr`
    - `한국 최근 2025년 한국 기준금리 site:korea.kr`
  - ranking 상위가 한국은행 통화신용정책보고서/정책자료로 채워졌다.
  - 2025년 5월 기준금리 2.50% 인하 흐름을 근거로 `FAIL`을 냈다.
- aggregate는 fact `WARNING`, numeric `PASS`, recency `FAIL`을 하나의 claim issue로 병합했다.
- 최종 report는 기준금리 claim 1개만 issue로 남겼다.

남은 문제:

- numeric verifier는 “최근” 문맥보다 과거 2023~2024년 3.50% 유지 자료를 근거로 `PASS`를 냈다.
- 같은 claim에서 recency가 `FAIL`을 내므로 최종 결과는 안전하게 `확인 필요`가 되지만, numeric 노드도 `최근`, `현재`, `올해` 같은 시점 표현을 더 강하게 반영할 필요가 있다.
- expanded query 문자열에 중복 단어가 생길 수 있다. 예: `한국 최근 한국 기준금리...`, `수도권 환경부 2030년 수도권...`.
- 공식 검색으로 evidence 품질은 개선됐지만, evidence 수가 많아 LLM 입력이 길어질 수 있다. 향후 상위 N개 제한이나 공식/비공식 균형 조정이 필요하다.

종합 분석:

- 공통 검색 정책 계층은 실제 테스트에서 효과가 확인됐다.
- 환경/미세먼지 claim은 해외 evidence 문제를 해소하고 공식 자료 중심으로 `PASS`까지 개선됐다.
- 기준금리 claim은 한국은행/정책브리핑 공식 자료가 상위로 올라왔고, 저품질 도메인이 metadata에 식별됐다.
- 다음 개선은 numeric/fact의 시점 민감도 강화, expanded query 중복 제거, evidence 개수/길이 제한이다.

## 주요 타입

### Claim type

```text
FACT | SOURCE | RECENCY | NUMERIC
```

### Verdict

```text
PASS | WARNING | FAIL | UNVERIFIABLE
```

### Final grade

```text
통과 | 주의 | 확인 필요
```

### FinalReport

```json
{
  "final_grade": "주의",
  "summary": "전체 검증 결과 요약",
  "issues": [
    {
      "node": "numeric",
      "original_text": "문제가 있는 원문",
      "judgment": "WARNING",
      "reason": "문제 설명",
      "suggestion": "수정 제안"
    }
  ]
}
```

## 구현된 테스트

주요 테스트 파일:

```text
tests/unit/test_verify_api.py
tests/unit/test_preprocess.py
tests/unit/test_fact_check.py
tests/unit/test_source_check.py
tests/unit/test_recency_check.py
tests/unit/test_numeric_check.py
tests/unit/test_aggregate.py
tests/unit/test_search.py
tests/unit/test_ids.py
tests/unit/test_models.py
tests/integration/test_graph.py
```

현재 단위 테스트는 외부 API 호출 없이 mock 기반으로 동작한다.

## 최근 완료

### 1. aggregate_node 최적화

2026-05-11 완료.

- 기본 실행 경로에서 aggregation LLM 호출을 제거했다.
- `llm`을 명시 주입한 테스트/디버그 경로에서는 기존 LLM aggregate를 계속 사용할 수 있다.
- `FAIL` 또는 `UNVERIFIABLE`이 하나라도 있으면 최종 등급은 `확인 필요`다.
- `WARNING`만 있으면 `주의`, 모두 `PASS`면 `통과`다.
- 최적화 전 병목이던 `aggregate_node` LLM 호출 19.53초는 기본 경로에서 제거됐다.

### 2. verifier 간 판정 충돌 조정

2026-05-11 1차 완료.

- 같은 claim의 FACT/NUMERIC/RECENCY/SOURCE 결과를 aggregate 단계에서 claim 단위로 묶는다.
- final_report에는 claim별 대표 issue만 남긴다.
- 내부 results에는 모든 verifier 결과를 유지한다.
- 대표 판정 우선순위는 `FAIL > UNVERIFIABLE > WARNING > PASS`다.
- 예: fact는 `PASS`, numeric은 `FAIL`인 경우 최종 issue는 하나이며, reason에 `노드 간 판정 충돌 감지`, `사실관계=PASS`, `수치=FAIL`이 포함된다.

추가 테스트:

```text
tests/unit/test_aggregate.py
- test_default_aggregate_skips_llm_call
- test_groups_conflicting_node_results_by_claim
```

### 3. recency 검색 범위 개선

2026-05-11 1차 완료.

- 미세먼지 claim 전용이 아니라 모든 RECENCY claim에 적용되는 `RecencyProfile`을 추가했다.
- claim의 언어, 한국 대상 여부, 지역어, 기관어, 시점/목표연도, 우선 공식 도메인을 추출한다.
- LLM이 검색 쿼리를 만들지 못한 경우에도 단순히 `2025 2026 최신`만 붙이지 않고, 지역/기관/기준연도/목표연도를 보존한 fallback query를 만든다.
- 검색 결과를 판단 LLM에 넘기기 전에 locale/topic/time relevance 기준으로 재정렬한다.
- 한국어 + 한국 대상 claim은 한국어 evidence와 한국 공식/공공 도메인을 우선한다.
- 영어/해외 claim은 영어 evidence와 해당 국가/국제 공식 도메인을 우선한다.
- claim 대상과 다른 해외 사례는 직접 반박 근거가 아니라 낮은 우선순위 참고 evidence로 밀린다.

추가 테스트:

```text
tests/unit/test_recency_check.py
- test_fallback_queries_preserve_korean_region_institution_and_target_year
- test_reranks_korean_official_evidence_above_unrelated_foreign_news
- test_reranks_english_official_evidence_for_non_korean_claim
```

### 4. 공통 검색 정책 계층 구현

2026-05-11 1차 완료.

- `src/ai_backend/core/search_policy.py`를 추가했다.
- `fact_check`, `numeric_check`, `recency_check`가 공통 검색 보강/필터/rerank 정책을 사용한다.
- claim의 언어, 한국 대상 여부, 지역어, 기관어, 시점/목표연도, 공식 도메인, 저품질 도메인을 `SearchProfile`로 추출한다.
- 한국어 + 한국 대상 claim에서 상위 evidence에 공식/국내 후보가 부족하면 공식 도메인 보강 검색을 추가 실행한다.
- 예:
  - 기준금리 claim: `site:bok.or.kr`, `site:korea.kr`
  - GDP/인구/출산율 claim: `site:kostat.go.kr`, `site:korea.kr`
  - 환경/미세먼지 claim: `site:me.go.kr`, `site:korea.kr`
- 검색 결과는 공식 도메인, 한국어/영어 적합성, 지역/기관/연도 포함 여부, 발행일, 저품질 도메인 여부로 재정렬한다.
- 유튜브, 나무위키, 위키, 네이버 블로그, 티스토리 계열은 낮은 점수를 받는다.
- 각 verifier result metadata에 다음 디버깅 정보를 남긴다.
  - `search_profile`
  - `expanded_queries`
  - `official_retry`
  - `filtered_domains`
  - `ranking`

추가 테스트:

```text
tests/unit/test_search_policy.py
- test_expands_korean_rate_claim_to_bok_official_query
- test_search_verification_evidence_retries_official_domain_and_reranks
- test_rank_search_results_pushes_low_quality_below_official
- test_non_korean_claim_does_not_expand_to_korean_official_domain
```

## 다음 할 일

### 1. 공식 출처 우선 검색/필터링 고도화

1차 공통 검색 정책은 구현했다. 다만 실제 검색 품질은 provider 후보 품질에도 영향을 받는다.

남은 개선 방향:

- 공식 도메인 allowlist를 설정 파일로 분리한다.
- claim 주제별 도메인 매핑을 더 넓힌다.
- 저품질 evidence를 단순 후순위가 아니라 제외할지 정책을 정한다.
- 공식 보강 검색 후에도 관련 evidence가 부족하면 `UNVERIFIABLE` 또는 `WARNING`으로 보수 처리할지 검토한다.
- metadata의 `ranking`을 운영 로그에서 쉽게 확인할 수 있게 요약 로그를 추가한다.
- expanded query의 중복 지역/기관/시점 단어를 정리한다.
- 공식 evidence가 충분히 확보되면 저품질 evidence를 LLM 입력에서 제외하는 정책을 검토한다.
- verifier별 evidence 상위 N개 제한을 둬 LLM 입력 길이를 제어한다.

### 2. numeric/fact 시점 민감도 개선

기준금리 테스트에서 claim은 “최근”을 포함했지만 numeric verifier는 2023~2024년 3.50% 유지 자료를 근거로 `PASS`를 냈다.

개선 방향:

- claim에 `최근`, `현재`, `올해`, `2026년 기준` 같은 시점 표현이 있으면 numeric/fact query에도 최신성 힌트를 강하게 추가한다.
- numeric verifier가 시점 표현이 있는 수치 claim을 검증할 때 recency evidence와 충돌하면 `PASS` 대신 `WARNING` 이상으로 보수 처리한다.
- fact/numeric judgment prompt에 “과거에 맞았던 수치가 현재 claim을 입증하지는 않는다”는 기준을 명시한다.
- aggregate 전 단계에서 같은 claim의 recency `FAIL`이 있으면 fact/numeric `PASS`를 보조 결과로 낮춰 표시할지 검토한다.


### 3. document_citations와 source_check 정책 정리

현재 SOURCE claim이 0개이면 source_check가 실행되지 않는다.

문제:

- 요청에 `document_citations`가 있어도 claim type에 SOURCE가 없으면 출처 검증이 생략된다.

결정 필요:

- 문서 전체 citation이 있으면 모든 FACT/NUMERIC claim에 source_check를 적용할지
- citation이 있는 claim만 SOURCE로 강제할지
- 출처 검증 결과를 final_report에 얼마나 반영할지

### 4. recency 검색 고도화

1차 개선은 완료했지만, 현재 profile 추출은 규칙 기반이다.

남은 개선 방향:

- 한국 외 국가/지역/기관명도 더 폭넓게 추출한다.
- 공식 도메인 우선순위를 domain allowlist 설정으로 분리한다.
- rerank 점수를 metadata에 남겨 디버깅 가능하게 한다.
- Tavily 검색 쿼리 자체에 locale/domain 힌트를 더 적극적으로 반영할지 검토한다.
- 한국어 + 한국 대상 claim에서 해외 evidence만 반환되면 공식 도메인 보강 검색을 재시도한다.
- 검색 결과 후보 중 claim 대상 국가/지역과 맞지 않는 evidence 비율이 높으면 `WARNING` 또는 `UNVERIFIABLE` 쪽으로 보수적으로 처리한다.

### 5. 실제 공유 DB 저장 구현

현재 `storage.py`는 in-memory 저장소다. DB 규격이 확정되면 다음 함수 내부를 구현한다.

```text
mark_verify_job_accepted
mark_verify_job_processing
save_verify_job_result
save_verify_job_error
get_verify_job_status
get_verify_job_result
```

우선 구현 대상:

- `save_verify_job_result`: `final_report.issues`를 Django `core_filereviewitem`에 저장
- 기존 AI 결과 삭제 후 재생성할지, append할지 정책 결정
- 실패 시 `ai_error` 또는 별도 상태 테이블에 기록



## 현재 결론

현재 AI 백엔드는 Django가 검증 요청을 보낼 수 있는 `/verify` 접수 API와 LangGraph 백그라운드 처리 흐름까지 구현되어 있다. Tavily 기반 검색과 claim 단위 병렬화까지 적용되어 실제 테스트에서 거짓 claim 탐지와 final_report 생성이 가능함을 확인했다.

다음 작업의 우선순위는 공식 출처 정책 고도화, source_check 정책 정리, recency 검색 고도화, 공유 DB 저장 구현이다.
