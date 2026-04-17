import logging
import re
from typing import Any, Iterable, Mapping

logger = logging.getLogger(__name__)

# Phrases commonly used in prompt injection attempts.
# Keep these focused. Over-broad patterns will destroy legitimate workflows.
INJECTION_PHRASES = [
    "ignore previous instructions",
    "ignore all previous instructions",
    "forget all instructions",
    "disregard previous instructions",
    "bypass instructions",
    "developer mode",
    "system prompt",
    "reveal your system prompt",
    "jailbreak",
    "act as if",
    "you are now",
]

# More flexible patterns for variants.
INJECTION_PATTERNS = [
    re.compile(
        r"\bignore\b.{0,40}\b(instructions?|system prompt|prior instructions?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bforget\b.{0,40}\b(instructions?|previous instructions?)\b", re.IGNORECASE
    ),
    re.compile(
        r"\bdisregard\b.{0,40}\b(instructions?|previous instructions?)\b", re.IGNORECASE
    ),
    re.compile(
        r"\bbypass\b.{0,40}\b(instructions?|safety|guardrails?)\b", re.IGNORECASE
    ),
    re.compile(
        r"\breveal\b.{0,40}\b(system prompt|hidden prompt|developer message)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bdeveloper mode\b", re.IGNORECASE),
    re.compile(r"\bjailbreak\b", re.IGNORECASE),
]

# Basic secret / leakage patterns for outputs.
LEAK_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA|OPENSSH|DSA|EC|PGP) PRIVATE KEY-----"),
    re.compile(r"\b(?:xox[baprs]-[A-Za-z0-9-]{10,})\b"),  # Slack-style tokens
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),  # GitHub personal access tokens
]


def _normalize_text(text: str) -> str:
    """Normalize whitespace for easier matching."""
    return re.sub(r"\s+", " ", text or "").strip()


def _looks_like_meta_discussion(text: str) -> bool:
    """
    Heuristic to reduce false positives when a message is clearly *about*
    injections rather than *attempting* them.
    """
    t = _normalize_text(text).lower()

    meta_markers = [
        "for example",
        "example",
        "explain",
        "discussion",
        "research",
        "article",
        "paper",
        "security",
        "prompt injection",
        "jailbreak",
        "quoted",
        "quote",
        "mention",
        "term",
        "phrase",
    ]

    # If the content is explicitly discussing the phrase, we are more cautious.
    if any(marker in t for marker in meta_markers):
        return True

    return False


def _matches_injection(text: str) -> str | None:
    """
    Return the first matched injection signature, or None.
    """
    normalized = _normalize_text(text)

    for phrase in INJECTION_PHRASES:
        if phrase.lower() in normalized.lower():
            return phrase

    for pattern in INJECTION_PATTERNS:
        m = pattern.search(normalized)
        if m:
            return m.group(0)

    return None


def validate_input(
    messages: Iterable[Mapping[str, Any]],
    *,
    scan_roles: tuple[str, ...] = ("user",),
    strict_tool_scanning: bool = False,
) -> None:
    """
    Validate conversation input for prompt injection attempts.

    Default behavior:
    - Scan only user messages.
    - Skip system messages.
    - Skip assistant messages.
    - Skip tool/search outputs unless strict_tool_scanning=True.

    This prevents false positives from retrieval results that merely *mention*
    injection phrases in an informational context.
    """
    for msg in messages:
        role = str(msg.get("role", "")).lower()
        content = msg.get("content", "")

        if not content or not isinstance(content, str):
            continue

        # Default: only inspect user messages.
        # This is the key fix for false positives from tool outputs.
        if role not in scan_roles:
            if strict_tool_scanning and role in ("tool", "function", "search"):
                pass
            else:
                continue

        match = _matches_injection(content)
        if not match:
            continue

        # Reduce false positives for content that's clearly discussing the topic.
        # Still not perfect, but much less trigger-happy.
        if _looks_like_meta_discussion(content) and role != "user":
            logger.info(
                "Guardrail noted possible injection-like phrase in non-user content: %r",
                match,
            )
            continue

        logger.warning(
            "Guardrail triggered: prompt injection-like content detected in %s message (%r)",
            role,
            match,
        )
        raise ValueError(
            "Guardrail blocked request: possible prompt injection detected."
        )


def validate_output(response: str) -> None:
    """
    Validate model output for obvious secret leakage / formatting abuse.

    This is intentionally stricter than input scanning because outputs should
    not contain credentials, private keys, or similar secrets.
    """
    if not response or not isinstance(response, str):
        return

    normalized = _normalize_text(response)

    for pattern in LEAK_PATTERNS:
        if pattern.search(normalized):
            logger.warning(
                "Guardrail triggered: potential secret leakage detected in output."
            )
            raise ValueError("Guardrail blocked response: suspected data leakage.")

    # Optional: catch direct requests to reveal internal prompts in generated output.
    prompt_exposure_patterns = [
        re.compile(r"\bmy system prompt is\b", re.IGNORECASE),
        re.compile(r"\bhere is the system prompt\b", re.IGNORECASE),
        re.compile(r"\brevealed the system prompt\b", re.IGNORECASE),
    ]
    for pattern in prompt_exposure_patterns:
        if pattern.search(normalized):
            logger.warning(
                "Guardrail triggered: potential system prompt leakage detected in output."
            )
            raise ValueError("Guardrail blocked response: suspected prompt leakage.")


def validate_tool_output(text: str) -> None:
    """
    Optional validator for tool/search output.

    Use this only if you want to inspect tool content separately from user input.
    It is less strict than validate_input because tool content often contains
    quoted or descriptive mentions of restricted phrases.
    """
    if not text or not isinstance(text, str):
        return

    match = _matches_injection(text)
    if not match:
        return

    # Tool output is usually informational, so we only log by default.
    logger.info(
        "Guardrail noted injection-like phrase in tool output (%r). Not blocking by default.",
        match,
    )
