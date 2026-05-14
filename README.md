# AI Backend

FastAPI 기반 AI 자료 검증 서버입니다. Django 메인 백엔드가 검증할 문서 텍스트를 넘기면, AI 백엔드는 요청을 접수하고 LangGraph 검증 파이프라인을 백그라운드에서 실행합니다.

## API 엔드포인트

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| `POST` | `/verify` | 문서 검증 요청 접수. 즉시 `202 Accepted` 반환 |
| `GET` | `/verify/{job_id}/status` | 검증 job 상태 조회 (`accepted` / `processing` / `completed` / `failed`) |
| `GET` | `/verify/{job_id}/result` | 검증 완료 결과 조회 (`VerifyResponse`) |
| `GET` | `/health` | 서버 헬스 체크 |

**베이스 URL** (로컬): `http://127.0.0.1:8001`

**Swagger UI**: `http://127.0.0.1:8001/docs`

## GraphState

LangGraph 파이프라인 전체에서 공유되는 상태 객체입니다.

```text
GraphState
├── raw_text            str                     검증할 원문 텍스트 (입력)
├── document_id         str                     문서 식별자
├── document_citations  Citation[]              문서 전체 출처 목록
├── claims              Claim[]                 preprocess 노드가 추출한 주장 목록
├── fact_results        VerificationResult[]    사실관계 검증 결과 (fan-in 누적)
├── source_results      VerificationResult[]    출처 검증 결과 (fan-in 누적)
├── recency_results     VerificationResult[]    최신성 검증 결과 (fan-in 누적)
├── numeric_results     VerificationResult[]    수치 검증 결과 (fan-in 누적)
├── final_grade         "통과"|"주의"|"확인 필요"  최종 등급
└── final_report        FinalReport             사용자용 최종 리포트
```

**Claim**

```text
id, content_hash, document_id
text          str              검증 대상 문장 (원문 그대로)
type          ClaimType[]      FACT | NUMERIC | SOURCE | RECENCY
context       str              원문 주변 문맥
citations     Citation[]       claim에 직접 붙은 출처
extracted_at  datetime
```

**VerificationResult**

```text
id, claim_id, verifier ("fact"|"source"|"recency"|"numeric")
verdict       PASS | WARNING | FAIL | UNVERIFIABLE
confidence    float (0.0~1.0)
evidence      str[]            검색/출처 evidence 요약
reasoning     str              판단 근거
sources       str[]            evidence URL
```

**FinalReport**

```text
final_grade   "통과"|"주의"|"확인 필요"
summary       str
issues[]
  ├── node              str      문제 노드명 (예: 사실관계, 최신성)
  ├── highlighted_text  str      문제가 된 원문
  ├── judgment          Verdict
  ├── problem           str      문제 설명
  └── suggestion        str      수정 제안
```

## 노드 입출력

| 노드 | 입력 (GraphState 필드) | 출력 (GraphState 업데이트) |
| --- | --- | --- |
| `preprocess` | `raw_text`, `document_id`, `document_citations` | `claims` |
| `fact_check` | `claims` 중 `FACT` 타입 | `fact_results` |
| `source_check` | `claims` 중 `SOURCE` 타입, `document_citations` | `source_results` |
| `recency_check` | `claims` 중 `RECENCY` 타입 | `recency_results` |
| `numeric_check` | `claims` 중 `NUMERIC` 타입 | `numeric_results` |
| `aggregate` | `claims`, `*_results` 전체 | `final_grade`, `final_report` |

## 로컬 실행

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements-dev.txt
pip install -e .
uvicorn ai_backend.main:app --reload --host 0.0.0.0 --port 8001
```

Git Bash:

```bash
bash scripts/run_dev.sh
```

접속:

```text
http://127.0.0.1:8001/health
http://127.0.0.1:8001/docs
http://127.0.0.1:8001/redoc
```

## 환경 변수

`.env` 예:

```dotenv
SEARCH_PROVIDER=tavily
TAVILY_API_KEY=
OPENAI_API_KEY=
OPENAI_SEARCH_MODEL=gpt-5-mini
DATABASE_URL=mysql+aiomysql://user:password@localhost:3306/capstone_db
```

## 주요 파일

```text
src/ai_backend/main.py                      FastAPI 앱 진입점
src/ai_backend/api/routes/verify.py         Verify 접수/상태/결과 API
src/ai_backend/api/schemas.py               API 요청/응답 스키마
src/ai_backend/storage.py                   공유 DB 저장 경계
src/ai_backend/db.py                        MySQL 연결 (SQLAlchemy async)

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
