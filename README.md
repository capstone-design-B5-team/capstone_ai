# AI Backend

FastAPI 기반 AI 자료 검증 서버입니다. Django 메인 백엔드가 검증할 문서 텍스트를 넘기면, AI 백엔드는 요청을 접수하고 LangGraph 검증 파이프라인을 백그라운드에서 실행합니다.

이 레포는 Django 메인 백엔드와 분리된 독립 서비스로 배포하는 것을 전제로 합니다. 서비스는 분리하지만 운영 DB는 공유할 수 있습니다. Django가 DB migration과 원본 문서 모델을 소유하고, AI 백엔드는 검증 worker처럼 동작하면서 정해진 결과 테이블만 읽고 씁니다.

## 전체 구조

```text
Frontend
  |
  v
Django main backend
  |
  | POST /verify
  v
AI Backend (FastAPI)
  |
  | LangGraph verification pipeline
  v
Shared production DB
  |
  v
Django reads FileReviewItem / ai_status
```

역할 분리:

```text
Django main backend
- 사용자 인증/권한
- 프로젝트/문서 관리
- PDF 업로드 및 텍스트 추출
- ProjectFile.content 저장
- AI 백엔드에 /verify 요청
- AI 결과 조회 및 화면 표시
- DB migration 소유

AI backend
- VerifyRequest 접수
- Claim 추출
- 사실/출처/최신성/수치 검증
- 최종 등급과 issue 생성
- 공유 DB 저장 경계 제공
```

현재 `storage.py`는 로컬 개발용 in-memory 저장소입니다. 공유 DB 스키마가 확정되면 `storage.py` 내부만 실제 DB write로 교체하면 API route와 graph 코드는 유지됩니다.

## 데이터 흐름

```text
Django ProjectFile
  |
  | project_file_id, topic, text, document_citations
  v
POST /verify
  |
  | 202 Accepted
  v
FastAPI BackgroundTasks
  |
  v
GraphState 초기화
  |
  v
preprocess_node
  |
  | Claim[]
  v
fact_check_node / source_check_node / recency_check_node / numeric_check_node
  |
  | VerificationResult[]
  v
aggregate_node
  |
  | final_grade, final_report
  v
storage.py
  |
  | 향후 core_filereviewitem / ProjectFile.ai_status 저장
  v
Django reads shared DB
```

LangGraph 레벨에서 네 검증 노드(`fact`, `source`, `recency`, `numeric`)는 병렬 실행됩니다. 각 검증 노드 내부에서도 claim 단위 처리를 `ThreadPoolExecutor`로 병렬화합니다.

## API

### POST `/verify`

검증 요청을 접수합니다. 최종 결과를 기다리지 않고 즉시 `202 Accepted`를 반환합니다.

요청 예시:

```json
{
  "project_file_id": 102,
  "request_id": "verify-project-file-102",
  "project_id": 1,
  "topic": "환경 정책 보고서",
  "text": "환경부는 수도권 미세먼지 배출량을 2030년까지 20% 줄일 계획이다.",
  "document_citations": [
    {
      "raw_text": "https://www.me.go.kr",
      "citation_type": "url"
    }
  ]
}
```

요청 필드:

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `project_file_id` | `int \| null` | 조건부 | Django `core.ProjectFile.id`. 공유 DB 연동의 기준 ID입니다. |
| `document_id` | `str \| null` | 조건부 | 레거시/외부 문서 ID입니다. `project_file_id`가 있으면 자동으로 문자열 ID를 채웁니다. |
| `request_id` | `str \| null` | 아니오 | Django가 넘기는 idempotency/request ID입니다. 있으면 `job_id`로 사용합니다. |
| `project_id` | `int \| null` | 아니오 | Django `core.Project.id`입니다. 현재는 메타데이터입니다. |
| `topic` | `str \| null` | 아니오 | 문서 제목 또는 주제입니다. |
| `text` | `str` | 예 | 검증할 본문 텍스트입니다. PDF 파일 자체가 아니라 Django가 추출한 텍스트를 넘깁니다. |
| `document_citations` | `Citation[]` | 아니오 | 문서 전체에 붙은 URL/참고문헌 목록입니다. |

`project_file_id`와 `document_id` 중 하나는 반드시 있어야 합니다.

`Citation` 필드:

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `raw_text` | `str` | 원본 URL 또는 참고문헌 문자열 |
| `citation_type` | `"url" \| "reference"` | URL인지 문헌/텍스트 reference인지 구분 |

즉시 응답:

```json
{
  "job_id": "verify-project-file-102",
  "project_file_id": 102,
  "document_id": "102",
  "request_id": "verify-project-file-102",
  "status": "accepted"
}
```

응답 필드:

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `job_id` | `str` | 검증 job 식별자입니다. `request_id`가 있으면 같은 값을 씁니다. |
| `project_file_id` | `int \| null` | 요청에서 받은 Django ProjectFile ID입니다. |
| `document_id` | `str` | 문서 ID입니다. |
| `request_id` | `str \| null` | 요청에서 받은 request ID입니다. |
| `status` | `"accepted"` | 접수 상태입니다. |

### GET `/verify/{job_id}/status`

로컬 개발용 상태 조회 API입니다. 운영에서는 Django가 공유 DB를 읽는 흐름이 목표입니다.

응답 예시:

```json
{
  "job_id": "verify-project-file-102",
  "status": "processing"
}
```

가능 상태:

```text
accepted | processing | completed | failed
```

실패 시:

```json
{
  "job_id": "verify-project-file-102",
  "status": "failed",
  "error": "error message"
}
```

### GET `/verify/{job_id}/result`

로컬 개발용 결과 조회 API입니다. 완료된 job의 `VerifyResponse`를 반환합니다. 운영에서는 공유 DB의 `FileReviewItem`과 `ProjectFile.ai_status` 조회로 대체하는 것이 목표입니다.

## 최종 결과 스키마

`VerifyResponse`:

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `project_file_id` | `int \| null` | Django ProjectFile ID |
| `document_id` | `str` | 문서 ID |
| `claims` | `Claim[]` | 전처리 노드가 추출한 검증 대상 주장 |
| `results` | `VerificationResult[]` | 각 검증 노드의 claim별 판정 결과 |
| `final_grade` | `"통과" \| "주의" \| "확인 필요"` | 문서 최종 등급 |
| `final_report` | `FinalReport` | 사용자에게 보여줄 최종 요약과 issue 목록 |

`Claim`:

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `id` | `str` | claim UUID |
| `content_hash` | `str` | claim 텍스트 기반 12자리 hash |
| `document_id` | `str` | 문서 ID |
| `text` | `str` | 검증할 주장 문장 |
| `type` | `ClaimType[]` | `FACT`, `NUMERIC`, `SOURCE`, `RECENCY` 중 하나 이상 |
| `context` | `str` | 원문 주변 문맥 |
| `citations` | `Citation[]` | claim에 직접 붙은 출처 |
| `extracted_at` | `datetime` | 추출 시각 |
| `parent_claim_id` | `str \| null` | 재검증/파생 claim용 부모 ID |

`VerificationResult`:

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `id` | `str` | result UUID |
| `claim_id` | `str` | 대상 claim ID |
| `verifier` | `"fact" \| "source" \| "recency" \| "numeric"` | 검증 노드 이름 |
| `verdict` | `"PASS" \| "WARNING" \| "FAIL" \| "UNVERIFIABLE"` | 노드 판정 |
| `confidence` | `float` | 0.0~1.0 신뢰도 |
| `evidence` | `str[]` | LLM 판단에 전달된 검색/출처 evidence 요약 |
| `reasoning` | `str` | 노드별 판단 이유 |
| `sources` | `str[]` | evidence URL |
| `metadata` | `object` | 디버깅용 내부 데이터 |
| `verified_at` | `datetime` | 검증 시각 |
| `parent_result_id` | `str \| null` | 재검증/파생 result용 부모 ID |

`FinalReport`:

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `final_grade` | `"통과" \| "주의" \| "확인 필요"` | 최종 등급 |
| `summary` | `str` | 전체 요약 |
| `issues` | `FinalIssue[]` | 사용자에게 표시할 문제 목록 |

`FinalIssue`:

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `node` | `str` | 문제를 만든 노드 라벨. 예: `수치`, `최신성`, `사실관계, 최신성` |
| `original_text` | `str` | 문제가 있는 원문 claim |
| `judgment` | `Verdict` | 대표 판정 |
| `reason` | `str` | 문제 설명 |
| `suggestion` | `str` | 수정 제안 |

예시:

```json
{
  "project_file_id": 102,
  "document_id": "102",
  "claims": [
    {
      "id": "claim-id",
      "content_hash": "f4fce7e1a62a",
      "document_id": "102",
      "text": "한국의 기준금리는 3.5%로 유지되고 있다",
      "type": ["FACT", "NUMERIC", "RECENCY"],
      "context": "최근 한국의 기준금리는 3.5%로 유지되고 있다.",
      "citations": [],
      "extracted_at": "2026-05-11T05:50:35.405436Z",
      "parent_claim_id": null
    }
  ],
  "results": [
    {
      "id": "result-id",
      "claim_id": "claim-id",
      "verifier": "recency",
      "verdict": "FAIL",
      "confidence": 0.85,
      "evidence": ["한국은행 자료에 따르면 기준금리는 2.50%로 유지되고 있다."],
      "reasoning": "time_indicators=['최근']\njudgment=FAIL\nreason=최신 기준금리와 충돌합니다.",
      "sources": ["https://www.bok.or.kr/..."],
      "metadata": {
        "search_profile": {
          "language": "ko",
          "country_hint": "KR",
          "region_terms": ["한국"],
          "institution_terms": [],
          "time_terms": ["최근"]
        },
        "expanded_queries": ["한국 최근 2025년 한국 기준금리 site:bok.or.kr"],
        "official_retry": true,
        "filtered_domains": ["blog.naver.com", "www.youtube.com"],
        "ranking": []
      },
      "verified_at": "2026-05-11T05:50:48.904710Z",
      "parent_result_id": null
    }
  ],
  "final_grade": "확인 필요",
  "final_report": {
    "final_grade": "확인 필요",
    "summary": "명백한 오류 또는 검증 불가 항목이 있어 확인이 필요합니다.",
    "issues": [
      {
        "node": "최신성",
        "original_text": "한국의 기준금리는 3.5%로 유지되고 있다",
        "judgment": "FAIL",
        "reason": "최신 한국은행 자료와 충돌합니다.",
        "suggestion": "최신 자료 기준으로 표현과 시점을 갱신하세요."
      }
    ]
  }
}
```

## 노드별 구현

현재 그래프는 `src/ai_backend/graph/builder.py`에서 다음 순서로 조립됩니다.

```text
START
  -> preprocess
  -> fact_check ┐
  -> source_check ├─> aggregate
  -> recency_check│
  -> numeric_check┘
  -> END
```

`preprocess`가 원문 텍스트를 claim 목록으로 바꾸면, 네 검증 노드는 같은 `claims`를 입력으로 받아 자기 타입에 해당하는 claim만 골라 처리합니다. LangGraph 레벨에서는 네 검증 노드가 fan-out/fan-in 구조로 연결되어 있고, 각 검증 노드 내부에서도 claim 단위로 `ThreadPoolExecutor`를 사용해 병렬 처리합니다.

전체 처리 흐름은 다음과 같습니다.

```text
POST /verify
  -> VerifyRequest 검증
  -> BackgroundTasks에 run_verify_job 등록
  -> GraphState 초기화
       raw_text = request.text
       document_id = request.document_id
       document_citations = request.document_citations
  -> verification_graph.ainvoke(...)
  -> preprocess_node
       text -> LLM claim extraction -> Claim[]
  -> fact_check_node / source_check_node / recency_check_node / numeric_check_node
       Claim[] -> 타입별 필터링 -> 검색/LLM/출처 fetch -> VerificationResult[]
  -> aggregate_node
       VerificationResult[] -> final_grade + final_report
  -> save_verify_job_result(...)
```

### 현재 구현 상세 흐름

#### `preprocess_node`: text를 claim으로 바꾸는 단계

```text
GraphState.raw_text
  -> CLAIM_EXTRACTION_SYSTEM / CLAIM_EXTRACTION_USER
  -> get_llm("extraction").invoke(...)
  -> parse_json_with_fallback(...)
  -> _validate_and_build_claim(...)
  -> make_claim(...)
  -> GraphState.claims
```

이 노드는 원문 전체를 LLM에 보내 검증 가능한 문장 단위 claim 목록을 JSON으로 받습니다. 응답이 JSON 그대로 오지 않아도 `parse_json_with_fallback()`이 코드블록/주변 텍스트를 어느 정도 복구해서 파싱합니다. 파싱 결과가 list가 아니거나 복구가 실패하면 빈 claim 목록을 반환합니다.

각 claim 후보는 `_validate_and_build_claim()`에서 다시 걸러집니다. `text`가 비어 있거나 `type`이 list가 아니면 버리고, `type` 값 중 `FACT`, `NUMERIC`, `SOURCE`, `RECENCY`에 없는 값은 제거합니다. 한두 항목이 잘못돼도 전체를 버리지 않고 유효한 항목만 남깁니다.

출처 타입도 여기서 보정합니다. LLM이 `SOURCE` 타입을 붙였지만 citations가 없으면 `SOURCE`를 제거하고, citations가 있는데 `SOURCE`가 빠져 있으면 `SOURCE`를 추가합니다. 최종 Claim 객체의 UUID, `content_hash`, `document_id`, `extracted_at` 같은 메타 필드는 LLM이 아니라 `make_claim()`이 코드에서 부여합니다.

#### `fact_check_node`: 일반 사실관계 검증

```text
Claim[] 중 "FACT" claim만 필터링
  -> get_llm("verification")
  -> get_search_client()
  -> claim별 ThreadPoolExecutor 병렬 처리
  -> GraphState.fact_results
```

Tavily provider일 때의 claim 1개 처리 순서:

```text
claim
  -> FACT_QUERY_USER로 LLM 검색 계획 생성
  -> search_queries 추출, 없으면 claim.text 사용
  -> search_verification_evidence(...)
       -> Tavily search
       -> URL/content 기준 중복 제거
       -> SearchProfile 생성
       -> 공식 도메인/한국어/저품질 도메인 기준 rerank
       -> 필요 시 site: 공식 도메인 보강 검색
  -> format_evidence(...)
  -> FACT_JUDGMENT_USER로 LLM 판정
  -> normalize_judgment(...)
  -> make_verification_result(verifier="fact")
```

OpenAI provider일 때는 검색 쿼리 생성 LLM과 별도 judgment LLM을 나누지 않고 `OpenAIWebSearchClient.verify_claim_once()`를 호출합니다. 이 함수가 OpenAI Responses API의 `web_search`를 한 번 사용하고, 반환 JSON의 `"fact"` section을 `VerificationResult`로 바꿉니다.

검색 client 초기화 실패나 개별 claim 예외는 전체 job 실패로 올리지 않고 해당 claim의 `UNVERIFIABLE` 결과로 남깁니다.

#### `numeric_check_node`: 수치/비율/비교 검증

```text
Claim[] 중 "NUMERIC" claim만 필터링
  -> get_llm("verification")
  -> get_search_client()
  -> claim별 ThreadPoolExecutor 병렬 처리
  -> GraphState.numeric_results
```

Tavily provider에서는 fact 노드와 같은 2단계 구조를 씁니다. `NUMERIC_QUERY_USER`로 수치 검증용 검색어를 만들고, `search_verification_evidence()`가 검색/중복 제거/공식 도메인 보강/rerank를 수행합니다. 이후 `NUMERIC_JUDGMENT_USER`가 evidence를 보고 claim의 숫자, 단위, 기준연도, 비교 표현이 맞는지 판정합니다.

OpenAI provider에서는 `verify_claim_once()` 결과의 `"numeric"` section을 사용합니다. 결과 metadata에는 `numeric_type`, `suggestion`, `search_queries`, 검색 정책 정보가 들어갑니다.

현재 구현은 수치 자체의 일치 여부에 집중합니다. 그래서 “현재 3.5%” 같은 claim에서 과거 자료의 3.5%를 근거로 numeric은 `PASS`를 낼 수 있고, 최신 시점 불일치는 recency가 `FAIL`로 잡을 수 있습니다. 이런 경우 aggregate에서 같은 claim의 노드 간 판정 충돌로 묶입니다.

#### `recency_check_node`: 최신성/시점 표현 검증

```text
Claim[] 중 "RECENCY" claim만 필터링
  -> get_llm("verification")
  -> get_search_client()
  -> claim별 ThreadPoolExecutor 병렬 처리
  -> GraphState.recency_results
```

Tavily provider에서는 먼저 `build_search_profile(claim)`로 claim의 언어, 한국 지역명, 기관명, 연도/최근/현재 같은 시간 표현을 추출합니다. 한국어 + 한국 대상 claim이면 한국 공식/공공 도메인과 한국어 evidence에 가산점을 주고, 유튜브/나무위키/위키/블로그/티스토리는 낮은 점수를 줍니다.

그 다음 `RECENCY_QUERY_USER`로 LLM 검색 계획을 만들고, 검색어가 없으면 `fallback_queries(..., latest=True)`로 최신성 중심 fallback 검색어를 생성합니다. 검색에는 기본 `recent_days=730`이 `days`로 전달됩니다. 수집된 evidence는 `RECENCY_JUDGMENT_USER`에 들어가며, LLM은 claim의 “최근/현재/올해/목표연도” 표현이 최신 근거와 맞는지 판정합니다.

OpenAI provider에서는 `verify_claim_once(..., recent_days=730)`를 호출하고 `"recency"` section을 사용합니다. 결과 metadata에는 `time_indicators`, `recency_profile`, `expanded_queries`, `official_retry`, `filtered_domains`, `ranking` 등이 남습니다.

#### `source_check_node`: 인용/출처 왜곡 검증

```text
Claim[] 중 "SOURCE" claim만 필터링
  -> get_llm("verification")
  -> claim별 ThreadPoolExecutor 병렬 처리
  -> claim.citations + GraphState.document_citations 병합
  -> citation별 fetch_source_context(...)
  -> SOURCE_CHECK_USER로 LLM 판정
  -> GraphState.source_results
```

`_claim_sources()`는 claim 내부 citations와 문서 전체 `document_citations`를 합친 뒤 `citation_type:raw_text` 키로 중복을 제거합니다. 검증할 citation이 없으면 해당 claim은 `UNVERIFIABLE`입니다.

URL citation은 `httpx.Client(follow_redirects=True, timeout=10.0)`로 직접 GET 요청을 합니다. 정상 응답이면 HTML 텍스트를 공백 기준으로 압축해 앞 4000자를 source context로 쓰고, HTTP 오류나 400 이상 status는 `ERROR (...)` 접근성으로 기록합니다. `reference` citation은 외부 fetch 없이 raw text 자체를 context로 넘기며 `accessibility="REFERENCE"`로 표시합니다.

LLM 응답에서는 `distortion_check`를 읽어 `PASS`, `WARNING`, `FAIL`로 정규화합니다. 알 수 없는 값은 `WARNING`으로 처리합니다. URL 접근 실패나 fetch 예외는 사실관계 실패가 아니라 source 검증의 `UNVERIFIABLE` 또는 접근성 issue로 남습니다.

#### `aggregate_node`: 노드별 결과를 사용자용 판정으로 묶는 단계

```text
fact_results + source_results + recency_results + numeric_results
  -> 결과가 없으면 "확인 필요"
  -> 기본은 _heuristic_aggregate(...)
  -> claim_id별 결과 grouping
  -> PASS가 아닌 결과를 issue 후보로 선택
  -> claim별 대표 result 선택
  -> final_grade / final_report 생성
```

기본 실행 경로에서는 LLM을 호출하지 않습니다. `aggregate_node(state)`처럼 호출하면 rule-based `_heuristic_aggregate()`가 최종 등급과 issue를 만듭니다. 테스트나 디버그에서 `llm`을 명시 주입하면 `AGGREGATE_SYSTEM`, `AGGREGATE_USER`를 사용한 LLM aggregate 경로를 시도하고, 실패하면 heuristic fallback을 사용합니다.

최종 등급은 전체 `VerificationResult` 중 하나라도 `FAIL` 또는 `UNVERIFIABLE`이면 `확인 필요`, 그렇지 않고 `WARNING`이 있으면 `주의`, 모두 `PASS`면 `통과`입니다. `final_report.issues`는 claim 단위로 묶이며, 대표 판정 우선순위는 `FAIL > UNVERIFIABLE > WARNING > PASS`입니다. 같은 우선순위 안에서는 confidence가 높은 결과가 대표 issue의 기준이 됩니다.

API 응답의 `results`에는 모든 노드별 원본 결과가 유지되고, `final_report.issues`에는 사용자에게 보여줄 대표 문제만 남습니다.

### 1. `preprocess_node`

파일: `src/ai_backend/graph/nodes/preprocess.py`

역할:

- 원문 `raw_text`에서 검증 가능한 claim을 추출합니다.
- 각 claim에 `FACT`, `NUMERIC`, `SOURCE`, `RECENCY` 타입을 하나 이상 부여합니다.
- 주변 문맥, citation, hash, 추출 시각을 채웁니다.

Claim type:

```text
FACT    일반 사실관계
NUMERIC 수치, 비율, 비교, 통계
SOURCE  출처 인용, URL, 참고문헌
RECENCY 최신성, 현재성, 최근/올해/목표연도 표현
```

빈 텍스트거나 LLM JSON 파싱에 실패하면 claim 목록을 비우거나 유효한 항목만 남깁니다.

### 2. `fact_check_node`

파일: `src/ai_backend/graph/nodes/fact_check.py`

대상:

```text
"FACT" in claim.type
```

역할:

- claim의 일반 사실관계를 검색합니다.
- LLM이 검색 쿼리를 설계합니다.
- Tavily 또는 OpenAI web_search provider를 통해 evidence를 수집합니다.
- evidence와 claim을 대조해 `PASS`, `WARNING`, `FAIL`을 냅니다.

Tavily provider 사용 시:

```text
query plan LLM
  -> common search policy
  -> evidence formatting
  -> judgment LLM
```

OpenAI provider 사용 시:

```text
OpenAIWebSearchClient.verify_claim_once()
```

같은 claim이 `FACT`, `NUMERIC`, `RECENCY`에 동시에 걸릴 수 있으므로 OpenAI provider에는 claim 단위 cache가 있습니다.

### 3. `numeric_check_node`

파일: `src/ai_backend/graph/nodes/numeric_check.py`

대상:

```text
"NUMERIC" in claim.type
```

역할:

- 숫자, 비율, 순위, 기간, 비교 표현을 검증합니다.
- 근거 수치와 claim 수치가 일치하는지 봅니다.
- 가능하면 계산/비교 결과를 reasoning에 남깁니다.

예:

```text
claim: 한국의 기준금리는 3.5%로 유지되고 있다
numeric evidence: 2023~2024년에는 3.50% 유지
recency evidence: 2025~2026년에는 2.50%
```

현재 numeric 노드는 시점 표현이 있는 claim에서 과거 수치를 근거로 `PASS`를 낼 수 있습니다. 이 경우 aggregate 단계에서 recency `FAIL`과 충돌로 묶입니다. 향후 numeric/fact 시점 민감도 강화가 필요합니다.

### 4. `recency_check_node`

파일: `src/ai_backend/graph/nodes/recency_check.py`

대상:

```text
"RECENCY" in claim.type
```

역할:

- `최근`, `현재`, `올해`, `2030년까지` 같은 시점/목표연도 표현을 확인합니다.
- 최신 통계, 법령 개정, 정책 변경, 후속 보도와 claim을 비교합니다.
- claim의 언어/지역/기관/연도를 보존해 검색합니다.

예:

```text
claim: 수도권 미세먼지 배출량을 2030년까지 20% 줄일 계획이다
profile:
  language: ko
  country_hint: KR
  region_terms: ["수도권"]
  institution_terms: ["환경부"]
  time_terms: ["2030년"]
official retry:
  site:me.go.kr
  site:korea.kr
```

한국어 + 한국 대상 claim이면 한국어 evidence와 한국 공식/공공기관 도메인을 우선합니다. 해외 사례는 claim이 해외 일반 동향을 말하는 경우가 아니면 낮은 우선순위로 밀립니다.

### 5. `source_check_node`

파일: `src/ai_backend/graph/nodes/source_check.py`

대상:

```text
"SOURCE" in claim.type
```

역할:

- claim citation과 문서 전체 `document_citations`를 모읍니다.
- URL 접근 가능성을 확인합니다.
- URL/문헌 내용과 claim이 왜곡 없이 맞는지 LLM이 판단합니다.

현재 주의점:

- `https://www.me.go.kr` 같은 루트 URL은 상세 보도자료 URL이 아니므로 강한 검증 근거로 쓰기 어렵습니다.
- `"환경부 보도자료"`처럼 제목/발행일/상세 URL이 없는 reference는 보통 `WARNING`에 가깝습니다.
- URL 접근 실패만으로 claim 자체를 무조건 `FAIL`로 볼지, source-only issue로 분리할지는 정책 정리가 남아 있습니다.

### 6. `aggregate_node`

파일: `src/ai_backend/graph/nodes/aggregate.py`

역할:

- 네 검증 노드 결과를 모아 최종 등급과 사용자용 issue를 생성합니다.
- 기본 실행 경로에서는 LLM을 호출하지 않고 rule-based로 처리합니다.
- 테스트/디버그 목적으로 `llm`을 명시 주입하면 기존 LLM aggregate 경로도 사용할 수 있습니다.

최종 등급 규칙:

```text
하나라도 FAIL 또는 UNVERIFIABLE -> 확인 필요
FAIL/UNVERIFIABLE은 없고 WARNING 있음 -> 주의
모두 PASS -> 통과
```

같은 claim에 여러 노드 결과가 있으면 claim 단위로 묶습니다.

대표 판정 우선순위:

```text
FAIL > UNVERIFIABLE > WARNING > PASS
```

예:

```text
claim: 한국의 기준금리는 3.5%로 유지되고 있다
fact:    WARNING
numeric: PASS
recency: FAIL
```

최종 issue:

```json
{
  "node": "사실관계, 최신성",
  "original_text": "한국의 기준금리는 3.5%로 유지되고 있다",
  "judgment": "FAIL",
  "reason": "노드 간 판정 충돌 감지. 노드별 판정: 사실관계=WARNING, 최신성=FAIL, 수치=PASS. ...",
  "suggestion": "최신 자료 기준으로 표현과 시점을 갱신하세요."
}
```

즉, API 응답의 `results`에는 모든 노드별 원본 결과가 유지되고, `final_report.issues`에는 사용자에게 보여줄 대표 문제만 남습니다.

## 공통 검색 정책

파일:

```text
src/ai_backend/core/search.py
src/ai_backend/core/search_policy.py
src/ai_backend/core/verification.py
```

검색 provider:

```dotenv
SEARCH_PROVIDER=tavily
```

- Tavily 검색 결과를 수집하고, 판단은 LLM이 수행합니다.
- 현재 기본 추천 provider입니다.

```dotenv
SEARCH_PROVIDER=openai
```

- OpenAI Responses API `web_search`를 사용합니다.
- `verify_claim_once()`로 같은 claim의 FACT/NUMERIC/RECENCY 중복 web_search를 줄입니다.

공통 검색 정책은 `fact`, `numeric`, `recency`에서 함께 사용합니다.

`SearchProfile`:

```json
{
  "language": "ko",
  "country_hint": "KR",
  "region_terms": ["수도권"],
  "institution_terms": ["환경부"],
  "time_terms": ["2030년"],
  "official_domains": ["go.kr", "korea.kr", "me.go.kr"]
}
```

정책:

- 한국어 + 한국 대상 claim은 한국어 결과와 한국 공식 도메인을 우선합니다.
- 기준금리 claim은 `bok.or.kr`, `korea.kr` 보강 쿼리를 생성합니다.
- GDP/인구/출산율 claim은 `kostat.go.kr`, `korea.kr` 보강 쿼리를 생성합니다.
- 환경/미세먼지 claim은 `me.go.kr`, `korea.kr` 보강 쿼리를 생성합니다.
- 유튜브, 나무위키, 위키, 네이버 블로그, 티스토리는 낮은 점수를 받습니다.
- 공식/국내 후보가 부족하면 `site:` 보강 검색을 추가 실행합니다.

metadata 예:

```json
{
  "search_profile": {
    "language": "ko",
    "country_hint": "KR",
    "region_terms": ["한국"],
    "institution_terms": [],
    "time_terms": ["최근"],
    "official_domains": ["go.kr", "korea.kr", "bok.or.kr"]
  },
  "expanded_queries": [
    "한국 최근 2025년 한국 기준금리 site:bok.or.kr",
    "한국 최근 2025년 한국 기준금리 site:korea.kr"
  ],
  "official_retry": true,
  "filtered_domains": ["blog.naver.com", "www.youtube.com", "namu.wiki"],
  "ranking": [
    {
      "url": "https://www.bok.or.kr/...",
      "score": 8.5,
      "reasons": ["official_domain", "korean_text", "korea_signal"]
    }
  ]
}
```

## DB 연동 계획

Django 모델 기준 예상 매핑:

```text
ProjectFile.id          -> VerifyRequest.project_file_id
ProjectFile.content     -> VerifyRequest.text
ProjectFile.topic       -> VerifyRequest.topic
FileReviewItem          -> AI 결과 저장 대상
```

`FinalIssue` -> `FileReviewItem`:

```text
project_file_id         <- VerifyResponse.project_file_id
highlighted_text        <- FinalIssue.original_text
problem                 <- FinalIssue.reason
suggestion              <- FinalIssue.suggestion
order                   <- issue index
```

Django 쪽 권장 AI 상태 필드:

```python
class ProjectFile(models.Model):
    AI_STATUS_CHOICES = [
        ("not_requested", "AI 검증 전"),
        ("queued", "AI 검증 대기"),
        ("processing", "AI 검증 중"),
        ("completed", "AI 검증 완료"),
        ("failed", "AI 검증 실패"),
    ]

    ai_status = models.CharField(
        max_length=20,
        choices=AI_STATUS_CHOICES,
        default="not_requested",
    )
    ai_requested_at = models.DateTimeField(null=True, blank=True)
    ai_completed_at = models.DateTimeField(null=True, blank=True)
    ai_error = models.TextField(blank=True)
```

주의:

- Django의 기존 `ProjectFile.status`는 `pending / verified / rejected`로 팀 검토 상태에 가깝습니다.
- AI 처리 상태와 섞지 않는 것이 안전합니다.
- DB migration은 Django가 소유합니다.
- AI 백엔드는 migration을 만들지 않고, `storage.py` 경계 안에서 정해진 테이블만 업데이트합니다.

## 배포 방식

권장 배포는 Django와 AI 백엔드를 별도 서비스로 띄우고 같은 운영 DB를 바라보게 하는 방식입니다.

```text
capstone_demo          Django main backend
ai-backend             FastAPI AI verification service
PostgreSQL             shared DB
```

Docker Compose 예:

```text
nginx
django
ai-backend
postgres 또는 managed PostgreSQL
```

외부 노출:

```text
https://your-domain.com        -> Django
http://ai-backend:8000         -> 내부 네트워크 전용
```

Django 환경 변수:

```dotenv
DATABASE_URL=postgresql://...
AI_BACKEND_URL=http://ai-backend:8000
```

AI 백엔드 환경 변수:

```dotenv
DATABASE_URL=postgresql://...
OPENAI_API_KEY=...
TAVILY_API_KEY=...
SEARCH_PROVIDER=tavily
```

## 로컬 실행

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements-dev.txt
pip install -e .
uvicorn ai_backend.main:app --reload --host 0.0.0.0 --port 8000
```

Git Bash:

```bash
bash scripts/run_dev.sh
```

접속:

```text
http://127.0.0.1:8000/health
http://127.0.0.1:8000/docs
http://127.0.0.1:8000/redoc
```

## 환경 변수

`.env` 예:

```dotenv
APP_ENV=development
LOG_LEVEL=INFO

OPENAI_API_KEY=
TAVILY_API_KEY=
SEARCH_PROVIDER=tavily

LLM_MODEL_EXTRACTION=gpt-4o-mini
LLM_MODEL_VERIFICATION=gpt-4o-mini
LLM_MODEL_AGGREGATION=gpt-4o-mini
LLM_TEMPERATURE=0
LLM_REQUEST_TIMEOUT=60

OPENAI_SEARCH_MODEL=gpt-5-mini
TAVILY_MAX_RESULTS=5
```

## 주요 파일

```text
src/ai_backend/main.py                      FastAPI 앱 진입점
src/ai_backend/api/routes/verify.py         Verify 접수/상태/결과 API
src/ai_backend/api/schemas.py               API 요청/응답 스키마
src/ai_backend/storage.py                   공유 DB 저장 경계

src/ai_backend/graph/builder.py             LangGraph 조립
src/ai_backend/graph/state.py               GraphState / TypedDict 타입
src/ai_backend/graph/nodes/preprocess.py    Claim 추출 노드
src/ai_backend/graph/nodes/fact_check.py    사실관계 검증 노드
src/ai_backend/graph/nodes/source_check.py  출처 검증 노드
src/ai_backend/graph/nodes/recency_check.py 최신성 검증 노드
src/ai_backend/graph/nodes/numeric_check.py 수치 검증 노드
src/ai_backend/graph/nodes/aggregate.py     최종 종합 노드

src/ai_backend/graph/prompts/               LLM 프롬프트
src/ai_backend/core/llm.py                  LLM 클라이언트 생성
src/ai_backend/core/search.py               Tavily/OpenAI 검색 클라이언트
src/ai_backend/core/search_policy.py        공통 검색 정책
src/ai_backend/core/verification.py         검증 공통 helper
src/ai_backend/core/ids.py                  ID/hash/time factory
src/ai_backend/models/claim.py              Pydantic 모델
```

## 남은 작업

- `storage.py`를 실제 공유 DB 저장으로 구현
- `source_check`에서 루트 URL/reference citation 처리 정책 정리
- `document_citations`가 있을 때 SOURCE claim이 없어도 출처 검증할지 결정
- numeric/fact 노드의 시점 민감도 개선
- 공식 검색 정책 allowlist를 설정 파일로 분리
- evidence 개수/길이 제한으로 LLM 입력 크기 제어
