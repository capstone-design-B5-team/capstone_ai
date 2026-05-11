"""LLM 출력 JSON 파싱.

LLM이 가끔 마크다운 코드블록으로 감싸거나 앞뒤에 설명을 붙이므로,
다단계 fallback으로 최대한 복구를 시도한다.
"""

from __future__ import annotations

import json
import re
from typing import Any


def parse_json_with_fallback(raw: str) -> Any | None:
    """LLM 출력에서 JSON 객체/배열을 추출.

    파싱 성공 시 파싱된 객체, 모든 단계 실패 시 None 반환.
    호출 측에서 None 처리(빈 결과로 처리하거나 에러 로깅)는 알아서 한다.

    파싱 단계:
        1. 그대로 ``json.loads``
        2. 마크다운 코드블록(```json ... ```) 제거 후 재시도
        3. 첫 ``[`` 또는 ``{`` 부터 마지막 ``]`` 또는 ``}`` 추출 후 재시도
    """
    if not raw or not raw.strip():
        return None

    # 1차: 그대로
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 2차: 코드블록 제거
    cleaned = _strip_code_fences(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 3차: 첫 괄호 ~ 마지막 괄호 추출
    extracted = _extract_outermost_braces(cleaned)
    if extracted:
        try:
            return json.loads(extracted)
        except json.JSONDecodeError:
            pass

    return None


def _strip_code_fences(text: str) -> str:
    """마크다운 코드블록 펜스 제거."""
    text = text.strip()
    text = re.sub(r"^```(?:json|JSON)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def _extract_outermost_braces(text: str) -> str | None:
    """첫 ``[`` 또는 ``{`` 부터 마지막 짝까지 추출.

    배열/객체 모두 지원. 문자열 내부의 괄호는 고려하지 않는 단순 휴리스틱이지만
    LLM 출력에 한정한 fallback이라 충분.
    """
    array_match = re.search(r"\[.*\]", text, re.DOTALL)
    object_match = re.search(r"\{.*\}", text, re.DOTALL)

    # 더 먼저 시작하는 쪽 우선 (배열이 보통 우리 케이스)
    candidates = [m for m in (array_match, object_match) if m is not None]
    if not candidates:
        return None
    candidates.sort(key=lambda m: m.start())
    return candidates[0].group()
