"""검색 클라이언트.

4개 검증 노드가 공통으로 사용한다. 노드 코드는 ``SearchClient`` Protocol에만
의존하므로 Tavily에서 다른 provider로 갈아끼울 수 있다.

Tavily 무료 티어 한도(월 1000 req)를 초과하거나 품질 이슈 시 Serper/Brave
등으로 교체 가능. 그때는 ``SearchClient``를 구현하는 새 클래스만 추가하면 된다.

사용 예:
    >>> from ai_backend.core.search import get_search_client
    >>> client = get_search_client()
    >>> results = client.search("한국 GDP 2024", max_results=5)
    >>> # 최신성 검증용 — 최근 1년 이내 결과만
    >>> recent = client.search("한국 GDP 2024", max_results=5, days=365)
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from time import perf_counter
from typing import Any, Protocol

from openai import OpenAI
from tavily import TavilyClient

from ai_backend.config import get_settings
from ai_backend.core.parsing import parse_json_with_fallback

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SearchResult:
    """단일 검색 결과.

    어떤 provider를 쓰든 노드는 이 형태로만 받는다.
    """

    url: str
    title: str
    content: str
    """본문 발췌(snippet). 검증 LLM의 컨텍스트로 들어감."""

    score: float = 0.0
    """provider가 부여한 관련도 점수 (0.0~1.0). 없으면 0."""

    published_date: str | None = None
    """발행일 ISO 8601. 최신성 판단에 사용. provider가 제공하지 않으면 None."""

    raw: dict[str, Any] = field(default_factory=dict, repr=False)
    """원본 응답. 디버깅/추가 메타가 필요할 때 참조."""


class SearchClient(Protocol):
    """검색 클라이언트 인터페이스.

    노드 코드가 의존할 추상 타입. Tavily 외에 다른 provider를 쓸 때도
    이 Protocol만 만족하면 된다.
    """

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        days: int | None = None,
    ) -> list[SearchResult]:
        """검색 실행.

        Args:
            query: 검색어.
            max_results: 반환 결과 최대 개수.
            days: 최근 N일 이내 결과만 필터. None이면 제한 없음.
                  (최신성 검증 노드에서 365 등으로 사용)
        """
        ...


class TavilySearchClient:
    """Tavily 검색 클라이언트.

    공식 Python SDK ``tavily-python``을 wrapping한다.
    """

    def __init__(self, api_key: str) -> None:
        self._client = TavilyClient(api_key=api_key)

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        days: int | None = None,
    ) -> list[SearchResult]:
        # Tavily는 days 파라미터를 직접 지원
        started = perf_counter()
        logger.info(
            "tavily search started query=%r max_results=%d days=%s",
            query,
            max_results,
            days,
        )
        kwargs: dict[str, Any] = {
            "query": query,
            "max_results": max_results,
        }
        if days is not None:
            kwargs["days"] = days
            kwargs["topic"] = "news"  # days 필터는 news topic에서 가장 의미 있음

        response = self._client.search(**kwargs)
        results = [
            SearchResult(
                url=item.get("url", ""),
                title=item.get("title", ""),
                content=item.get("content", ""),
                score=float(item.get("score", 0.0)),
                published_date=item.get("published_date"),
                raw=item,
            )
            for item in response.get("results", [])
        ]
        logger.info(
            "tavily search finished elapsed=%.2fs query=%r results=%d",
            perf_counter() - started,
            query,
            len(results),
        )
        return results


class OpenAIWebSearchClient:
    """OpenAI Responses API web_search client."""

    def __init__(self, *, api_key: str, model: str) -> None:
        self._client = OpenAI(api_key=api_key)
        self._model = model
        self._verification_cache: dict[str, dict[str, Any]] = {}
        self._verification_lock = threading.Lock()

    def verify_claim_once(
        self,
        *,
        claim_text: str,
        context: str,
        claim_types: list[str],
        recent_days: int | None = None,
    ) -> dict[str, Any]:
        """Verify one claim with a single OpenAI web_search call.

        The result is cached by claim text/context/types so FACT, NUMERIC, and
        RECENCY nodes do not each perform separate web searches for the same
        claim during one local server run.
        """
        effective_recent_days = recent_days if recent_days is not None else (
            730 if "RECENCY" in claim_types else None
        )
        cache_key = json.dumps(
            {
                "claim_text": claim_text,
                "context": context,
                "claim_types": sorted(claim_types),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        with self._verification_lock:
            cached = self._verification_cache.get(cache_key)
            if cached is not None:
                logger.info("openai claim verification cache hit claim=%r", claim_text[:160])
                return cached

            result = self._verify_claim_once_uncached(
                claim_text=claim_text,
                context=context,
                claim_types=claim_types,
                recent_days=effective_recent_days,
            )
            self._verification_cache[cache_key] = result
            return result

    def _verify_claim_once_uncached(
        self,
        *,
        claim_text: str,
        context: str,
        claim_types: list[str],
        recent_days: int | None,
    ) -> dict[str, Any]:
        started = perf_counter()
        logger.info(
            "openai claim verification started model=%s claim=%r types=%s recent_days=%s",
            self._model,
            claim_text[:160],
            claim_types,
            recent_days,
        )
        recency_hint = (
            f"\nFor recency judgments, prefer sources from the last {recent_days} days."
            if recent_days
            else ""
        )
        response = self._client.responses.create(
            model=self._model,
            tools=[{"type": "web_search"}],
            tool_choice="auto",
            include=["web_search_call.action.sources"],
            input=(
                "You are verifying a claim for a document review system.\n"
                "Use web_search once, then return ONLY a JSON object.\n"
                "Requested verifier sections are based on claim_types.\n"
                "For each applicable section, use judgment PASS, WARNING, or FAIL.\n"
                "Include concise Korean reason, evidence list, sources list, and search_queries.\n"
                "JSON shape:\n"
                "{\n"
                '  "fact": {"judgment": "...", "reason": "...", '
                '"evidence": ["..."], "sources": ["..."], "search_queries": ["..."]},\n'
                '  "numeric": {"judgment": "...", '
                '"type": "Statistical|Comparative|Interval|Temporal|Unknown", '
                '"reason": "...", "suggestion": "", '
                '"evidence": ["..."], "sources": ["..."], '
                '"search_queries": ["..."]},\n'
                '  "recency": {"judgment": "...", "time_indicators": ["..."], '
                '"reason": "...", "evidence": ["..."], "sources": ["..."], '
                '"search_queries": ["..."]}\n'
                "}\n"
                "Omit sections that are not relevant to claim_types.\n"
                f"claim_types: {claim_types}\n"
                f"claim: {claim_text}\n"
                f"context: {context or '(none)'}"
                f"{recency_hint}"
            ),
        )
        text = _response_text(response)
        parsed = parse_json_with_fallback(text)
        result = parsed if isinstance(parsed, dict) else {}
        fallback_sources = [
            item.get("url", "")
            for item in (
                _extract_openai_sources(response) or _extract_openai_citations(response)
            )
            if item.get("url")
        ]
        for section in ("fact", "numeric", "recency"):
            section_value = result.get(section)
            if not isinstance(section_value, dict):
                continue
            sources = section_value.get("sources")
            if not isinstance(sources, list) or not sources:
                section_value["sources"] = fallback_sources
        logger.info(
            "openai claim verification finished elapsed=%.2fs claim=%r sections=%s",
            perf_counter() - started,
            claim_text[:160],
            [key for key in ("fact", "numeric", "recency") if key in result],
        )
        return result

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        days: int | None = None,
    ) -> list[SearchResult]:
        started = perf_counter()
        logger.info(
            "openai web_search started model=%s query=%r max_results=%d days=%s",
            self._model,
            query,
            max_results,
            days,
        )
        recency_hint = f"\nPrefer sources from the last {days} days." if days else ""
        response = self._client.responses.create(
            model=self._model,
            tools=[{"type": "web_search"}],
            tool_choice="auto",
            include=["web_search_call.action.sources"],
            input=(
                "Search the web for reliable sources that can verify this claim or query.\n"
                "Return concise evidence snippets with source URLs.\n"
                f"Query: {query}"
                f"{recency_hint}"
            ),
        )

        text = _response_text(response)
        sources = _extract_openai_sources(response)
        results = [
            SearchResult(
                url=source.get("url", ""),
                title=source.get("title", ""),
                content=source.get("snippet") or source.get("text") or text,
                score=0.0,
                published_date=source.get("published_date"),
                raw=source,
            )
            for source in sources[:max_results]
            if source.get("url")
        ]
        if results:
            logger.info(
                "openai web_search finished elapsed=%.2fs query=%r sources=%d citations=0",
                perf_counter() - started,
                query,
                len(results),
            )
            return results

        citations = _extract_openai_citations(response)
        citation_results = [
            SearchResult(
                url=citation.get("url", ""),
                title=citation.get("title", ""),
                content=text,
                score=0.0,
                raw=citation,
            )
            for citation in citations[:max_results]
            if citation.get("url")
        ]
        logger.info(
            "openai web_search finished elapsed=%.2fs query=%r sources=0 citations=%d",
            perf_counter() - started,
            query,
            len(citation_results),
        )
        return citation_results


def _response_text(response: Any) -> str:
    text = getattr(response, "output_text", None)
    return text if isinstance(text, str) else ""


def _extract_openai_sources(response: Any) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", None) != "web_search_call":
            continue
        action = getattr(item, "action", None)
        for source in getattr(action, "sources", []) or []:
            source_dict = _object_to_dict(source)
            if source_dict:
                sources.append(source_dict)
    return _dedupe_by_url(sources)


def _extract_openai_citations(response: Any) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", None) != "message":
            continue
        for content in getattr(item, "content", []) or []:
            for annotation in getattr(content, "annotations", []) or []:
                annotation_dict = _object_to_dict(annotation)
                if annotation_dict.get("type") == "url_citation":
                    citations.append(annotation_dict)
    return _dedupe_by_url(citations)


def _object_to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        return dumped if isinstance(dumped, dict) else {}
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return {}


def _dedupe_by_url(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        url = str(item.get("url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(item)
    return deduped


@lru_cache(maxsize=1)
def get_search_client() -> SearchClient:
    """앱 전역에서 사용할 검색 클라이언트 싱글톤.

    Raises:
        ValueError: 선택된 provider의 API key가 설정되지 않은 경우.
    """
    settings = get_settings()
    if settings.search_provider == "openai":
        if not settings.openai_api_key:
            raise ValueError(
                "OPENAI_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요."
            )
        return OpenAIWebSearchClient(
            api_key=settings.openai_api_key,
            model=settings.openai_search_model,
        )

    if not settings.tavily_api_key:
        raise ValueError(
            "TAVILY_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요."
        )
    return TavilySearchClient(api_key=settings.tavily_api_key)
