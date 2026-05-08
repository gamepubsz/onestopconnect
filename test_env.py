"""Pre-flight environment connectivity check.

Run this before launching any agents to verify that:
    1. All required environment variables are present.
    2. The Groq API key works (sends a tiny "say hello" completion).
    3. The Apify token works (calls the user-info endpoint).
    4. The Slack incoming webhook works (posts a confirmation message).

Usage:
    python test_env.py

Exit code is 0 only when every check passes, otherwise 1.
"""

from __future__ import annotations

import os
import sys
from typing import Callable, List, Tuple

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import requests


REQUIRED_ENV_VARS = [
    "GROQ_API_KEY",
    "APIFY_TOKEN",
    "SLACK_WEBHOOK_URL",
]

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _status(passed: bool) -> str:
    return f"{GREEN}PASS{RESET}" if passed else f"{RED}FAIL{RESET}"


def _print_result(name: str, passed: bool, detail: str = "") -> None:
    line = f"[{_status(passed)}] {name}"
    if detail:
        line += f" - {detail}"
    print(line)


def check_env_vars() -> Tuple[bool, str]:
    missing = [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]
    if missing:
        return False, f"missing: {', '.join(missing)}"
    return True, f"all {len(REQUIRED_ENV_VARS)} variables present"


def check_groq() -> Tuple[bool, str]:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return False, "GROQ_API_KEY not set"

    model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

    try:
        from groq import Groq
    except ImportError:
        return False, "groq package not installed (pip install groq)"

    try:
        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "say hello"}],
            max_tokens=16,
            temperature=0,
        )
        reply = (response.choices[0].message.content or "").strip()
        snippet = reply[:60].replace("\n", " ") if reply else "(empty reply)"
        return True, f"model={model} reply='{snippet}'"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def check_apify() -> Tuple[bool, str]:
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        return False, "APIFY_TOKEN not set"

    try:
        resp = requests.get(
            "https://api.apify.com/v2/users/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
    except requests.RequestException as exc:
        return False, f"network error: {exc}"

    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code}: {resp.text[:120]}"

    try:
        data = resp.json().get("data", {})
        username = data.get("username") or data.get("id") or "unknown"
    except ValueError:
        username = "unknown"
    return True, f"authenticated as '{username}'"


def check_slack() -> Tuple[bool, str]:
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook:
        return False, "SLACK_WEBHOOK_URL not set"

    try:
        resp = requests.post(
            webhook,
            json={"text": "Environment test passed"},
            timeout=15,
        )
    except requests.RequestException as exc:
        return False, f"network error: {exc}"

    if resp.status_code != 200 or resp.text.strip() != "ok":
        return False, f"HTTP {resp.status_code}: {resp.text[:120]}"
    return True, "posted 'Environment test passed' to webhook"


def main() -> int:
    checks: List[Tuple[str, Callable[[], Tuple[bool, str]]]] = [
        ("Environment variables", check_env_vars),
        ("Groq API", check_groq),
        ("Apify API", check_apify),
        ("Slack webhook", check_slack),
    ]

    print(f"{BOLD}Environment connectivity check{RESET}")
    print("-" * 60)

    results: List[Tuple[str, bool, str]] = []
    for name, fn in checks:
        try:
            passed, detail = fn()
        except Exception as exc:
            passed, detail = False, f"unexpected error: {type(exc).__name__}: {exc}"
        _print_result(name, passed, detail)
        results.append((name, passed, detail))

    print("-" * 60)
    total = len(results)
    passed_count = sum(1 for _, ok, _ in results if ok)
    summary_color = GREEN if passed_count == total else (YELLOW if passed_count else RED)
    print(f"{summary_color}{BOLD}Summary: {passed_count}/{total} checks passed{RESET}")

    if passed_count != total:
        failing = [name for name, ok, _ in results if not ok]
        print(f"{RED}Failing: {', '.join(failing)}{RESET}")
        return 1

    print(f"{GREEN}All checks passed - safe to run agents.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
