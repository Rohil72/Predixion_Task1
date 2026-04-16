import json
import os
import sys
from pathlib import Path
from urllib import error, request

from guardrails import validate_input, validate_output

def load_env_file(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def get_response(api_key: str, model: str, messages: list[dict[str, str]]) -> str:
    validate_input(messages)

    payload = {
        "model": model,
        "messages": messages,
    }

    req = request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost",
            "X-Title": "Simple OpenRouter Python Setup",
        },
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code}: {body}") from exc
    except error.URLError as exc:
        raise SystemExit(f"Request failed: {exc}") from exc

    result_content = result["choices"][0]["message"]["content"].strip()
    validate_output(result_content)
    return result_content


def run_single_prompt(api_key: str, model: str, prompt: str) -> int:
    reply = get_response(api_key, model, [{"role": "user", "content": prompt}])
    print(reply)
    return 0


def run_interactive_chat(api_key: str, model: str) -> int:
    history: list[dict[str, str]] = []

    print(f"Interactive chat started with model: {model}")
    print("Type 'exit' or 'quit' to end the session.")

    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSession ended.")
            return 0

        if not user_input:
            continue

        if user_input.lower() in {"exit", "quit"}:
            print("Session ended.")
            return 0

        history.append({"role": "user", "content": user_input})

        try:
            reply = get_response(api_key, model, history)
        except SystemExit as exc:
            print(exc, file=sys.stderr)
            history.pop()
            continue

        history.append({"role": "assistant", "content": reply})
        print(f"\nAssistant: {reply}")


def main() -> None:
    load_env_file()

    api_key = os.getenv("OPENROUTER_API_KEY")
    model = os.getenv("OPENROUTER_MODEL", "openrouter/free")
    prompt = " ".join(sys.argv[1:]).strip()

    if not api_key or api_key == "your_openrouter_api_key_here":
        raise SystemExit("Set OPENROUTER_API_KEY in .env before running this script.")

    if prompt:
        raise SystemExit(run_single_prompt(api_key, model, prompt))

    raise SystemExit(run_interactive_chat(api_key, model))


if __name__ == "__main__":
    main()
