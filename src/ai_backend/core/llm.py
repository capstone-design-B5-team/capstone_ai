"""LLM 클라이언트 팩토리.

용도(purpose)별로 다른 모델을 쓸 수 있도록 분리한다.
환경변수로 모델명을 변경 가능하므로 코드 변경 없이 비용/정확도 실험 가능.

Provider 추상화는 LangChain의 ``BaseChatModel``을 그대로 활용한다.
별도 wrapper를 두면 provider별 기능(structured output, tool calling 등)을
잃게 되므로, 노드 코드는 ``BaseChatModel`` 인터페이스에 의존한다.

사용 예:
    >>> from ai_backend.core.llm import get_llm
    >>> llm = get_llm("extraction")
    >>> response = llm.invoke("Hello")
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from ai_backend.config import Settings, get_settings

LLMPurpose = Literal["extraction", "verification", "aggregation"]
"""LLM 사용 목적 분류.

- extraction: 전처리 노드 (Claim 추출)
- verification: 4개 검증 노드 (사실/출처/최신성/수치)
- aggregation: 종합판정 노드
"""


def _resolve_model_name(purpose: LLMPurpose, settings: Settings) -> str:
    """purpose → 모델명 매핑."""
    mapping: dict[LLMPurpose, str] = {
        "extraction": settings.llm_model_extraction,
        "verification": settings.llm_model_verification,
        "aggregation": settings.llm_model_aggregation,
    }
    return mapping[purpose]


@lru_cache(maxsize=8)
def _build_llm_cached(
    model: str,
    temperature: float,
    timeout: float,
    api_key: str,
) -> BaseChatModel:
    """순수 함수로 캐싱 가능한 LLM 빌더.

    같은 (model, temperature, timeout, api_key) 조합이면 동일 인스턴스를 재사용한다.
    ``ChatOpenAI`` 객체 생성 비용은 작지만, 노드마다 새로 만들 이유가 없다.
    """
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        timeout=timeout,
        api_key=api_key,  # type: ignore[arg-type]  # SecretStr 변환은 langchain이 처리
    )


def get_llm(
    purpose: LLMPurpose = "verification",
    *,
    temperature: float | None = None,
) -> BaseChatModel:
    """주어진 purpose에 맞는 LLM 인스턴스 반환.

    Args:
        purpose: 사용 목적. 환경변수에 따라 모델이 분기됨.
        temperature: override 값. None이면 settings의 기본값 사용.

    Returns:
        LangChain ``BaseChatModel`` 인스턴스. provider별 기능
        (structured output, function calling 등)을 그대로 쓸 수 있다.

    Raises:
        ValueError: ``OPENAI_API_KEY``가 설정되지 않은 경우.
    """
    settings = get_settings()
    if not settings.openai_api_key:
        raise ValueError(
            "OPENAI_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요."
        )

    return _build_llm_cached(
        model=_resolve_model_name(purpose, settings),
        temperature=temperature if temperature is not None else settings.llm_temperature,
        timeout=settings.llm_request_timeout,
        api_key=settings.openai_api_key,
    )
