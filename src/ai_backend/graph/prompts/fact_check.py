# ruff: noqa: E501

"""Fact verification prompts."""

FACT_CHECK_SYSTEM = """당신은 AI 자료 검증 플랫폼의 'Node 1: 사실관계 확인 에이전트'입니다.
이 노드는 사건·제도·인과관계·개념의 '존재 및 발생 여부'를 검증합니다.
주장에서 수치(%, 배, 억 원 등)를 제거해도 핵심 검증이 가능한 경우에만 이 노드가 담당합니다.
수치 자체가 검증 대상인 주장(수치를 빼면 의미를 잃는 주장)은 수치 검증 노드(Node 4)로 넘어갑니다."""

FACT_QUESTION_USER = """다음 Claim을 분석하여 유형 분류, 검증용 자연어 질문 5~7개, 각 질문별 검색 쿼리를 생성하십시오.

[유형 분류]
- EPC (Event / Property Claim): 사건 발생, 속성, 상태, 존재 여부를 다루는 주장
- CC (Causal Claim): A가 B를 유발했는지, A 때문에 B가 발생했는지, A가 B로 이어졌는지 다루는 주장
- 개념 정의 질문은 claim이 실제로 정의나 분류를 주장할 때만 생성하십시오. 일반 fact claim을 개념 질문으로 바꾸지 마십시오.

[자연어 질문 생성 지침]
- 다각도로 검증하기 위해 서로 다른 관점의 질문을 5~7개 생성하십시오. 질문이 많을수록 증거 수집 기회가 늘어납니다.
- 질문은 가능한 한 40~70자 이내의 간결한 형태로 작성하십시오.
- AVeriTeC QA처럼 한 질문은 하나의 원자 사실만 묻도록 하십시오. 배경·영향·이유를 한 질문에 합치지 마십시오.
- 핵심 entity, action, date, location, condition을 그대로 포함한 직접 질문을 우선 생성하십시오.
- Claim date ({claim_date})를 기준 시점으로 활용하여, 해당 시점에 유효한 사실과 맥락을 확인하는 질문을 포함하십시오. 검색 쿼리에는 주장 시점의 연도를 포함하십시오.
- Q1: 핵심 사실 확인 (사건 발생·존재 여부, 예: "스웨덴이 1995년에 남성 육아휴직 의무화를 도입했나요?")
- Q2: 구체적 수치·시점·주체·조건 확인 (예: "스웨덴 남성 의무 육아휴직 도입 시점은 언제인가요?")
- Q3: 인과·맥락 확인 (예: "Did X cause Y?", "What effect did X have on Y?")
- Q4: 배경·전제 확인 (예: "Claim의 배경이 된 정책·제도·상황은 무엇인가요?", "What was the context leading to X?")
- Q5: 반례·예외 확인 (예: "X가 아닌 경우 또는 예외가 있나요?", "Are there any exceptions or counterexamples to X?")
- Q6: Claim date ({claim_date}) 시점 유효성 확인 (예: "이 사실은 Claim date 당시에도 유효했나요?")
- Q7(선택): 대안적 관점 또는 반론 (예: "이 주장에 대한 다른 해석이나 반론이 있나요?")
- 질문은 Claim과 동일한 언어로 자연어 질문 형태로 작성하십시오.

Type-specific QA atom policy:
- EPC path: prioritize direct event/property atoms: Did X happen? Is X true?
  What did the named person/source say about the claim? What is the correct
  fact if the claim is false? When was the claim or event?
- CC path: prioritize causal atoms: Did A cause B? What evidence links A to B?
  What do authoritative sources say about the causal relationship? Is the
  causal relation established or only alleged?
- For Refuted claims, ask for the direct denial, correction, true alternative
  fact, or authoritative recommendation rather than only a verdict question.
- Do not use broad context/impact/reason questions unless that atom is material
  to the claim.

AVeriTeC QA coverage requirements:
- Do not force generic context, exception, background, or "alternative view"
  questions. They often hurt matching unless the claim itself asserts that atom.
- If the claim contains exact scope, time, entity, location, condition, quote,
  source, causal wording, or numeric wording, ask that atom directly.
- For broad or partially framed claims, ask the smallest missing or qualified
  atom directly, not a general "what is the context" question.
- Search queries should preserve the named entities and exact claim wording;
  add fact check, official, source, transcript, correction, or false only when
  natural for finding the direct evidence fragment.

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

AVeriTeC answer calibration:
- Optimize for the QA answer fragment, not for a final verdict sentence.
- If the evidence refutes the claim, answer with the direct denial, correction,
  true alternative fact, authoritative recommendation, or relevant quote when
  available. Avoid answers that only say "the claim is false".
- If the evidence is related to the broad topic but does not answer the exact
  subject, time, place, scope, condition, or causal wording in the question,
  return Unanswerable rather than inferring a Yes/No answer.
- When evidence shows a partial truth, exception, caveat, omitted context, or
  credible disagreement, include that limitation explicitly in the answer.
- Do not turn "not officially confirmed", "not aware of reports", or "not enough
  data" into a direct No unless the evidence explicitly disproves the claim.
- Keep the answer as one short sentence when possible. Prefer the exact
  sentence fragment that answers the question; do not add background, causes,
  implications, or source commentary unless the question asks for them.
- Also output support_type, directness, and mismatch_type:
  support_type must be one of direct_support, partial_support, contradiction,
  insufficient_evidence, related_only, unknown.
  directness must be one of direct, indirect, unknown.
  mismatch_type must be one of none, scope, time, number, attribution, context,
  methodology, source, unknown.

반드시 아래 JSON 형식으로만 출력하십시오 (answer_type은 Boolean, Extractive, Abstractive, Unanswerable 중 하나):
Boolean인 경우: {{"answer": "Yes", "answer_type": "Boolean", "boolean_explanation": "...상세 근거...", "support_type": "direct_support", "directness": "direct", "mismatch_type": "none"}}
Non-Boolean인 경우: {{"answer": "...", "answer_type": "Extractive|Abstractive|Unanswerable", "support_type": "partial_support", "directness": "indirect", "mismatch_type": "context"}}"""

