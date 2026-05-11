"""Search policy helpers shared by verification nodes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from ai_backend.core.search import SearchResult
from ai_backend.graph.state import Claim

SearchLanguage = Literal["ko", "en"]

_KOREAN_RE = re.compile(r"[가-힣]")
_YEAR_RE = re.compile(r"(?:19|20)\d{2}년?|올해|내년|최근|현재|장기|중기")
_SITE_OPERATOR_RE = re.compile(r"\s+site:\S+", re.IGNORECASE)
_KR_REGION_TERMS = (
    "한국",
    "대한민국",
    "전국",
    "수도권",
    "서울",
    "부산",
    "대구",
    "인천",
    "광주",
    "대전",
    "울산",
    "세종",
    "경기",
    "강원",
    "충북",
    "충남",
    "전북",
    "전남",
    "경북",
    "경남",
    "제주",
)
_KR_INSTITUTION_TERMS = (
    "통계청",
    "한국은행",
    "환경부",
    "보건복지부",
    "교육부",
    "고용노동부",
    "국토교통부",
    "산업통상자원부",
    "금융위원회",
    "기획재정부",
    "질병관리청",
    "식약처",
)
_KR_OFFICIAL_DOMAINS = (
    "go.kr",
    "or.kr",
    "ac.kr",
    "re.kr",
    "korea.kr",
    "bok.or.kr",
    "kostat.go.kr",
    "moef.go.kr",
    "me.go.kr",
    "molit.go.kr",
)
_GLOBAL_OFFICIAL_DOMAINS = (
    ".gov",
    ".edu",
    "oecd.org",
    "worldbank.org",
    "who.int",
    "imf.org",
    "un.org",
)
_LOW_QUALITY_DOMAINS = (
    "youtube.com",
    "youtu.be",
    "namu.wiki",
    "wikipedia.org",
    "blog.naver.com",
    "tistory.com",
)


@dataclass(frozen=True, slots=True)
class SearchProfile:
    """Lightweight search intent extracted from a claim."""

    language: SearchLanguage
    country_hint: str | None
    region_terms: list[str]
    institution_terms: list[str]
    time_terms: list[str]
    official_domains: tuple[str, ...]
    blocked_domains: tuple[str, ...] = _LOW_QUALITY_DOMAINS


@dataclass(frozen=True, slots=True)
class RankedSearchResult:
    """Search result plus policy score for debug metadata."""

    result: SearchResult
    score: float
    reasons: list[str]


def build_search_profile(claim: Claim) -> SearchProfile:
    """Extract general search policy from claim text and context."""
    text = f"{claim['text']} {claim.get('context', '')}"
    is_korean = bool(_KOREAN_RE.search(text))
    region_terms = _terms_present(text, _KR_REGION_TERMS) if is_korean else []
    institution_terms = _terms_present(text, _KR_INSTITUTION_TERMS) if is_korean else []
    time_terms = _dedupe(_YEAR_RE.findall(text))
    country_hint = "KR" if is_korean and (region_terms or institution_terms) else None
    official_domains = _KR_OFFICIAL_DOMAINS if country_hint == "KR" else _GLOBAL_OFFICIAL_DOMAINS
    return SearchProfile(
        language="ko" if is_korean else "en",
        country_hint=country_hint,
        region_terms=region_terms,
        institution_terms=institution_terms,
        time_terms=time_terms,
        official_domains=official_domains,
    )


def fallback_queries(claim: Claim, profile: SearchProfile, *, latest: bool = False) -> list[str]:
    """Build conservative fallback queries when a verifier does not provide any."""
    preserved_terms = [*profile.region_terms, *profile.institution_terms, *profile.time_terms]
    preserved = " ".join(_dedupe(preserved_terms))
    if profile.language == "ko":
        official_hint = "공식 통계 발표 보도자료"
        if preserved:
            return [
                f"{preserved} {'최신 현황 변경' if latest else '공식 자료'}",
                f"{claim['text']} {official_hint}",
            ]
        return [
            f"{claim['text']} {official_hint}",
            f"{claim['text']} 2025 2026 변경" if latest else claim["text"],
        ]

    if latest:
        return [
            f"{claim['text']} latest update official source {' '.join(profile.time_terms)}",
            f"{claim['text']} 2025 2026 official data",
        ]
    return [f"{claim['text']} official source", claim["text"]]


def expand_official_queries(
    queries: list[str],
    profile: SearchProfile,
    claim_text: str,
    *,
    max_extra_queries: int = 2,
) -> list[str]:
    """Create official-domain backup queries for the current claim."""
    if profile.country_hint != "KR":
        return []

    domains = _preferred_kr_domains(profile, claim_text)
    terms = " ".join(
        _dedupe([*profile.region_terms, *profile.institution_terms, *profile.time_terms])
    )
    base = _SITE_OPERATOR_RE.sub("", queries[0] if queries else claim_text).strip()
    seed = f"{terms} {base}".strip()
    expanded: list[str] = []
    for domain in domains:
        query = f"{seed} site:{domain}"
        if query not in expanded:
            expanded.append(query)
        if len(expanded) >= max_extra_queries:
            break
    return expanded


def rank_search_results(
    profile: SearchProfile,
    results: list[SearchResult],
) -> list[RankedSearchResult]:
    """Rank search results using locale, official-domain, and low-quality signals."""
    ranked = [_rank_one(profile, result) for result in results]
    return sorted(ranked, key=lambda item: item.score, reverse=True)


def needs_official_retry(profile: SearchProfile, ranked: list[RankedSearchResult]) -> bool:
    """Return whether official-domain backup search should run."""
    if profile.country_hint != "KR":
        return False
    top = ranked[:3]
    if not top:
        return True
    has_official = any("official_domain" in item.reasons for item in top)
    low_quality_count = sum("low_quality_domain" in item.reasons for item in top)
    foreign_count = sum("foreign_or_non_korean_for_kr_claim" in item.reasons for item in top)
    return not has_official or (low_quality_count + foreign_count) >= 2


def search_policy_metadata(
    profile: SearchProfile,
    *,
    expanded_queries: list[str],
    official_retry: bool,
    ranked: list[RankedSearchResult],
) -> dict[str, object]:
    """Build compact metadata for debugging search-policy decisions."""
    return {
        "search_profile": {
            "language": profile.language,
            "country_hint": profile.country_hint,
            "region_terms": profile.region_terms,
            "institution_terms": profile.institution_terms,
            "time_terms": profile.time_terms,
            "official_domains": list(profile.official_domains),
        },
        "expanded_queries": expanded_queries,
        "official_retry": official_retry,
        "filtered_domains": _dedupe(
            [
                _domain(item.result.url)
                for item in ranked
                if "low_quality_domain" in item.reasons
            ]
        ),
        "ranking": [
            {
                "url": item.result.url,
                "score": round(item.score, 3),
                "reasons": item.reasons,
            }
            for item in ranked[:8]
        ],
    }


def _rank_one(profile: SearchProfile, result: SearchResult) -> RankedSearchResult:
    raw_text = f"{result.title} {result.content}"
    text = raw_text.lower()
    domain = _domain(result.url)
    score = result.score
    reasons: list[str] = []

    if _domain_matches(domain, profile.official_domains):
        score += 3.0
        reasons.append("official_domain")
    if _domain_matches(domain, profile.blocked_domains):
        score -= 4.0
        reasons.append("low_quality_domain")

    if profile.language == "ko":
        if _KOREAN_RE.search(raw_text):
            score += 1.5
            reasons.append("korean_text")
        elif profile.country_hint == "KR":
            score -= 1.25
            reasons.append("foreign_or_non_korean_for_kr_claim")
        if profile.country_hint == "KR" and (
            domain.endswith(".kr") or "korea" in domain or "한국" in text
        ):
            score += 1.0
            reasons.append("korea_signal")
    elif not _KOREAN_RE.search(raw_text):
        score += 1.0
        reasons.append("english_or_non_korean_text")

    for term in profile.region_terms:
        if term.lower() in text:
            score += 1.25
            reasons.append(f"region:{term}")
    for term in profile.institution_terms:
        if term.lower() in text:
            score += 1.5
            reasons.append(f"institution:{term}")
    for term in profile.time_terms:
        if term.lower() in text:
            score += 0.75
            reasons.append(f"time:{term}")
    if result.published_date:
        score += 0.5
        reasons.append("published_date")
    return RankedSearchResult(result=result, score=score, reasons=reasons)


def _preferred_kr_domains(profile: SearchProfile, claim_text: str) -> list[str]:
    text = f"{claim_text} {' '.join(profile.institution_terms)}"
    domains: list[str] = []
    if "한국은행" in text or "기준금리" in text or "금리" in text:
        domains.extend(["bok.or.kr", "korea.kr"])
    if "통계청" in text or "인구" in text or "출산율" in text or "GDP" in text:
        domains.extend(["kostat.go.kr", "korea.kr"])
    if "환경부" in text or "미세먼지" in text or "대기" in text:
        domains.extend(["me.go.kr", "korea.kr"])
    domains.extend(["go.kr", "korea.kr"])
    return _dedupe(domains)


def _terms_present(text: str, terms: tuple[str, ...]) -> list[str]:
    return [term for term in terms if term in text]


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in deduped:
            deduped.append(normalized)
    return deduped


def _domain(url: str) -> str:
    return urlparse(url.lower()).netloc


def _domain_matches(domain: str, patterns: tuple[str, ...]) -> bool:
    return any(
        domain == pattern.lstrip(".")
        or domain.endswith(pattern)
        or domain.endswith(f".{pattern}")
        for pattern in patterns
    )
