# 배포 가이드

> 팀원이 배포된 서비스 확인하고 시연 준비하기 위한 문서.
> Azure 명령 외울 필요 없이 GitHub 페이지에서 버튼 클릭으로 끝.

---

## 30초 요약

1. GitHub Actions 탭 → **"Manage deployment (wake/sleep)"** → **Run workflow** → `wake` → Run
2. 90초 기다리기 → 결과 페이지에 URL 표시됨
3. 끝나면 `sleep` 으로 한 번 더 (또는 매일 02:00 KST 자동 sleep)

---

## 운영 URL

| 서비스 | URL | 비고 |
|---|---|---|
| **Demo (Django)** | https://capstone-demo.greenhill-c0ae3c47.koreacentral.azurecontainerapps.io | 회원/프로젝트/자료 |
| **AI (FastAPI)** | https://capstone-ai.greenhill-c0ae3c47.koreacentral.azurecontainerapps.io | LangGraph 검증 |

## 시드 계정

| ID | 비번 | 역할 |
|---|---|---|
| `minjun` | `test1234` | PM |
| `seoyeon` | `test1234` | 프론트 |
| `jiho` | `test1234` | 백엔드 |
| `yuna` | `test1234` | AI |

**프로젝트 코드: `247813`** ("AI 강의 추천 팀")

---

## 시스템 구성

```
[사용자 브라우저]
        │
        ▼
┌───────────────────────────────┐
│  capstone-demo (Django)       │   ← 회원, 팀, 자료 업로드, 리뷰
│  Container App                │
└──────┬─────────────┬──────────┘
       │             │
       │             └─→ Azure Blob (PDF 영구 저장)
       │
       │ HTTP /verify
       ▼
┌───────────────────────────────┐
│  capstone-ai (FastAPI)        │   ← LangGraph 검증 그래프
│  Container App                │      (fact, source, recency, numeric)
└───────────────────────────────┘
       │
       └─→ OpenAI / Tavily (외부 API)

[양쪽이 공유]
   - MySQL: 회원/프로젝트/자료 메타데이터
   - ACR: 컨테이너 이미지 저장소
```

### 왜 두 컨테이너로 분리했나

- **관심사 분리**: Django는 웹 UI/auth, FastAPI+LangGraph는 검증 워크플로우 전담
- **독립 배포**: 한쪽 코드만 바뀌어도 다른 쪽 영향 X
- **확장성**: AI 검증 부하 커지면 ai만 scale-out 가능
- **AVeriTeC 벤치마크 호환**: ai 그래프는 텍스트 입력만으로 검증 가능한 구조

---

## 배포 방식

### 자동 배포 흐름

```
[로컬에서 코드 수정]
        │
        │ git push origin deploy_test
        ▼
[GitHub Actions: Deploy 워크플로우 자동 실행]
        │
        │ ① Docker 이미지 빌드
        │ ② Azure Container Registry에 push
        │ ③ Azure Container App에 새 이미지 반영
        ▼
[새 revision 활성화 → 자동으로 트래픽 받음]
```

**소요 시간**: 약 3~5분

### 왜 `deploy_test` 브랜치 트리거인가

- `main`에 push해도 **자동 배포 안 됨** — 안전장치
- `deploy_test`는 명시적으로 "배포해도 됨"이라는 의도 표명
- 평소 개발은 본인 dev 브랜치 → `main` 머지하더라도 prod 영향 0
- 시연 전 안정화 끝나면 `main`도 자동 배포 트리거로 바꿀 예정

### 배포 상태 확인

GitHub Actions 탭에서 "Deploy capstone-{demo,ai} to Azure" 워크플로우 → 최근 run 확인. 실패 시 로그에서 단계별 에러 확인 가능.

---

## wake/sleep 운영 (비용 절감 방법론)

### 왜 자동화했나

- 발표일(2026-06-17) 까지 비용 최소화
- 팀원이 Azure CLI 없이도 시연 준비 가능
- GitHub 페이지에서 누구나 클릭으로 사용

### 동작 원리

**wake**:
1. MySQL Flexible Server `start` → 1~2분 소요
2. Container Apps `min-replicas=1`로 변경 → 컨테이너 활성화
3. 60초 대기 (워밍업)
4. `/accounts/login/`, `/health` 헬스체크

**sleep**:
1. Container Apps `min-replicas=0` → 트래픽 없으면 0으로 스케일다운
2. MySQL Flexible Server `stop` → 컴퓨팅 정지 (데이터 보존)

### 사용법 (단계별)

**1. GitHub 레포 페이지에서 상단 메뉴의 `Actions` 탭 클릭**

![Actions 탭 위치](docs/images/actions-tab.png)

> 탭 위치: `Code` `Issues` `Pull requests` `Agents` **`Actions`** `Projects` ...

**2. 좌측 사이드바에서 `Manage deployment (wake/sleep)` 워크플로우 선택**

**3. 우측 상단 `Run workflow ▾` 드롭다운 클릭 → 옵션 선택**

![Run workflow 드롭다운](docs/images/run-workflow.png)

| 항목 | 선택값 | 설명 |
|---|---|---|
| **Use workflow from** | `Branch: main` | 그대로 두기 (워크플로우 파일은 main에 있음) |
| **서버 켜기(wake) 또는 끄기(sleep)** | `wake` 또는 `sleep` | 처음엔 `wake` |

**4. 초록색 `Run workflow` 버튼 클릭**

**5. 페이지 새로고침 → 새 run이 목록 맨 위에 노란색(진행중) 으로 뜸**

**6. 해당 run 클릭 → 약 90초 후 완료되면 결과 페이지에 URL + 시드 계정 정보 자동 표시**

> 💡 **양쪽 레포 어느 쪽이든 사용 가능**: capstone_demo / capstone_ai 둘 다 같은 워크플로우 있음. 같은 Azure 리소스를 켜고 끔.

### 자동 sleep

매일 **02:00 KST** cron으로 자동 sleep. 새벽에 깜빡 켜둬도 비용 폭주 방지.

### 왜 main 브랜치에 워크플로우를 뒀나

GitHub Actions의 `workflow_dispatch`와 `schedule` 트리거는 **default branch(main)에 있는 워크플로우만** 인식. `deploy_test`에 두면 수동 실행 버튼이 안 보이고 cron도 안 돔.

→ `.github/workflows/manage.yml`만 main에 단독 commit. 다른 app 코드 영향 0.

---

## 로컬 개발 영향

배포용 변경사항은 **로컬 개발과 호환**되도록 설계됨.

### capstone_demo

| 변경 | 로컬 영향 |
|---|---|
| `requirements.txt` 패키지 추가 | `pip install -r requirements.txt` 한 번 다시. 새 패키지: `gunicorn`, `mysqlclient`, `whitenoise`, `django-storages[azure]` |
| `ALLOWED_HOSTS = env` | default `localhost,127.0.0.1` → 로컬에서 그대로 동작 |
| `DATABASES` env 기반 `.get()` | `.env` 있으면 로컬 MySQL 그대로 |
| WhiteNoise 미들웨어 | `DEBUG=True`면 Django 기본 static이 우선 → 사실상 비활성 |
| `STATIC_ROOT` 추가 | `runserver`에 영향 없음. `collectstatic` 시만 사용 |
| `STORAGES` Azure 조건부 | `AZURE_ACCOUNT_NAME` env 없으면 자동으로 로컬 FileSystemStorage |

### capstone_ai

`main.py` lifespan에서 `DATABASE_URL` 없으면 DB 연결 시도를 skip. 로컬에서도 동일하게 동작.

### 새 변경 따라잡기

```bash
git pull origin main
pip install -r requirements.txt
```

`.env` 파일은 본인 로컬용 그대로 유지하면 됨.

---

## 비용 정책

학생 구독($100 크레딧)을 효율적으로 사용하기 위해:

| 상태 | 일일 비용 |
|---|---|
| 모두 sleep | ~$0.17 (ACR Basic만) |
| MySQL만 ON, Container Apps min=0 | ~$0.6 |
| 모두 ON (min=1) | ~$1.5 |

기본 전략: **평소 sleep, 시연/확인 시만 wake**. 자동 sleep으로 깜빡 켜둬도 새벽 2시면 잠듦.

---

## 트러블슈팅

### wake 후 500 에러

컨테이너 워밍업이 60초보다 오래 걸리는 경우. 1~2분 더 기다리고 재시도.

### MySQL 연결 실패

- MySQL이 stop 상태면 connection refused → wake 워크플로우 실행
- `require_secure_transport=OFF` 설정 (SSL 안 함) — 데모 환경 한정. 프로덕션이면 SSL 설정 필요

### 컨테이너 로그 확인 (Azure CLI 있다면)

```bash
az containerapp logs show -n capstone-demo -g capstone_design --tail 30
az containerapp logs show -n capstone-ai -g capstone_design --tail 30
```

또는 GitHub Actions 워크플로우 → 헬스체크 단계의 응답 코드 확인.

### CI/CD 실패

GitHub Actions → 실패한 run → 로그 → 단계별 에러 확인. 자주 발생:
- Dockerfile build 실패 → requirements.txt 호환성 문제
- 새 secret 추가 잊음 → Container App env 미설정

---

## v2 후보 (현재 미적용)

- PDF 본문을 AI source citation으로 전달 (현재 텍스트만 검증)
- capstone_ai의 Job 상태/결과를 MySQL에 영속화 (현재 in-memory)
- MySQL SSL 강제 (현재 OFF, demo용)
- 보안 강화 (Private endpoint, managed identity)

---

## 빠른 참조 (한눈에)

| 상황 | 방법 |
|---|---|
| 시연 준비 | GitHub Actions → Manage deployment → wake |
| 정지 | 같은 워크플로우 → sleep (또는 02:00 KST 자동) |
| 배포 | `deploy_test` 브랜치 push |
| 로컬 dev | `git pull` + `pip install -r requirements.txt` |
| 로그 | GitHub Actions run 로그 또는 `az containerapp logs show` |
