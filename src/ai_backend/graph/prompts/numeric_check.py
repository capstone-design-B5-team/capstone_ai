"""Numeric verification prompts."""

# ruff: noqa: E501

NUMERIC_CHECK_SYSTEM = """당신은 AI 자료 검증 플랫폼의 'Node 4: 수치 검증 에이전트'입니다.
이 노드는 주장 속 '수치(숫자, 비율, 통계, 금액 등) 자체'의 정확성을 검증합니다.
수치가 존재하더라도 수치가 주장의 핵심이 아닌 경우(예: "5개국이 참여했다")는 사실관계 확인 노드(Node 1)가 담당합니다."""

NUMERIC_QUESTION_USER = """다음 수치 Claim을 분석하여 검증용 자연어 질문 4~6개와 각 질문별 검색 쿼리를 생성하십시오.

[자연어 질문 생성 지침]
- 질문이 많을수록 증거 수집 기회가 늘어납니다. 4~6개의 서로 다른 관점의 질문을 생성하십시오.
- Claim date ({claim_date})의 연도를 검색 쿼리에 반드시 포함하십시오.
- Q1: 핵심 수치 검증 (예: "{{주체}}의 {{연도}} {{지표}}는 얼마였나요?", "What was X's Y in [year]?")
- Q2: 비교 대상 또는 맥락 질문 (예: 비교 대상의 수치, 해당 수치의 발표 출처 등)
- Q3: 단위·기준·출처 확인 (예: "해당 수치의 단위 및 측정 기준은 무엇인가요?", "어떤 기관이 이 수치를 발표했나요?")
- Q4: 시점·맥락 추가 확인 (예: "해당 수치의 기준 연도 또는 측정 시점은 언제인가요?")
- Q5(선택): 공식 출처·발표 날짜 확인 (예: "이 수치를 공식 발표한 기관과 발표 날짜는 언제인가요?")
- Q6(선택): 인접 연도와의 비교 (예: "전년도 및 다음 해의 해당 지표 수치는 얼마였나요?")
- 질문은 Claim과 동일한 언어로 자연어 질문 형태로 작성하십시오.
- AVeriTeC QA처럼 한 질문은 하나의 수치 사실만 묻도록 하십시오. 수치, 단위, 기간, 대상, 비교 기준을 분리해서 묻고 한 질문에 여러 검증 목표를 합치지 마십시오.

NC gold-style QA atom policy:
- First ask for the exact value/number/percentage in the claim.
- Then ask separate questions for unit, period, geography, comparison basis,
  source/institution, and corrected figure if the claim is wrong.
- Prefer extractive answers containing the exact number and unit.
- Do not turn a numeric claim into a broad background or policy question unless
  the number cannot be interpreted without that basis.

AVeriTeC numeric QA coverage requirements:
- Ask the exact number together with its unit, denominator, measurement method,
  timeframe, geography, and comparison target when those atoms appear in the
  claim.
- Ask separate direct questions for a different unit, period, baseline,
  annualization, per-capita basis, nominal/real basis, or source methodology
  only when the claim's number depends on that basis.
- Do not force broad context or caveat questions. If context matters, ask the
  concrete missing numeric basis directly.
- Search queries should preserve the claimed number and named entities; add
  official, methodology, annualized, per capita, baseline, fact check, or
  misleading only when useful for finding the direct numeric fragment.

Claim:
{claim}

Context:
{context}

Claim date:
{claim_date}

반드시 아래 JSON 형식으로만 출력하십시오:
{{"questions": [
  {{"question": "...", "search_queries": ["쿼리1", "쿼리2"]}},
  {{"question": "...", "search_queries": ["쿼리3"]}},
  {{"question": "...", "search_queries": ["쿼리4"]}},
  {{"question": "...", "search_queries": ["쿼리5"]}},
  {{"question": "...", "search_queries": ["쿼리6"]}},
  {{"question": "...", "search_queries": ["쿼리7"]}}
]}}"""

NUMERIC_ANSWER_USER = """다음 질문에 대해 Evidence에서 관련 수치를 찾아 답변을 생성하십시오.

[답변 원칙]
- Evidence에서 수치를 직접 추출하여 Extractive 방식으로 답변하십시오. 수치는 반드시 원문 그대로 인용(Extractive)하십시오. 계산 결과도 수식과 함께 명시하십시오.
- 수치가 명확히 확인되면 해당 수치와 출처를 포함하여 답변하십시오.
- 수치 비교 결과가 Yes/No로 명확히 판단되는 경우, answer는 "Yes" 또는 "No"로 작성하고 boolean_explanation에 수치 근거를 명시하십시오.
- Evidence에서 수치를 찾을 수 없으면 answer_type을 "Unanswerable"로 하십시오.

답변은 Claim과 동일한 언어로 작성하십시오.

Claim:
{claim}

Question:
{question}

Evidence:
{evidence}

AVeriTeC numeric answer calibration:
- Optimize for the numeric answer fragment, not for a final verdict sentence.
- If the evidence refutes the claim, answer with the corrected figure, unit,
  period, source, or numeric mismatch when available. Avoid answers that only
  say "the claim is false".
- Do not answer Yes only because a nearby number appears. The unit, denominator,
  timeframe, geography, source, and comparison target must match the question.
- If the evidence gives a related number using a different measurement method,
  report the mismatch explicitly instead of treating it as support.
- If the exact numeric basis cannot be established from the evidence, return
  Unanswerable rather than inferring support.
- Keep the answer as one short sentence when possible. Prefer the exact number,
  unit, date, denominator, and comparison target from the evidence; do not add
  general background unless the question asks for it.
- Also output support_type, directness, and mismatch_type:
  support_type must be one of direct_support, partial_support, contradiction,
  insufficient_evidence, related_only, unknown.
  directness must be one of direct, indirect, unknown.
  mismatch_type must be one of none, scope, time, number, attribution, context,
  methodology, source, unknown.

반드시 아래 JSON 형식으로만 출력하십시오 (answer_type은 Boolean, Extractive, Unanswerable 중 하나):
Boolean인 경우: {{"answer": "Yes", "answer_type": "Boolean", "boolean_explanation": "...수치 근거...", "support_type": "direct_support", "directness": "direct", "mismatch_type": "none"}}
Non-Boolean인 경우: {{"answer": "...", "answer_type": "Extractive|Unanswerable", "support_type": "partial_support", "directness": "direct", "mismatch_type": "methodology"}}"""

