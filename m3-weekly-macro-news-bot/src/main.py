from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests

CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
GEMINI_MODEL = "gemini-3.6-flash"
GEMINI_FALLBACK_MODEL = "gemini-3.5-flash-lite"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"
LAGOS = ZoneInfo("Africa/Lagos")
TARGET_CURRENCIES = {"USD", "EUR", "GBP"}


def fetch_calendar() -> list[dict[str, Any]]:
    response = requests.get(CALENDAR_URL, timeout=30, headers={"User-Agent": "M3-Weekly-Macro-News-Bot/1.3"})
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        raise ValueError("Forex Factory calendar response was not a list")
    return data


def is_relevant(event: dict[str, Any]) -> bool:
    currency = str(event.get("country", "")).upper()
    impact = str(event.get("impact", "")).strip().lower()
    return currency in TARGET_CURRENCIES and impact in {"high", "medium"}


def parse_date(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def impact_dot(event: dict[str, Any]) -> str:
    return "🔴" if str(event.get("impact", "")).strip().lower() == "high" else "🟠"


def format_events(events: list[dict[str, Any]]) -> str:
    rows = []
    for event in sorted(events, key=lambda item: parse_date(item["date"])):
        local_time = parse_date(event["date"]).astimezone(LAGOS)
        rows.append(" | ".join([
            local_time.strftime("%a %d %b %H:%M"),
            str(event.get("country", "")),
            str(event.get("impact", "")),
            str(event.get("title", "")),
            f"Forecast={event.get('forecast') or 'N/A'}",
            f"Previous={event.get('previous') or 'N/A'}",
        ]))
    return "\n".join(rows)


def build_prompt(events_text: str) -> str:
    today = datetime.now(LAGOS).strftime("%A, %d %B %Y")
    return f"""You are the macroeconomic news analyst for M3 Capital.

Today is {today}. Create a clean, readable WEEKLY MACRO NEWS BRIEF from the supplied economic calendar.

SCOPE:
- News and macro intelligence only.
- NEVER give buy/sell signals, entries, stop losses, take profits, or trade instructions.
- Focus on USD, EUR/USD, and GOLD. Include GBP only when materially relevant to GBP/USD.
- Use only Medium and High impact events.
- Do not discuss Low impact events.
- Do not present macro reactions as guaranteed.

For each important event, use:
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
Key caveat: ...

Prioritize events that can materially change the macro picture. Do not waste space repeating minor duplicates.

Then add:
WEEK AHEAD
USD WATCH
EUR WATCH
GOLD WATCH
MACRO SUMMARY

Formatting:
- Plain text only.
- Short paragraphs and bullets.
- Blank line between events.
- Telegram-friendly and concise.
- No tables, URLs, citations, or trading instructions.

CALENDAR DATA:
{events_text}
"""


def call_gemini(model: str, prompt: str, api_key: str) -> str:
    url = GEMINI_URL.format(model=model)
    response = requests.post(
        url,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"maxOutputTokens": 4500}},
        timeout=90,
    )
    if not response.ok:
        raise RuntimeError(f"Gemini {model} returned HTTP {response.status_code}: {response.text[:800]}")
    payload = response.json()
    try:
        return payload["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected Gemini response: {str(payload)[:1000]}") from exc


def generate_report(events: list[dict[str, Any]]) -> str:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    prompt = build_prompt(format_events(events))
    try:
        report = call_gemini(GEMINI_MODEL, prompt, api_key)
        print(f"Gemini analysis generated with {GEMINI_MODEL}.")
        return report
    except Exception as primary_error:
        print(f"Primary Gemini model failed: {primary_error}", file=sys.stderr)
    try:
        report = call_gemini(GEMINI_FALLBACK_MODEL, prompt, api_key)
        print(f"Gemini analysis generated with fallback model {GEMINI_FALLBACK_MODEL}.")
        return report
    except Exception as fallback_error:
        print(f"Fallback Gemini model failed: {fallback_error}", file=sys.stderr)
        raise RuntimeError("All Gemini analysis attempts failed") from fallback_error


def fallback_report(events: list[dict[str, Any]]) -> str:
    lines = [datetime.now(LAGOS).strftime("Week generated %d %B %Y"), "", "AI analysis was unavailable. Calendar data only:", ""]
    for event in sorted(events, key=lambda item: parse_date(item["date"])):
        local_time = parse_date(event["date"]).astimezone(LAGOS)
        lines.extend([
            f"{impact_dot(event)} {event.get('title', '')}",
            f"{local_time.strftime('%a %d %b %H:%M')} | {event.get('country', '')}",
            f"Forecast: {event.get('forecast') or 'N/A'} | Previous: {event.get('previous') or 'N/A'}",
            "",
        ])
    return "\n".join(lines).rstrip()


def send_telegram(text: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
    chunks = []
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
    for chunk in chunks:
        response = requests.post(TELEGRAM_URL.format(token=token), json={"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True}, timeout=30)
        response.raise_for_status()


def main() -> int:
    try:
        calendar = fetch_calendar()
        events = [event for event in calendar if is_relevant(event)]
        if not events:
            raise RuntimeError("No Medium or High impact USD/EUR/GBP events were found")
        try:
            report = generate_report(events)
        except Exception as ai_error:
            print(f"AI generation failed, using calendar fallback: {ai_error}", file=sys.stderr)
            report = fallback_report(events)
        send_telegram("M3 CAPITAL | WEEKLY MACRO NEWS\n\n" + report)
        print("Weekly macro report sent successfully.")
        return 0
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
