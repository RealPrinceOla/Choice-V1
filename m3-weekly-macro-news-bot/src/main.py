from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CALENDAR_CACHE_PATH = Path(__file__).resolve().parents[1] / "data" / "calendar_cache.json"
GEMINI_MODEL = "gemini-3.6-flash"
GEMINI_FALLBACK_MODEL = "gemini-3.5-flash-lite"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TELEGRAM_URL = "https://api.telegram.org/bot{token}/{method}"
LAGOS = ZoneInfo("Africa/Lagos")
TARGET_CURRENCIES = {"USD", "EUR", "GBP"}


def fetch_calendar() -> list[dict[str, Any]]:
    response = requests.get(
        CALENDAR_URL,
        timeout=30,
        headers={"User-Agent": "M3-Weekly-Macro-News-Bot/2.0"},
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        raise ValueError("Forex Factory calendar response was not a list")
    return data


def save_calendar_cache(calendar: list[dict[str, Any]]) -> None:
    CALENDAR_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CALENDAR_CACHE_PATH.write_text(
        json.dumps(calendar, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Calendar cache updated: {CALENDAR_CACHE_PATH}")


def is_relevant(event: dict[str, Any]) -> bool:
    currency = str(event.get("country", "")).upper()
    impact = str(event.get("impact", "")).strip().lower()
    return currency in TARGET_CURRENCIES and impact in {"high", "medium"}


def parse_date(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def impact_dot(event: dict[str, Any]) -> str:
    return "🔴" if str(event.get("impact", "")).strip().lower() == "high" else "🟠"


def build_clean_weekly_message(events: list[dict[str, Any]]) -> str:
    lines = [
        "M3 CAPITAL | WEEKLY MACRO NEWS",
        "",
        f"Week generated {datetime.now(LAGOS).strftime('%d %B %Y')}",
        "",
    ]
    for event in sorted(events, key=lambda item: parse_date(item["date"])):
        local_time = parse_date(event["date"]).astimezone(LAGOS)
        lines.extend([
            f"{impact_dot(event)} {event.get('title', '')}",
            f"{local_time.strftime('%a %d %b %H:%M')} | {event.get('country', '')}",
            f"Forecast: {event.get('forecast') or 'N/A'} | Previous: {event.get('previous') or 'N/A'}",
            "",
        ])
    return "\n".join(lines).rstrip()


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
- NEVER give buy/sell signals, entries, stop losses, take profits, or trade instructions.
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
        timeout=90,
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


def send_telegram(text: str, reply_markup: dict[str, Any] | None = None) -> None:
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not chat_id:
        raise RuntimeError("TELEGRAM_CHAT_ID is required")

    chunks: list[str] = []
    current = ""
    for line in text.splitlines():
        candidate = line if not current else current + "\n" + line
        if len(candidate) > 3900:
            if current:
                chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)

    for index, chunk in enumerate(chunks):
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": chunk,
            "disable_web_page_preview": True,
        }
        if reply_markup is not None and index == len(chunks) - 1:
            payload["reply_markup"] = reply_markup
        telegram_call("sendMessage", payload)


def send_to_chat(chat_id: str | int, text: str) -> list[int]:
    chunks: list[str] = []
    current = ""
    for line in text.splitlines():
        candidate = line if not current else current + "\n" + line
        if len(candidate) > 3900:
            if current:
                chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)

    message_ids: list[int] = []
    for chunk in chunks:
        result = telegram_call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            },
        )
        message = result.get("result") or {}
        message_id = message.get("message_id")
        if isinstance(message_id, int):
            message_ids.append(message_id)
    return message_ids


def weekly_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [[
            {"text": "📖 EXPLAIN THIS WEEK'S NEWS", "callback_data": "explain_week"}
        ]]
    }


def main() -> int:
    try:
        calendar = fetch_calendar()
        save_calendar_cache(calendar)
        events = [event for event in calendar if is_relevant(event)]
        if not events:
            raise RuntimeError("No Medium or High impact USD/EUR/GBP events were found")

        message = build_clean_weekly_message(events)
        send_telegram(message, reply_markup=weekly_keyboard())
        print("Weekly macro calendar sent successfully.")
        return 0
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
