from __future__ import annotations

import re
from typing import Any

import json5

from .errors import ParseError


def _scan_expression(source: str, start: int) -> str:
    depth = 0
    quote = ""
    escaped = False
    for index in range(start, len(source)):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
        elif char in "[{(":
            depth += 1
        elif char in "]})":
            depth -= 1
        elif char == ";" and depth == 0:
            return source[start:index].strip()
    raise ParseError("unterminated JavaScript assignment")


def extract_assignment(source: str, name: str, default: Any = None) -> Any:
    match = re.search(rf"\bvar\s+{re.escape(name)}\s*=", source)
    if not match:
        return default
    expression = _scan_expression(source, match.end())
    if expression.startswith("new Array(") and expression.endswith(")"):
        expression = "[" + expression[10:-1] + "]"
    try:
        return json5.loads(expression)
    except Exception as exc:
        raise ParseError(f"could not parse JavaScript variable {name}: {exc}") from exc


def extract_int(source: str, name: str, default: int = 0) -> int:
    value = extract_assignment(source, name, default)
    return int(value)


def extract_token(source: str) -> str:
    match = re.search(r"\bg_tid\s*=\s*(\d+)", source)
    if not match:
        raise ParseError("authenticated page did not expose g_tid")
    return match.group(1)


def first(value: Any, default: Any = "") -> Any:
    if isinstance(value, list):
        return value[0] if value else default
    return value if value is not None else default
