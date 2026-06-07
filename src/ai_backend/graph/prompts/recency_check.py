# ruff: noqa: E501

"""Recency verification prompts."""

RECENCY_CHECK_SYSTEM = """당신은 AI 자료 검증 플랫폼의 'Node 3: 데이터 선택적 사용(Cherry-picking) 탐지 에이전트'입니다.
당신의 임무는 주장에서 과거 시점의 수치나 데이터가 현재 맥락을 뒷받침하는 근거로 오용되고 있는지를 탐지하는 것입니다.

[numeric_check Temporal 유형과의 역할 분리]
- numeric_check Temporal: 과거 시점의 수치가 '그 시점 기준으로 정확한가'를 검증
- 이 노드(Node 3): 과거 수치가 '현재 맥락에서 오용되고 있는가'를 검증

[트리거 조건 — 두 조건 모두 충족 시에만 활성화]
1. 과거 시점이 명시됨: 주장에 특정 연도/시점이 포함되어 있음 (예: "2019년 기준", "당시")
2. 현재·지속 함의가 존재함: 해당 수치가 현재 상황 또는 지속적 상태의 근거로 사용되고 있음
   - **현재성 표현**: "현재", "지금", "여전히", "아직도", "지금도", "변함없이" 등이 동반되거나
   - **지속성 표현**: "계속", "지속적으로", "고착화", "만성적", "반복적으로" 등 문제가 이어지고 있음을 암시하거나
   - **현재적 평가 표현**: "OECD 최고 수준", "가장 높은", "심각한", "최악" 등 현재 상태에 대한 평가가 동반되거나
   - Context에서 현재형·지속형 주장의 근거로 해당 수치를 인용하는 경우

두 조건 중 하나라도 충족하지 않으면 cherry_pick_direction은 "해당없음"으로 반환하십시오.
반례 (트리거 X): "2019년 비정규직 비율은 36.4%였다" (현재·지속 함의 없는 순수 역사적 서술)"""

RECENCY_QUERY_USER = """다음 Claim에서 시점 지표를 추출하고, 현재 함의 여부를 판단한 후 최신 데이터 검색 쿼리를 설계하십시오.
트리거 조건(과거 시점 명시 + 현재 함의) 중 하나라도 충족하지 않으면 cherry_pick_direction은 "해당없음", search_queries는 기본 검색 쿼리로 두십시오.

AVeriTeC recency/cherrypicking probe requirements:
- Include a question that verifies the claim at the claim date and another that
  checks whether newer or broader context changes the meaning.
- Include a question that asks whether the claim uses a selective time window,
  outdated statistic, partial trend, or cherry-picked comparison.
- Include a question that searches for official updated data, later correction,
  caveat, or competing interpretation when the claim is temporal or comparative.
- Search queries should include updated, latest, trend, context, correction,
  time series, official, or misleading when natural for the claim.

Claim:
{claim}

Context:
{context}
"""

RECENCY_QUESTION_USER = """다음 Claim에서 시점 지표를 추출하고, cherry-picking 탐지를 위한 검증 질문 3~5개와 각 질문별 검색 쿼리를 생성하십시오. 트리거 조건 충족 시 5개, 미충족 시 3개를 생성하십시오.

[Cherry-picking 탐지 기준]
- 과거 시점 명시 + 현재 함의가 동시에 존재할 때만 cherry-picking 탐지 대상입니다.
- 조건이 충족되지 않으면 Q1~Q3만 생성하십시오.

[질문 생성 지침 (트리거 조건 충족 시)]
- Q1: 과거 수치 검증 (예: "{{연도}} {{주체}}의 {{지표}}는 실제로 {{수치}}였나요?")
- Q2: 최신 수치 조회 (예: "현재(최신) {{주체}}의 {{지표}}는 얼마인가요?")
- Q3: 현재 맥락 유효성 (예: "과거 수치가 현재 {{주제}} 상황을 대표할 수 있나요?")
- Q4: Claim date ({claim_date}) 당시 수치 (예: "Claim date 기준으로 해당 지표는 어떤 상태였나요?")
- Q5(선택): 추세 방향 (예: "해당 지표의 최근 3년간 추세는 어떻게 되나요?")

[search_queries 생성 지침]
- Q1: 과거 시점의 정확한 수치를 확인할 검색어 (연도 포함 필수)
- Q2: 최신(2024~2026) 수치를 찾을 검색어
- Q3: 현재 추세·비교 데이터를 찾을 검색어
- Q4: Claim date 시점 데이터를 찾을 검색어 (Claim date 연도 포함)
- Q5: 최근 3년 추세를 파악할 검색어

AVeriTeC recency QA requirements:
- Generate at least one question for the claim date and one question for newer
  or broader context when the claim is temporal, comparative, or trend-based.
- Generate one question that can reveal selective time windows, outdated data,
  partial trends, or cherry-picked comparisons.
- Prefer search queries with updated, latest, trend, official, correction,
  context, time series, misleading, or fact check when natural.
- AVeriTeC QA처럼 한 질문은 하나의 시점 사실만 묻도록 하십시오. claim date의 사실, 최신 사실, 추세, 선택적 기간 여부를 각각 분리해서 묻습니다.

Claim:
{claim}

Context:
{context}

Claim date:
{claim_date}

반드시 아래 JSON 형식으로만 출력하십시오:
{{"time_indicators": ["시점 키워드1", "키워드2"], "cherry_pick_direction": "과장|축소|해당없음", "questions": [
  {{"question": "...", "search_queries": ["쿼리1", "쿼리2"]}},
  {{"question": "...", "search_queries": ["쿼리3"]}},
  {{"question": "...", "search_queries": ["쿼리4"]}},
  {{"question": "...", "search_queries": ["쿼리5"]}},
  {{"question": "...", "search_queries": ["쿼리6"]}}
]}}"""

RECENCY_ANSWER_USER = """다음 질문에 대해 Evidence를 바탕으로 답변을 생성하십시오.

[답변 원칙]
- 수치 관련 질문이면 Evidence에서 수치를 직접 추출(Extractive)하십시오. 수치·날짜 관련 답변은 Evidence에서 직접 인용(Extractive)하십시오.
- Yes/No로 명확히 답할 수 있으면 Boolean으로 답하고 boolean_explanation에 근거를 서술하십시오.
- Evidence에서 답을 찾을 수 없으면 answer_type을 "Unanswerable"로 하십시오.

답변은 Claim과 동일한 언어로 작성하십시오.

Claim:
{claim}

Question:
{question}

Evidence:
{evidence}

AVeriTeC recency answer calibration:
- Distinguish "true at the claim date" from "still meaningful in the broader or
  current context". Include the timing limitation explicitly.
- If evidence shows the claim is based on a selective window, outdated number,
  or partial trend, state that limitation rather than answering simple Yes.
- If the exact date or trend cannot be established, return Unanswerable.
- Keep the answer as one short sentence when possible. Prefer the exact date,
  number, trend, or timing limitation from the evidence; do not add general
  background unless the question asks for it.
- Also output support_type, directness, and mismatch_type:
  support_type must be one of direct_support, partial_support, contradiction,
  insufficient_evidence, related_only, unknown.
  directness must be one of direct, indirect, unknown.
  mismatch_type must be one of none, scope, time, number, attribution, context,
  methodology, source, unknown.

반드시 아래 JSON 형식으로만 출력하십시오 (answer_type은 Boolean, Extractive, Abstractive, Unanswerable 중 하나):
Boolean인 경우: {{"answer": "Yes", "answer_type": "Boolean", "boolean_explanation": "...상세 근거...", "support_type": "direct_support", "directness": "direct", "mismatch_type": "none"}}
Non-Boolean인 경우: {{"answer": "...", "answer_type": "Extractive|Abstractive|Unanswerable", "support_type": "partial_support", "directness": "direct", "mismatch_type": "time"}}"""
