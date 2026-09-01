"""Prompt injection and anomaly detection."""

import re

INJECTION_PATTERNS = [
    re.compile(r"(?i)ignore\s+(all\s+)?(previous|prior)\s+instructions"),
    re.compile(r"(?i)system\s*:\s*you\s+are"),
    re.compile(r"(?i)disregard\s+(the\s+)?(policy|rules|guardrails)"),
    re.compile(r"(?i)charge\s+₹?0"),
    re.compile(r"(?i)free\s+order"),
]


def detect_injection(text: str) -> tuple[bool, list[str]]:
    flags = []
    for pattern in INJECTION_PATTERNS:
        if pattern.search(text):
            flags.append(pattern.pattern)
    return bool(flags), flags


def anomaly_score(text: str, flags: list[str]) -> float:
    score = 0.0
    if flags:
        score += 0.6
    if len(text) > 2000:
        score += 0.2
    if text.count("\n") > 30:
        score += 0.15
    return min(score, 1.0)
