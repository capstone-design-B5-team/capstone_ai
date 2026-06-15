# ruff: noqa: E501

"""Source verification prompts."""

SOURCE_CHECK_SYSTEM = """당신은 AI 자료 검증 플랫폼의 '출처 검증 에이전트'입니다.
웹 검색으로 수집한 증거를 바탕으로 주장의 출처 신뢰성을 분석하고 질의응답(Q&A) 증거를 생성합니다.

[인용 유형별 분석 기준]
- PS (Paraphrase Support): 출처의 내용을 요약·의역하여 주장의 근거로 사용한 경우
  · 증거에서 핵심 주장이 의미상 지지되는지 종합적으로 판단하여 답변하십시오.
- QV (Quote Verification): 출처의 내용을 따옴표로 직접 인용한 경우
  · 인용구가 증거에 실제로 존재하는지 Extractive 방식으로 찾아 답변하십시오."""

SOURCE_QUESTION_USER = """다음 Claim과 인용 출처를 분석하여 인용 유형, 검증용 자연어 질문 4~6개, 각 질문별 검색 쿼리를 생성하십시오.

[인용 유형]
- PS (Paraphrase Support): 출처의 내용을 요약·의역하여 주장의 근거로 사용한 경우
- QV (Quote Verification): 출처의 내용을 따옴표로 직접 인용한 경우

[질문 생성 지침]
- 질문이 많을수록 증거 수집 기회가 늘어납니다. 4~6개의 서로 다른 관점의 질문을 생성하십시오.
- Q1: 핵심 확인 (PS: "{{화자/기관}}이 {{핵심 주장}}을 밝혔나요?" / QV: "{{화자}}가 실제로 '{{인용구}}'라고 말했나요?")
- Q2: 맥락·배경 확인 (예: "해당 발언·보고가 이루어진 상황이나 맥락은 무엇인가요?")
- Q3: 출처·공식 발표 확인 (예: "이 내용이 공식적으로 발표되거나 보고된 적이 있나요?")
- Q4: Claim date ({claim_date}) 시점 유효성 (예: "이 발언·보고는 {claim_date} 시점에 유효했나요?")
- Q5(선택): 추가 관점 (예: "이에 대한 다른 기관이나 전문가의 입장은 무엇인가요?")
- Q6(선택): 공식 반박 또는 수정 (예: "이 주장에 대한 공식 반박이나 수정 발표가 있었나요?")
- 질문은 Claim과 동일한 언어로 자연어 질문 형태로 작성하십시오.

[search_queries 생성 지침]
- PS: Claim의 핵심 주장을 검증할 수 있는 웹 검색 쿼리 2~3개를 생성하십시오.
- QV: 인용구가 실제로 존재하는지 확인할 수 있는 검색 쿼리 2~3개를 생성하십시오. (예: "화자명 인용구 핵심 키워드", "기관명 해당 발언 원문")

Claim:
{claim}

Citation:
{citation}

Claim date:
{claim_date}

반드시 아래 JSON 형식으로만 출력하십시오:
{{"citation_type": "PS|QV", "questions": [
  {{"question": "...", "search_queries": ["쿼리1", "쿼리2"]}},
  {{"question": "...", "search_queries": ["쿼리3"]}},
  {{"question": "...", "search_queries": ["쿼리4"]}},
  {{"question": "...", "search_queries": ["쿼리5"]}},
  {{"question": "...", "search_queries": ["쿼리6"]}},
  {{"question": "...", "search_queries": ["쿼리7"]}}
]}}"""

SOURCE_ANSWER_USER = """다음 질문에 대해 제공된 Evidence를 바탕으로 답변을 생성하십시오.

인용 유형: {citation_type}
- PS인 경우: 출처가 해당 내용을 의미상 지지하는지 종합적으로 판단하여 답변하십시오. 출처에서 해당 내용을 직접 인용(Extractive)할 수 있으면 우선 사용하십시오.
- QV인 경우: 증거에서 인용구를 직접 찾아 Extractive 방식으로 답변하십시오. 출처에서 해당 내용을 직접 인용(Extractive)할 수 있으면 우선 사용하십시오.

[답변 유형]
- Boolean: Yes/No로 답할 수 있는 경우. answer는 반드시 "Yes" 또는 "No"로만 작성하고, boolean_explanation에 판단 근거를 상세히 서술하십시오.
- Extractive: Evidence에서 관련 문장을 직접 인용하는 경우
- Abstractive: 증거 내용을 종합하여 답변하는 경우
- 증거가 없거나 내용 확인이 불가하면 answer_type을 "Unanswerable"로 하십시오.

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
