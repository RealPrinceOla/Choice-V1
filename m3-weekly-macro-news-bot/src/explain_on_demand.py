from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

CACHE_URL = "https://raw.githubusercontent.com/RealPrinceOla/Choice-V1/main/m3-weekly-macro-news-bot/data/calendar_cache.json"
CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
STATE_PATH = Path(__file__).resolve().parents[1] / "data" / "explanation_state.json"
GEMINI_MODEL = "gemini-3.6-flash"
GEMINI_FALLBACK_MODEL = "gemini-3.5-flash-lite"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TELEGRAM_URL = "https://api.telegram.org/bot{token}/{method}"
LAGOS = ZoneInfo("Africa/Lagos")
TARGET_CURRENCIES = {"USD", "EUR", "GBP"}


def fetch_calendar_from(url: str) -> list[dict[str, Any]]:
    response = requests.get(
        url,
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"},
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        raise ValueError("Calendar response was not a list")
    return data


def is_relevant(event: dict[str, Any]) -> bool:
    currency = str(event.get("country", "")).upper()
    impact = str(event.get("impact", "")).strip().lower()
    return currency in TARGET_CURRENCIES and impact in {"high", "medium"}


def parse_date(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_upcoming_events() -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    for url in [CACHE_URL, CALENDAR_URL]:
        try:
            calendar = fetch_calendar_from(url)
        except Exception as error:
            print(f"Calendar source failed: {url} : {error}", file=sys.stderr)
            continue
        filtered = [event for event in calendar if is_relevant(event) and parse_date(event["date"]) >= now]
        print(f"Checked {url} and found {len(filtered)} relevant upcoming events.")
        if filtered:
            return filtered
    return []


def build_explanation_prompt(events: list[dict[str, Any]]) -> str:
    today = datetime.now(LAGOS).strftime("%A, %d %B %Y")
    events_text = "\n".join(
        " | ".join([
            parse_date(event["date"]).astimezone(LAGOS).strftime("%a %d %b %H:%M"),
            str(event.get("country", "")),
            str(event.get("impact", "")),
            str(event.get("title", "")),
            f"Forecast={event.get('forecast') or 'N/A'}",
            f"Previous={event.get('previous') or 'N/A'}",
        ])
        for event in sorted(events, key=lambda item: parse_date(item["date"]))
    )
    return f"""You are the macroeconomic news analyst for M3 Capital.

Today is {today}. A user requested a detailed explanation of this week's Medium and High impact macroeconomic calendar.

PURPOSE:
- Explain what the week's relevant news means and why it matters.
- This is macroeconomic intelligence only.
- NEVER give buy/sell signals, entries, stop losses, take profits, position sizes, or trade instructions.
- Focus on USD, EUR/USD, GOLD, and GBP/USD when GBP is materially relevant.
- Use ONLY the supplied Medium and High impact events.
- Do not omit a materially important eligible event.
- You MAY combine tightly linked releases into one section when they are part of the same economic decision or release window, but mention every component event.
- Do not treat any market reaction as guaranteed.

FOR EVERY EVENT OR TIGHTLY LINKED EVENT GROUP, INCLUDE:
🔴 or 🟠 EVENT NAME
Date/time: ...
Forecast: ... | Previous: ...
What it means: ...
What is expected: ...
If stronger than expected: ...
If weaker than expected: ...
USD: Bullish / Bearish / Mixed / Limited - reason
EUR/USD: Bullish / Bearish / Mixed / Limited - reason
GOLD: Bullish / Bearish / Mixed / Limited - reason
GBP/USD: Bullish / Bearish / Mixed / Limited - reason, when relevant
Key caveat: ...

After the events, include:
WEEK AHEAD
USD WATCH
EUR WATCH
GOLD WATCH
MACRO SUMMARY

Formatting:
- Plain text only.
- Telegram-friendly.
- Blank line between event sections.
- Be detailed enough to be useful, but concise enough to avoid unnecessary repetition.
- No tables, URLs, citations, or trading instructions.

CALENDAR DATA:
{events_text}
"""


def call_gemini(model: str, prompt: str, api_key: str) -> str:
    url = GEMINI_URL.format(model=model)
    response = requests.post(
        url,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 8000},
        },
        timeout=120,
    )
    if not response.ok:
        raise RuntimeError(f"Gemini {model} returned HTTP {response.status_code}: {response.text[:800]}")
    payload = response.json()
    try:
        return payload["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected Gemini response: {str(payload)[:1000]}") from exc


def generate_explanation(events: list[dict[str, Any]]) -> str:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    prompt = build_explanation_prompt(events)
    try:
        report = call_gemini(GEMINI_MODEL, prompt, api_key)
        print(f"Gemini explanation generated with {GEMINI_MODEL}.")
        return report
    except Exception as primary_error:
        print(f"Primary Gemini model failed: {primary_error}", file=sys.stderr)
    try:
        report = call_gemini(GEMINI_FALLBACK_MODEL, prompt, api_key)
        print(f"Gemini explanation generated with fallback model {GEMINI_FALLBACK_MODEL}.")
        return report
    except Exception as fallback_error:
        print(f"Fallback Gemini model failed: {fallback_error}", file=sys.stderr)
        raise RuntimeError("All Gemini analysis attempts failed") from fallback_error


def telegram_call(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
    response = requests.post(
        TELEGRAM_URL.format(token=token, method=method),
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {result}")
    return result


def split_text(text: str, size: int = 3900) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in text.splitlines():
        candidate = line if not current else current + "\n" + line
        if len(candidate) > size:
            if current:
                chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def send_chunks(chat_id: str, text: str) -> list[int]:
    message_ids: list[int] = []
    for chunk in split_text(text):
        result = telegram_call(
            "sendMessage",
            {"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True},
        )
        message = result.get("result") or {}
        message_id = message.get("message_id")
        if isinstance(message_id, int):
            message_ids.append(message_id)
    return message_ids


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    subprocess.run(
        ["git", "config", "user.name", "M3 Bot"],
        check=False,
    )
    subprocess.run(
        ["git", "config", "user.email", "m3bot@users.noreply.github.com"],
        check=False,
    )
    subprocess.run(["git", "add", str(STATE_PATH)], check=False)
    subprocess.run(["git", "commit", "-m", "Update explanation state"], check=False)
    subprocess.run(["git", "push"], check=False)


def main() -> int:
    chat_id = os.getenv("TARGET_CHAT_ID", "").strip()
    if not chat_id:
        print("ERROR: TARGET_CHAT_ID is required", file=sys.stderr)
        return 1

    state = load_state()
    old_ids = state.get(chat_id, [])
    for message_id in old_ids:
        try:
            telegram_call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
        except Exception:
            pass

    try:
        events = load_upcoming_events()
        print(f"Upcoming relevant events: {len(events)}")

        if not events:
            text = "There are no remaining Medium or High impact macroeconomic events scheduled for the rest of this week. Enjoy your weekend, and the bot will post the new weekly calendar on Sunday morning."
        else:
            text = generate_explanation(events)

        message_ids = send_chunks(chat_id, text)
        state[chat_id] = message_ids
        save_state(state)
        print(f"Explanation sent to chat {chat_id} in {len(message_ids)} messages.")
        return 0
    except Exception as error:
        reason = str(error)[:300]
        print(f"ERROR: {reason}", file=sys.stderr)
        try:
            telegram_call(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": f"Explanation failed. Reason: {reason} | Please press the button again in a few minutes.",
                },
            )
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
