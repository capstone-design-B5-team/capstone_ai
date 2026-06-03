"""전처리 노드.

입력 텍스트에서 검증 대상 Claim들을 추출하여 State에 저장한다.
이 노드의 출력 품질이 후속 4개 검증 노드의 입력 품질을 결정하므로
파이프라인에서 가장 중요한 단계 중 하나.

처리 흐름:
    1. LLM에 Claim 추출 프롬프트 전송
    2. JSON 응답을 fallback 파서로 파싱
    3. 각 항목을 검증하고 ``make_claim()``으로 Claim 객체 생성
       (UUID, content_hash 등 메타 필드는 코드에서 부여)
    4. claims 리스트를 State에 반환
"""

from __future__ import annotations

import logging
import re
from time import perf_counter
from typing import Any, get_args

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from ai_backend.core.ids import make_citation, make_claim
from ai_backend.core.llm import get_llm
from ai_backend.core.parsing import parse_json_with_fallback
from ai_backend.graph.prompts.claim_extraction import (
    CLAIM_EXTRACTION_SYSTEM,
    CLAIM_EXTRACTION_USER,
)
from ai_backend.graph.state import Citation, CitationType, Claim, ClaimType, GraphState

logger = logging.getLogger(__name__)

VALID_CLAIM_TYPES: frozenset[str] = frozenset(get_args(ClaimType))

_PAST_YEAR_RE = re.compile(r"(?:19|20)\d{2}년\s*(?:기준|당시|의)?")
_PRESENT_IMPLICATION_TERMS = (
    "여전히", "아직도", "지금도", "지금까지도", "현재까지도", "오늘날까지도", "변함없이",
    "고착화", "만성화", "만성적", "지속적으로", "계속",
    "OECD 최고", "OECD 최하", "가장 높은", "가장 낮은", "심각한", "최악",
)
"""state.py의 ClaimType Literal에서 자동으로 유효 type 집합을 추출.

타입 추가 시 한 곳만 수정하면 검증 로직이 자동 반영된다.
"""

VALID_CITATION_TYPES: frozenset[str] = frozenset(get_args(CitationType))


def preprocess_node(
    state: GraphState,
    *,
    llm: BaseChatModel | None = None,
) -> dict[str, list[Claim]]:
    """전처리 노드 본체.

    LangGraph가 ``state``를 인자로 호출. ``llm``은 테스트에서 주입할 수 있도록
    keyword-only 인자로 노출 (None이면 기본 LLM 사용).

    Args:
        state: 현재 GraphState. ``raw_text``와 ``document_id``를 사용.
        llm: 테스트용 mock LLM. None이면 ``get_llm("extraction")`` 호출.

    Returns:
        ``{"claims": [...]}`` 부분 업데이트. LangGraph가 State에 병합.
    """
    raw_text = state["raw_text"]
    document_id = state["document_id"]
    started = perf_counter()
    logger.info(
        "preprocess_node started document_id=%s text_chars=%d",
        document_id,
        len(raw_text),
    )

    if not raw_text or not raw_text.strip():
        logger.warning("preprocess_node: 입력 텍스트가 비어있음")
        return {"claims": []}

    # 1. LLM 호출
    llm = llm if llm is not None else get_llm("extraction")
    messages = [
        SystemMessage(content=CLAIM_EXTRACTION_SYSTEM),
        HumanMessage(content=CLAIM_EXTRACTION_USER.format(text=raw_text)),
    ]
    llm_started = perf_counter()
    logger.info("preprocess_node LLM extraction started document_id=%s", document_id)
    response = llm.invoke(messages)
    raw_output = response.content if isinstance(response.content, str) else str(response.content)
    logger.info(
        "preprocess_node LLM extraction finished document_id=%s elapsed=%.2fs output_chars=%d",
        document_id,
        perf_counter() - llm_started,
        len(raw_output),
    )

    # 2. JSON 파싱 (fallback 포함)
    parsed = parse_json_with_fallback(raw_output)
    if parsed is None:
        logger.error(
            "preprocess_node: JSON 파싱 실패. 원본 출력 (앞 500자):\n%s",
            raw_output[:500],
        )
        return {"claims": []}

    if not isinstance(parsed, list):
        logger.error("preprocess_node: 파싱 결과가 list가 아님: %s", type(parsed).__name__)
        return {"claims": []}

    # 3. 각 항목 검증 및 Claim 객체 생성
    if not parsed:
        logger.warning(
            "preprocess_node parsed empty list document_id=%s raw_output=%r",
            document_id,
            raw_output[:500],
        )

    claims: list[Claim] = []
    for idx, item in enumerate(parsed):
        claim = _validate_and_build_claim(item, document_id=document_id, idx=idx)
        if claim is not None:
            claims.append(claim)

    claims = [_add_recency_if_missing(c) for c in claims]
    logger.info("preprocess_node: %d개 항목 중 %d개 Claim 추출", len(parsed), len(claims))
    logger.info(
        "preprocess_node finished document_id=%s elapsed=%.2fs parsed_items=%d claims=%d",
        document_id,
        perf_counter() - started,
        len(parsed),
        len(claims),
    )
    return {"claims": claims}


def _validate_and_build_claim(
    item: Any,
    *,
    document_id: str,
    idx: int,
) -> Claim | None:
    """LLM이 반환한 dict 1개를 검증하고 Claim 객체로 변환.

    검증 실패 시 None 반환 + 로그. 한두 항목이 잘못돼도 전체를 버리지 않는다.

    검증 항목:
        - dict 타입인가
        - text가 비어있지 않은 문자열인가
        - type이 list이고, 유효한 ClaimType이 1개 이상 있는가
          (잘못된 type 값은 제거하되 유효한 게 있으면 통과)
        - context는 누락 시 빈 문자열로 fallback

    위치 정보:
        - 원문 내 위치 추적은 수행하지 않는다.
    """
    if not isinstance(item, dict):
        logger.warning("Claim[%d]: dict가 아님 (%s)", idx, type(item).__name__)
        return None

    # text 검증
    text = item.get("text")
    if not isinstance(text, str) or not text.strip():
        logger.warning("Claim[%d]: text 누락 또는 빈 문자열", idx)
        return None

    # type 검증 (관대하게: 잘못된 값은 거르고 유효한 것만 유지)
    raw_types = item.get("type")
    if not isinstance(raw_types, list):
        logger.warning("Claim[%d]: type이 list가 아님", idx)
        return None

    valid_types: list[ClaimType] = [t for t in raw_types if t in VALID_CLAIM_TYPES]
    if not valid_types:
        logger.warning("Claim[%d]: 유효한 type이 없음 (raw=%s)", idx, raw_types)
        return None

    invalid_count = len(raw_types) - len(valid_types)
    if invalid_count > 0:
        logger.info("Claim[%d]: 유효하지 않은 type %d개 제거됨", idx, invalid_count)

    # context는 옵셔널
    context = item.get("context", "")
    if not isinstance(context, str):
        context = ""

    text_clean = text.strip()

    # citations 검증 및 추출
    citations = _extract_citations(item.get("citations"), idx=idx)

    # SOURCE type과 citations 일관성 보정
    # - LLM이 SOURCE 부여했는데 citations가 비어있으면 → SOURCE 제거
    # - LLM이 citations는 채웠는데 SOURCE 누락이면 → SOURCE 추가
    has_source_type = "SOURCE" in valid_types
    has_citations = len(citations) > 0
    if has_source_type and not has_citations:
        valid_types = [t for t in valid_types if t != "SOURCE"]
        logger.info(
            "Claim[%d]: SOURCE 부여됐으나 citations 없음 → SOURCE 제거", idx
        )
    elif has_citations and not has_source_type:
        valid_types.append("SOURCE")
        logger.info(
            "Claim[%d]: citations 있으나 SOURCE 누락 → SOURCE 추가", idx
        )

    # type이 모두 제거되어 비었으면 Claim 자체를 제외
    if not valid_types:
        logger.warning("Claim[%d]: 정합성 보정 후 유효한 type이 없음", idx)
        return None

    return make_claim(
        text=text_clean,
        type_=valid_types,
        context=context.strip(),
        document_id=document_id,
        citations=citations,
    )


def _extract_citations(
    raw_citations: Any,
    *,
    idx: int,
) -> list[Citation]:
    """LLM이 반환한 citations 항목을 검증하여 Citation 객체 리스트로 변환.

    잘못된 항목은 거르되 유효한 것은 유지. citations가 None/누락이면 빈 리스트.
    citation의 원문 내 위치는 추적하지 않고 기본값(-1)을 유지한다.
    """
    if raw_citations is None:
        return []
    if not isinstance(raw_citations, list):
        logger.warning("Claim[%d]: citations가 list가 아님 (%s)", idx, type(raw_citations).__name__)
        return []

    citations: list[Citation] = []
    for c_idx, c_item in enumerate(raw_citations):
        if not isinstance(c_item, dict):
            logger.warning("Claim[%d].citations[%d]: dict가 아님", idx, c_idx)
            continue

        c_raw = c_item.get("raw_text")
        c_type = c_item.get("citation_type")

        if not isinstance(c_raw, str) or not c_raw.strip():
            logger.warning("Claim[%d].citations[%d]: raw_text 누락/빈 문자열", idx, c_idx)
            continue

        if c_type not in VALID_CITATION_TYPES:
            logger.warning(
                "Claim[%d].citations[%d]: 유효하지 않은 citation_type (%s)", idx, c_idx, c_type
            )
            continue

        c_text_clean = c_raw.strip()
        # citation_type이 정적으로 검증됐으므로 cast 불필요 (mypy는 알지만 명시적 처리)
        citations.append(
            make_citation(
                raw_text=c_text_clean,
                citation_type=c_type,
            )
        )

    return citations


def _add_recency_if_missing(claim: Claim) -> Claim:
    """과거 시점 + 현재 함의 패턴이 있는데 RECENCY 타입이 누락됐으면 보정한다.

    claim_extraction LLM이 RECENCY 조건을 충족하는 claim에 타입을 부여하지 않은 경우
    cherry-picking 탐지(recency_check_node)가 아예 실행되지 않는다. 이를 방지하기 위해
    패턴 매칭으로 한 번 더 확인하고 누락된 경우에만 추가한다.
    """
    if "RECENCY" in claim["type"]:
        return claim

    text = f"{claim['text']} {claim.get('context', '')}"
    has_past = bool(_PAST_YEAR_RE.search(text))
    has_present = any(term in text for term in _PRESENT_IMPLICATION_TERMS)

    if has_past and has_present:
        claim["type"].append("RECENCY")
        logger.info(
            "preprocess_node RECENCY 보정 claim_id=%s text=%r",
            claim["id"],
            claim["text"][:80],
        )
    return claim
