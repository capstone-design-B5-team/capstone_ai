# ruff: noqa: E501

"""Fact verification prompts."""

FACT_CHECK_SYSTEM = """당신은 AI 자료 검증 플랫폼의 'Node 1: 사실관계 확인 에이전트'입니다.
이 노드는 사건·제도·인과관계·개념의 '존재 및 발생 여부'를 검증합니다.
주장에서 수치(%, 배, 억 원 등)를 제거해도 핵심 검증이 가능한 경우에만 이 노드가 담당합니다.
수치 자체가 검증 대상인 주장(수치를 빼면 의미를 잃는 주장)은 수치 검증 노드(Node 4)로 넘어갑니다."""

FACT_QUESTION_USER = """다음 Claim을 분석하여 유형 분류, 검증용 자연어 질문 5~7개, 각 질문별 검색 쿼리를 생성하십시오.

[유형 분류]
- EPC (Empirical/Process/Causal): 역사적 사실, 제도·법령, 인과관계 등 사건·현상의 발생 및 존재 여부를 다루는 주장
- CC (Conceptual/Categorical): 정의·개념, 분류 등 어떤 대상이 무엇인지를 다루는 주장

[자연어 질문 생성 지침]
- 다각도로 검증하기 위해 서로 다른 관점의 질문을 5~7개 생성하십시오. 질문이 많을수록 증거 수집 기회가 늘어납니다.
- 질문은 가능한 한 40~70자 이내의 간결한 형태로 작성하십시오.
- Claim date ({claim_date})를 기준 시점으로 활용하여, 해당 시점에 유효한 사실과 맥락을 확인하는 질문을 포함하십시오. 검색 쿼리에는 주장 시점의 연도를 포함하십시오.
- Q1: 핵심 사실 확인 (사건 발생·존재 여부, 예: "스웨덴이 1995년에 남성 육아휴직 의무화를 도입했나요?")
- Q2: 구체적 수치·시점·주체·조건 확인 (예: "스웨덴 남성 의무 육아휴직 도입 시점은 언제인가요?")
- Q3: 인과·맥락 확인 (예: "Did X cause Y?", "What effect did X have on Y?")
- Q4: 배경·전제 확인 (예: "Claim의 배경이 된 정책·제도·상황은 무엇인가요?", "What was the context leading to X?")
- Q5: 반례·예외 확인 (예: "X가 아닌 경우 또는 예외가 있나요?", "Are there any exceptions or counterexamples to X?")
- Q6: Claim date ({claim_date}) 시점 유효성 확인 (예: "이 사실은 Claim date 당시에도 유효했나요?")
- Q7(선택): 대안적 관점 또는 반론 (예: "이 주장에 대한 다른 해석이나 반론이 있나요?")
- CC 유형: 개념 정의를 묻는 형태도 포함하십시오 (예: "의무 육아휴직제는 어떻게 정의되나요?")
- 질문은 Claim과 동일한 언어로 자연어 질문 형태로 작성하십시오.

Claim:
{claim}

Context:
{context}

Claim date:
{claim_date}

반드시 아래 JSON 형식으로만 출력하십시오:
{{"claim_type": "EPC|CC", "questions": [
  {{"question": "...", "search_queries": ["쿼리1", "쿼리2"]}},
  {{"question": "...", "search_queries": ["쿼리3"]}},
  {{"question": "...", "search_queries": ["쿼리4"]}},
  {{"question": "...", "search_queries": ["쿼리5"]}},
  {{"question": "...", "search_queries": ["쿼리6"]}},
  {{"question": "...", "search_queries": ["쿼리7"]}},
  {{"question": "...", "search_queries": ["쿼리8"]}}
]}}"""

FACT_QUESTION_USER_AVERITEC = """Generate 5-8 natural-language verification questions and per-question search queries for the Claim below.
These questions must follow the style of human fact-checkers in the AVeriTeC dataset.

[Type classification]
- EPC (Empirical/Process/Causal): claims about whether an event/process/causal relation occurred or exists.
- CC (Conceptual/Categorical): claims about definitions, concepts, or categories.

[Question style — REQUIRED]
- Write short, concrete, factual questions. Each question asks about exactly ONE verifiable fact
  (a specific event, date, number, person, organization, or source).
- Prioritize these question types:
  * Provenance / source — where the claim originally came from and what kind of source that is
    (e.g., "Where was this claim first published/reported?", "What kind of website or organization is X?").
  * Concrete fact — a specific date, number, procedure, or party involved
    (e.g., "When did X happen?", "How much was the amount in X?", "Who did X?").
  * Authority / evidence — whether a relevant official body, study, or statement supports or refutes it
    (e.g., "Did X agency confirm this?", "Are there any studies or official records showing X?").
  * Time validity — whether the fact held as of the Claim date ({claim_date}).
- Use direct yes/no-answerable questions liberally.
- AVOID abstract, analytical, or meta questions such as "What was the impact of X?",
  "What alternative interpretations exist?", "What is the broader context?", "Why is this significant?".
  Such questions do not help fact verification.
- Write the questions in the same language as the Claim.

[Search queries]
- For each question, write 1-2 search queries containing specific proper nouns, organizations, dates, or source names. Include the claim-period year.

Claim:
{claim}

Context:
{context}

Claim date:
{claim_date}

Output ONLY in the following JSON format:
{{"claim_type": "EPC|CC", "questions": [
  {{"question": "...", "search_queries": ["query1", "query2"]}},
  {{"question": "...", "search_queries": ["query3"]}},
  {{"question": "...", "search_queries": ["query4"]}},
  {{"question": "...", "search_queries": ["query5"]}}
]}}"""

FACT_ANSWER_USER = """다음 질문에 대해 Evidence를 바탕으로 사실에 근거한 답변을 생성하십시오.

Claim Type: {claim_type}
[Claim Type별 답변 지침]
- EPC (사실·프로세스·인과관계): 사건 발생 여부를 명확히 답하십시오.
- CC (개념·분류): 개념의 정의나 분류를 설명하십시오.

[답변 유형 — 우선순위 순]
- Extractive (최우선): Evidence에서 핵심 문장을 직접 추출하여 답변하십시오.
- Boolean: Yes/No로 명확히 답할 수 있는 경우에만 사용하십시오. answer는 반드시 "Yes" 또는 "No"로만 작성하고, boolean_explanation에 판단 근거를 상세히 서술하십시오.
- Abstractive: Evidence를 직접 인용할 수 없는 경우에만 사용하십시오. Evidence를 종합·요약하여 답변하십시오.
- Evidence가 없거나 불충분하면 answer_type을 "Unanswerable"로 하십시오.

Evidence에서 직접 인용(Extractive)을 최우선으로 선택하십시오. Abstractive는 Evidence를 직접 인용할 수 없는 경우에만 사용하십시오.

답변은 Claim과 동일한 언어로 작성하십시오.

Claim:
{claim}

Question:
{question}

Evidence:
{evidence}

반드시 아래 JSON 형식으로만 출력하십시오 (answer_type은 Boolean, Extractive, Abstractive, Unanswerable 중 하나):
Boolean인 경우: {{"answer": "Yes", "answer_type": "Boolean", "boolean_explanation": "...상세 근거..."}}
Non-Boolean인 경우: {{"answer": "...", "answer_type": "Extractive|Abstractive|Unanswerable"}}"""

