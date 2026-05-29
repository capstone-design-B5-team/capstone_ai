# AI Backend

FastAPI + LangGraph 기반 문서 검증 백엔드입니다.

이 프로젝트는 두 가지 흐름을 동시에 지원합니다.

1. **서비스 흐름**: Django/프론트가 쓰는 기존 검증 API 응답을 유지합니다.
2. **AVeriTeC 테스트 흐름**: claim별 QA evidence를 만들고 `label`, `questions`, `justification` 형식의 `predictions.json`을 생성합니다.

즉, 각 검증 노드는 기존 서비스용 `VerificationResult`와 AVeriTeC 평가용 `Question`을 함께 생성합니다.

## API

| Method | Path | 설명 |
| --- | --- | --- |
| `POST` | `/verify` | 문서 검증 job 접수. 즉시 `202 Accepted` 반환 |
| `GET` | `/verify/{job_id}/status` | 검증 job 상태 조회 (`accepted`, `processing`, `completed`, `failed`) |
| `GET` | `/verify/{job_id}/result` | 검증 완료 결과 조회. 기존 프론트 호환 `VerifyResponse` 반환 |
| `GET` | `/health` | 서버 health check |

로컬 기본 URL:

```text
http://127.0.0.1:8001
```

Swagger UI:

```text
http://127.0.0.1:8001/docs
```

## 전체 파이프라인

```text
raw_text
  |
  v
[preprocess]
  - 원문에서 검증 가능한 sub-claim 추출
  - 각 claim에 FACT / NUMERIC / SOURCE / RECENCY 타입 부여
  |
  v
병렬 검증 노드
  - fact_check
  - numeric_check
  - recency_check
  - source_check
  |
  | 각 노드는 두 종류의 출력을 함께 생성
  | - 기존 서비스용: *_results
  | - AVeriTeC용: questions
  v
[aggregate]
  - 기존 서비스용 final_grade / final_report 생성
  - AVeriTeC용 label / justification 생성
```

## GraphState

`GraphState`는 LangGraph 전체에서 공유되는 상태입니다. 정의는 `src/ai_backend/graph/state.py`에 있습니다.

```json
{
  "raw_text": "검증할 원문 텍스트",
  "document_id": "문서 ID",
  "run_mode": "service | averitec",
  "document_citations": [
    {
      "raw_text": "https://example.com/document-source",
      "citation_type": "url"
    }
  ],
  "claims": [
    {
      "id": "claim UUID",
      "content_hash": "12자리 content hash",
      "document_id": "문서 ID",
      "text": "추출된 sub-claim 원문",
      "type": ["FACT", "NUMERIC", "SOURCE", "RECENCY"],
      "context": "claim 주변 문맥",
      "citations": [
        {
          "raw_text": "https://example.com/claim-source",
          "citation_type": "url"
        }
      ],
      "extracted_at": "ISO datetime",
      "parent_claim_id": null
    }
  ],
  "questions": [
    {
      "question": "검증을 위한 질문",
      "answers": [
        {
          "answer": "검색 결과 기반 답변",
          "answer_type": "Abstractive",
          "source_url": "https://example.com/source"
        }
      ]
    }
  ],
  "fact_results": [
    {
      "id": "result UUID",
      "claim_id": "claim UUID",
      "verifier": "fact",
      "verdict": "PASS",
      "confidence": 0.85,
      "evidence": ["검색 evidence 요약"],
      "reasoning": "판정 근거",
      "sources": ["https://example.com/source"],
      "metadata": {},
      "verified_at": "ISO datetime",
      "parent_result_id": null
    }
  ],
  "source_results": [],
  "recency_results": [],
  "numeric_results": [],
  "label": "Supported",
  "justification": "AVeriTeC label 판정 근거",
  "final_grade": "통과",
  "final_report": {
    "final_grade": "통과",
    "summary": "기존 서비스용 최종 요약",
    "issues": [
      {
        "node": "fact",
        "highlighted_text": "문제가 된 claim 원문",
        "judgment": "WARNING",
        "problem": "문제 설명",
        "suggestion": "수정 제안"
      }
    ]
  }
}
```

`run_mode`는 같은 graph를 서비스용과 테스트용으로 나누는 실행 모드입니다.

```text
service   기존 API/프론트용 모드. 검증 노드는 questions를 반환하지 않고, aggregate도 AVeriTeC label 예측을 건너뜁니다.
averitec  테스트/평가용 모드. 검증 노드는 questions를 누적하고, aggregate는 questions 기반 label/justification을 생성합니다.
```

### Claim

```json
{
  "id": "claim UUID",
  "content_hash": "12자리 content hash",
  "document_id": "문서 ID",
  "text": "추출된 sub-claim 원문",
  "type": ["FACT", "NUMERIC", "SOURCE", "RECENCY"],
  "context": "claim 주변 문맥",
  "citations": [
    {
      "raw_text": "https://example.com/source",
      "citation_type": "url"
    },
    {
      "raw_text": "Smith, J. (2021). Paper title. Journal.",
      "citation_type": "reference"
    }
  ],
  "extracted_at": "ISO datetime",
  "parent_claim_id": null
}
```

### VerificationResult

기존 서비스/프론트에서 문제 항목과 검증 근거를 보여주기 위한 결과입니다.

```json
{
  "id": "result UUID",
  "claim_id": "claim UUID",
  "verifier": "fact",
  "verdict": "PASS | WARNING | FAIL | UNVERIFIABLE",
  "confidence": 0.85,
  "evidence": ["검색 evidence 요약"],
  "reasoning": "판정 근거",
  "sources": ["https://example.com/source"],
  "metadata": {
    "node_result": {},
    "search_queries": ["검색 쿼리"],
    "raw_judgment": "PASS"
  },
  "verified_at": "ISO datetime",
  "parent_result_id": null
}
```

### Question

AVeriTeC 평가용 QA evidence입니다. `predictions.json`에 들어가는 핵심 필드입니다.

```json
{
  "question": "검증을 위한 질문",
  "answers": [
    {
      "answer": "검색 결과 기반 답변",
      "answer_type": "Abstractive",
      "source_url": "https://example.com/source"
    }
  ]
}
```

### Answer

```json
{
  "answer": "검색 결과 기반 답변",
  "answer_type": "Abstractive | Extractive | Boolean | Unanswerable",
  "source_url": "https://example.com/source"
}
```

### Label

AVeriTeC label은 다음 네 값만 사용합니다.

```text
Supported
Refuted
Not Enough Evidence
Conflicting Evidence/Cherrypicking
```

## 노드별 입출력

### preprocess

```json
input:
{
  "raw_text": "AVeriTeC claim 원문 또는 서비스에서 전달된 문서 본문",
  "document_id": "문서 ID",
  "document_citations": [
    {
      "raw_text": "https://example.com/source",
      "citation_type": "url"
    }
  ],
  "claims": [],
  "questions": [],
  "fact_results": [],
  "source_results": [],
  "recency_results": [],
  "numeric_results": [],
  "label": "Not Enough Evidence",
  "justification": "",
  "final_grade": "확인 필요",
  "final_report": {
    "final_grade": "확인 필요",
    "summary": "",
    "issues": []
  }
}

output:
{
  "claims": [
    {
      "id": "claim UUID",
      "content_hash": "12자리 content hash",
      "document_id": "문서 ID",
      "text": "추출된 sub-claim 원문",
      "type": ["FACT", "NUMERIC"],
      "context": "claim 주변 문맥",
      "citations": [
        {
          "raw_text": "https://example.com/source",
          "citation_type": "url"
        }
      ],
      "extracted_at": "ISO datetime",
      "parent_claim_id": null
    }
  ]
}
```

### fact_check

```json
input:
{
  "claims": [
    {
      "id": "claim UUID",
      "content_hash": "12자리 content hash",
      "document_id": "문서 ID",
      "text": "사실관계 검증 대상 claim",
      "type": ["FACT"],
      "context": "claim 주변 문맥",
      "citations": [],
      "extracted_at": "ISO datetime",
      "parent_claim_id": null
    }
  ]
}

output:
{
  "fact_results": [
    {
      "id": "result UUID",
      "claim_id": "claim UUID",
      "verifier": "fact",
      "verdict": "PASS | WARNING | FAIL | UNVERIFIABLE",
      "confidence": 0.85,
      "evidence": ["검색 evidence 요약"],
      "reasoning": "판정 근거",
      "sources": ["https://example.com/source"],
      "metadata": {
        "node_result": {},
        "search_queries": ["검색 쿼리"],
        "raw_judgment": "PASS"
      },
      "verified_at": "ISO datetime",
      "parent_result_id": null
    }
  ],
  "questions": [
    {
      "question": "사실관계 검증을 위한 질문",
      "answers": [
        {
          "answer": "검색 결과 기반 답변",
          "answer_type": "Abstractive",
          "source_url": "https://example.com/source"
        }
      ]
    }
  ]
}
```

`questions`는 `run_mode="averitec"`일 때만 반환됩니다. `run_mode="service"`에서는 `fact_results`만 반환합니다.

### numeric_check

```json
input:
{
  "claims": [
    {
      "id": "claim UUID",
      "content_hash": "12자리 content hash",
      "document_id": "문서 ID",
      "text": "수치 검증 대상 claim",
      "type": ["NUMERIC"],
      "context": "claim 주변 문맥",
      "citations": [],
      "extracted_at": "ISO datetime",
      "parent_claim_id": null
    }
  ]
}

output:
{
  "numeric_results": [
    {
      "id": "result UUID",
      "claim_id": "claim UUID",
      "verifier": "numeric",
      "verdict": "PASS | WARNING | FAIL | UNVERIFIABLE",
      "confidence": 0.85,
      "evidence": ["검색 evidence 요약"],
      "reasoning": "수치, 비율, 비교 관계 판정 근거",
      "sources": ["https://example.com/source"],
      "metadata": {
        "node_result": {},
        "search_queries": ["검색 쿼리"],
        "raw_judgment": "PASS",
        "numeric_type": "Statistical | Comparative | Interval | Temporal | Unknown",
        "suggestion": ""
      },
      "verified_at": "ISO datetime",
      "parent_result_id": null
    }
  ],
  "questions": [
    {
      "question": "수치 검증을 위한 질문",
      "answers": [
        {
          "answer": "검색 결과 기반 답변",
          "answer_type": "Abstractive",
          "source_url": "https://example.com/source"
        }
      ]
    }
  ]
}
```

`questions`는 `run_mode="averitec"`일 때만 반환됩니다. `run_mode="service"`에서는 `numeric_results`만 반환합니다.

### recency_check

```json
input:
{
  "claims": [
    {
      "id": "claim UUID",
      "content_hash": "12자리 content hash",
      "document_id": "문서 ID",
      "text": "최신성 검증 대상 claim",
      "type": ["RECENCY"],
      "context": "claim 주변 문맥",
      "citations": [],
      "extracted_at": "ISO datetime",
      "parent_claim_id": null
    }
  ]
}

output:
{
  "recency_results": [
    {
      "id": "result UUID",
      "claim_id": "claim UUID",
      "verifier": "recency",
      "verdict": "PASS | WARNING | FAIL | UNVERIFIABLE",
      "confidence": 0.85,
      "evidence": ["최근 검색 evidence 요약"],
      "reasoning": "최신 자료와의 일치 또는 충돌 판정 근거",
      "sources": ["https://example.com/source"],
      "metadata": {
        "node_result": {},
        "search_queries": ["검색 쿼리"],
        "raw_judgment": "PASS",
        "time_indicators": ["2024", "현재"],
        "recency_profile": {}
      },
      "verified_at": "ISO datetime",
      "parent_result_id": null
    }
  ],
  "questions": [
    {
      "question": "최신성 검증을 위한 질문",
      "answers": [
        {
          "answer": "검색 결과 기반 답변",
          "answer_type": "Abstractive",
          "source_url": "https://example.com/source"
        }
      ]
    }
  ]
}
```

`questions`는 `run_mode="averitec"`일 때만 반환됩니다. `run_mode="service"`에서는 `recency_results`만 반환합니다.

### source_check

```json
input:
{
  "document_citations": [
    {
      "raw_text": "https://example.com/document-source",
      "citation_type": "url"
    }
  ],
  "claims": [
    {
      "id": "claim UUID",
      "content_hash": "12자리 content hash",
      "document_id": "문서 ID",
      "text": "출처 검증 대상 claim",
      "type": ["SOURCE"],
      "context": "claim 주변 문맥",
      "citations": [
        {
          "raw_text": "https://example.com/claim-source",
          "citation_type": "url"
        },
        {
          "raw_text": "Smith, J. (2021). Paper title. Journal.",
          "citation_type": "reference"
        }
      ],
      "extracted_at": "ISO datetime",
      "parent_claim_id": null
    }
  ]
}

output:
{
  "source_results": [
    {
      "id": "result UUID",
      "claim_id": "claim UUID",
      "verifier": "source",
      "verdict": "PASS | WARNING | FAIL | UNVERIFIABLE",
      "confidence": 0.85,
      "evidence": [
        "source=...",
        "credibility_trust=HIGH ..."
      ],
      "reasoning": "출처 접근 가능성, 신뢰도, 왜곡 여부 판정 근거",
      "sources": ["https://example.com/claim-source"],
      "metadata": {
        "node_result": {},
        "raw_judgment": "PASS",
        "accessibility": "OK (200)",
        "source": "https://example.com/claim-source",
        "credibility": {
          "trust_level": "HIGH | MEDIUM | UNKNOWN | LOW",
          "reason": "도메인 신뢰도 판단 근거",
          "is_whitelisted": true
        }
      },
      "verified_at": "ISO datetime",
      "parent_result_id": null
    }
  ],
  "questions": [
    {
      "question": "명시된 출처가 claim을 뒷받침하는지 확인하는 질문",
      "answers": [
        {
          "answer": "출처 본문 preview 또는 reference 내용",
          "answer_type": "Extractive",
          "source_url": "https://example.com/claim-source"
        }
      ]
    }
  ]
}
```

`questions`는 `run_mode="averitec"`일 때만 반환됩니다. `run_mode="service"`에서는 `source_results`만 반환합니다.

### aggregate

```json
input:
{
  "claims": [
    {
      "id": "claim UUID",
      "content_hash": "12자리 content hash",
      "document_id": "문서 ID",
      "text": "집계 대상 claim",
      "type": ["FACT", "NUMERIC"],
      "context": "claim 주변 문맥",
      "citations": [],
      "extracted_at": "ISO datetime",
      "parent_claim_id": null
    }
  ],
  "questions": [
    {
      "question": "검증을 위한 질문",
      "answers": [
        {
          "answer": "검색 결과 기반 답변",
          "answer_type": "Abstractive",
          "source_url": "https://example.com/source"
        }
      ]
    }
  ],
  "fact_results": [
    {
      "id": "result UUID",
      "claim_id": "claim UUID",
      "verifier": "fact",
      "verdict": "PASS | WARNING | FAIL | UNVERIFIABLE",
      "confidence": 0.85,
      "evidence": ["검색 evidence 요약"],
      "reasoning": "판정 근거",
      "sources": ["https://example.com/source"],
      "metadata": {},
      "verified_at": "ISO datetime",
      "parent_result_id": null
    }
  ],
  "source_results": [],
  "recency_results": [],
  "numeric_results": []
}

output:
{
  "label": "Supported | Refuted | Not Enough Evidence | Conflicting Evidence/Cherrypicking",
  "justification": "AVeriTeC label 판정 근거",
  "final_grade": "통과 | 주의 | 확인 필요",
  "final_report": {
    "final_grade": "통과 | 주의 | 확인 필요",
    "summary": "기존 서비스용 최종 요약",
    "issues": [
      {
        "node": "fact | source | recency | numeric 또는 표시용 노드명",
        "highlighted_text": "문제가 된 claim 원문",
        "judgment": "WARNING | FAIL | UNVERIFIABLE",
        "problem": "문제 설명",
        "suggestion": "수정 제안"
      }
    ]
  }
}
```

## 기존 서비스 모드

서버 실행:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements-dev.txt
pip install -e .
uvicorn ai_backend.main:app --reload --host 0.0.0.0 --port 8001
```

Git Bash 환경에서는:

```bash
bash scripts/run_dev.sh
```

요청 예시:

```http
POST /verify
Content-Type: application/json

{
  "project_file_id": 123,
  "request_id": "verify-project-file-123",
  "project_id": 1,
  "topic": "문서 제목",
  "text": "검증할 문서 본문",
  "document_citations": []
}
```

응답:

```json
{
  "job_id": "verify-project-file-123",
  "project_file_id": 123,
  "document_id": "123",
  "request_id": "verify-project-file-123",
  "status": "accepted"
}
```

결과 조회:

```http
GET /verify/{job_id}/result
```

기존 API 결과는 `VerifyResponse` 형식을 유지합니다.

```text
project_file_id
document_id
claims
results
final_grade
final_report
```

이 AI 백엔드는 `DATABASE_URL`로 MySQL 연결을 확인합니다. 다만 이 저장소의 `storage.py` 기준으로는 verify job 상태와 결과를 아직 프로세스 메모리의 dict에 보관합니다. Django 메인 백엔드가 `/verify/{job_id}/result` 응답을 받아 자체 DB에 저장하는 흐름이 있을 수 있지만, 이 repo 내부의 `save_verify_job_result()`는 현재 직접 DB insert/update를 수행하지 않습니다.

## AVeriTeC 테스트 모드

테스트 모드는 AVeriTeC dev/test JSON을 읽어서 claim별로 graph를 실행하고 `predictions.json`을 만듭니다.

실행 명령은 고정입니다.

```bash
bash scripts/run_test.sh
```

설정은 `scripts/run_test.sh` 상단에서 수정합니다.

```bash
INPUT_JSON="averitec_dev_gold.json"
OUTPUT_JSON="predictions.json"
START=0
LIMIT=5
```

### 테스트 모드 내부 동작

```text
AVeriTeC JSON list
  |
  | item["claim"]
  v
GraphState 초기화
  - raw_text = item["claim"]
  - document_id = index 또는 item id
  - run_mode = "averitec"
  - claims = []
  - questions = []
  - *_results = []
  - label = "Not Enough Evidence"
  - final_grade = "확인 필요"
  |
  v
verification_graph.ainvoke(...)
  |
  v
prediction 추출
  - label
  - questions
  - justification
  |
  v
predictions.json 저장
```

생성되는 `predictions.json` 형식:

```json
[
  {
    "label": "Refuted",
    "questions": [
      {
        "question": "검증을 위한 질문",
        "answers": [
          {
            "answer": "검색 결과 기반 답변",
            "answer_type": "Abstractive",
            "source_url": "https://example.com"
          }
        ]
      }
    ],
    "justification": "QA evidence를 바탕으로 한 최종 판정 근거"
  }
]
```

### AVeriTeC 평가 방식 요약

AVeriTeC는 label만 맞아도 점수를 주지 않습니다. 예측한 QA evidence가 gold QA와 충분히 유사해야 label 정답이 인정됩니다.

핵심 출력은 다음 세 필드입니다.

```text
label
questions
justification
```

이 프로젝트는 2024년 HU-METEOR 기반 평가 형식에 맞춰 `predictions.json`을 생성하는 것을 목표로 합니다.

## 환경 변수

`.env` 예시:

```dotenv
APP_ENV=development
LOG_LEVEL=INFO

OPENAI_API_KEY=
TAVILY_API_KEY=
SEARCH_PROVIDER=tavily
OPENAI_SEARCH_MODEL=gpt-5-mini

DATABASE_URL=mysql+aiomysql://user:password@localhost:3306/capstone_db

LANGCHAIN_TRACING_V2=false
LANGCHAIN_API_KEY=
LANGCHAIN_PROJECT=capstone-ai
```

## 주요 파일

```text
src/ai_backend/main.py                      FastAPI app 진입점
src/ai_backend/api/routes/verify.py         Verify 접수/상태/결과 API
src/ai_backend/api/schemas.py               API request/response schema
src/ai_backend/storage.py                   저장소 경계. 현재 job 상태/결과는 process memory에 보관
src/ai_backend/db.py                        MySQL async engine. 현재 startup 연결 확인에 사용

src/ai_backend/graph/builder.py             LangGraph 조립
src/ai_backend/graph/state.py               GraphState / TypedDict 정의
src/ai_backend/graph/nodes/preprocess.py    Claim 추출 노드
src/ai_backend/graph/nodes/fact_check.py    FACT 검증 + QA 생성
src/ai_backend/graph/nodes/source_check.py  SOURCE 검증 + QA 생성
src/ai_backend/graph/nodes/recency_check.py RECENCY 검증 + QA 생성
src/ai_backend/graph/nodes/numeric_check.py NUMERIC 검증 + QA 생성
src/ai_backend/graph/nodes/aggregate.py     서비스 리포트 + AVeriTeC label 집계

src/ai_backend/graph/prompts/               LLM prompts
src/ai_backend/core/llm.py                  LLM client factory
src/ai_backend/core/search.py               Tavily/OpenAI search client
src/ai_backend/core/search_policy.py        검색 결과 ranking / official source policy
src/ai_backend/core/verification.py         검증 공통 helper
src/ai_backend/core/ids.py                  ID/hash/time factory
src/ai_backend/models/claim.py              Pydantic models

scripts/run_dev.sh                          로컬 API 서버 실행
scripts/run_test.sh                         AVeriTeC 테스트 모드 실행 설정
scripts/run_averitec_predictions.py         predictions.json 생성 runner
```

## 현재 주의점

- 이 repo의 `storage.py`는 아직 직접 DB insert/update를 하지 않습니다. DB 연결은 `db.py`에서 확인하지만, job 상태/결과는 현재 process memory에 보관합니다.
- AVeriTeC QA 생성은 현재 1차 구현입니다. 검색 evidence를 QA 형식으로 변환하며, gold QA와의 유사도를 높이기 위한 질문 생성 프롬프트 고도화가 필요합니다.
- 테스트 모드는 실제 LLM/API 검색 호출을 수행하므로 `LIMIT`을 작게 두고 smoke test부터 실행하는 것이 좋습니다.
