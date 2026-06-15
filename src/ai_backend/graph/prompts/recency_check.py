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

★ [본문이 맨숭한 수치여도 트리거 — 중요] Claim 본문 자체에는 현재형 표현이 없어도, **Context(서술부)가 과거 시점의 수치를 '현재를 대표하는/지금도 유효한' 근거로 제시**하면 조건 ②를 충족한 것으로 보고 반드시 트리거하십시오. cherry-picking의 현재 함의는 주로 Claim 본문이 아니라 Context에 담깁니다.
  - 트리거 O 예: 본문 "아마존 전체 매출의 약 35%가 추천 엔진에서 발생한다" + Context "맥킨지의 2013년 분석 수치는 10년이 지난 지금도 여전히 아마존의 현재 추천 의존도를 변함없이 그대로 대표하는 근거로 인용된다" → 본문엔 현재형 단어가 없지만 Context가 2013년 수치를 현재 대표 근거로 제시 → cherry_pick_direction을 "과장" 또는 "축소"로 판정.

두 조건 중 하나라도 충족하지 않으면 cherry_pick_direction은 "해당없음"으로 반환하십시오.
반례 (트리거 X): "2019년 비정규직 비율은 36.4%였다" (현재·지속 함의 없는 순수 역사적 서술)"""

RECENCY_QUERY_USER = """다음 Claim에서 시점 지표를 추출하고, 현재 함의 여부를 판단한 후 최신 데이터 검색 쿼리를 설계하십시오.
트리거 조건(과거 시점 명시 + 현재 함의) 중 하나라도 충족하지 않으면 cherry_pick_direction은 "해당없음", search_queries는 기본 검색 쿼리로 두십시오.

Claim:
{claim}

Context:
{context}
"""

RECENCY_QUESTION_USER = """다음 Claim에서 시점 지표를 추출하고, cherry-picking 탐지를 위한 검증 질문 3~5개와 각 질문별 검색 쿼리를 생성하십시오. 트리거 조건 충족 시 5개, 미충족 시 3개를 생성하십시오.

[Cherry-picking 탐지 기준]
- 과거 시점 명시 + 현재 함의가 동시에 존재할 때만 cherry-picking 탐지 대상입니다.
- ★ 현재 함의는 Claim 본문이 아니라 Context(서술부)에 있을 수 있습니다. Context가 과거 수치를 '지금도 여전히 현재를 대표하는' 근거로 제시하면 본문에 현재형 단어가 없어도 트리거하여 cherry_pick_direction을 과장/축소로 판정하고 Q1~Q5를 생성하십시오.
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

[최신성 비교 판정 지침 (cherry-picking)]
- 질문이 '최신 수치' 또는 '현재 대표성'에 관한 것이면, Evidence에서 최신(2024~2026) 수치를 찾아 Claim의 과거 수치와 **방향(상승/하락)과 규모 차이**를 명시적으로 비교하십시오.
- 최신 수치가 과거 수치와 방향이 반대이거나 규모 차이가 크면, 과거 수치가 현재 상황을 왜곡 대표한다고 판단하고 Boolean "Yes"로 답하십시오. boolean_explanation에는 "[과장|축소] — 과거 {{연도}} {{수치}} vs 최신 {{수치}}, 따라서 현재를 대표하지 못함" 형태로 근거를 명시하십시오.
- 최신 수치가 과거와 비슷하면 Boolean "No"로 답하고 과거 수치가 여전히 유효함을 근거로 서술하십시오.
- 최신 수치를 Evidence에서 찾을 수 없으면 answer_type을 "Unanswerable"로 하십시오.

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
