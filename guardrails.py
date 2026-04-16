import re
import logging

# Set up simple logging for guardrails
logger = logging.getLogger(__name__)

# Basic prompt injection signatures
INJECTION_KEYWORDS = [
    "ignore previous instructions",
    "ignore all previous instructions",
    "system prompt",
    "bypass instructions",
    "jailbreak",
    "developer mode",
    "dan", # "Do Anything Now"
    "forget all instructions",
    "disregard previous",
]

INJECTION_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(kw) for kw in INJECTION_KEYWORDS) + r")\b", 
    re.IGNORECASE
)

# Basic data leakage / secret protection patterns
# e.g., standard OpenAI keys and AWS AKIA keys
LEAK_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9]{30,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (?:RSA|OPENSSH|DSA|EC|PGP) PRIVATE KEY-----")
]

def validate_input(messages: list[dict[str, str]]) -> None:
    """
    Scans the input messages for common prompt injection patterns.
    Raises ValueError if an injection attempt is detected.
    """
    for msg in messages:
        if msg.get("role") == "system":
            continue
            
        content = msg.get("content", "")
        if not content:
            continue
            
        match = INJECTION_PATTERN.search(content)
        if match:
            keyword = match.group(0).lower()
            logger.warning(f"Guardrail triggered: Prompt injection attempt detected ({keyword})")
            raise ValueError("Guardrail blocked request: Possible prompt injection detected.")

def validate_output(response: str) -> None:
    """
    Scans the LLM output for data leakage, formatting abuses, or confidential info.
    Raises ValueError if unauthorized data leakage is detected.
    """
    if not response:
        return
        
    for pattern in LEAK_PATTERNS:
        if pattern.search(response):
            logger.warning("Guardrail triggered: Potential data leakage (secrets/keys) detected in output.")
            raise ValueError("Guardrail blocked response: Suspected data leakage.")
