"""애플리케이션 설정.

환경변수를 pydantic-settings로 로드하고 앱 전역에서 사용.
.env 파일이 있으면 자동으로 읽음.
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """앱 설정. 환경변수 또는 .env 파일에서 로드."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- 환경 ---
    app_env: Literal["development", "production", "test"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # --- LLM ---
    # provider는 OpenAI로 결정. 노드별로 다른 모델을 쓸 수 있게 분리.
    # 비용 실험 시 환경변수만 바꿔서 조정 가능.
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None  # 보존: 나중에 변경 가능성

    llm_model_extraction: str = "gpt-4o-mini"
    """전처리 노드(Claim 추출)용 모델."""

    llm_model_verification: str = "gpt-4o-mini"
    """4개 검증 노드용 모델."""

    llm_model_aggregation: str = "gpt-4o-mini"
    """종합판정 노드용 모델. 정확도 이슈 시 상위 모델로 업그레이드 가능."""

    llm_temperature: float = 0.0
    """검증 작업은 일관성이 핵심이므로 0 고정. 필요 시 노드별 override."""

    llm_request_timeout: float = 60.0
    """LLM 호출 타임아웃 (초)."""

    # --- 검색 ---
    search_provider: Literal["tavily", "openai"] = "tavily"
    """FACT/NUMERIC/RECENCY 검증 노드에서 사용할 웹검색 provider."""

    tavily_api_key: str | None = None

    tavily_max_results: int = 5
    """기본 검색 결과 개수. 노드에서 override 가능."""

    openai_search_model: str = "gpt-5-mini"
    """OpenAI Responses API web_search tool에 사용할 모델."""

    # --- LangSmith ---
    langchain_tracing_v2: bool = False
    langchain_api_key: str | None = None
    langchain_project: str = "capstone-ai"

    # --- DB ---
    database_url: str | None = None


@lru_cache
def get_settings() -> Settings:
    """싱글톤 패턴으로 설정 인스턴스 반환.

    FastAPI 의존성 주입에서 ``Depends(get_settings)`` 형태로 사용.
    """
    return Settings()
