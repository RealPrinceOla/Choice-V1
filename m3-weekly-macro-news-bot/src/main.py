from __future__ import annotations

import html
import os
import sys
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests

CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"
LAGOS = ZoneInfo("Africa/Lagos")

TARGET_CURRENCIES = {"USD", "EUR", "GBP"}
HIGH_VALUE_KEYWORDS = (
    "interest rate", "refinancing", "monetary policy", "policy statement",
    "press conference", "central bank", "cpi", "inflation", "ppi",
    "gdp", "employment", "payroll", "unemployment", "retail sales",
    "pmi", "manufacturing", "services", "consumer confidence",
    "consumer sentiment", "inflation expectations", "wage", "earnings",
    "trade balance", "bond auction", "fed", "ecb", "boe", "bank rate",
)


def fetch_calendar() -> list[dict[str, Any]]:
    response = requests.get(
        CALENDAR_URL,
        timeout=30,
        headers={"User-Agent": "M3-Weekly-Macro-News-Bot/1.0"},
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        raise ValueError("Forex Factory calendar response was not a list")
    return data


def is_relevant(event: dict[str, Any]) -> bool:
    currency = str(event.get("country", "")).upper()
    impact = str(event.get("impact", "")).lower()
    title = str(event.get("title", "")).lower()

    if currency not in TARGET_CURRENCIES:
        return False
    if impact in {"high", "medium"}:
        return True
    return any(keyword in title for keyword in HIGH_VALUE_KEYWORDS)


def parse_date(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def format_events(events: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    for event in sorted(events, key=lambda item: parse_date(item["date"])):
        local_time = parse_date(event["date"]).astimezone(LAGOS)
        rows.append(
            " | ".join(
                [
                    local_time.strftime("%a %d %b %H:%M"),
                    str(event.get("country", "")),
                    str(event.get("impact", "")),
                    str(event.get("title", "")),
                    f"Forecast={event.get('forecast') or 'N/A'}",
                    f"Previous={event.get('previous') or 'N/A'}",
                ]
            )
        )
    return "\n".join(rows)


def build_prompt(events_text: str) -> str:
    today = datetime.now(LAGOS).strftime("%A, %d %B %Y")
    return f"""You are the macroeconomic news analyst for M3 Capital.

Today is {today}. Produce the M3 WEEKLY MACRO NEWS BRIEF from the supplied economic calendar.

IMPORTANT SCOPE:
- This is a NEWS AND MACRO INTELLIGENCE REPORT.
- Do NOT give buy signals, sell signals, entries, stop losses, take profits, or trade instructions.
- Do NOT tell the reader to buy or sell anything.
- Explain information and possible macroeconomic reactions only.
- Do not pretend a macro relationship is guaranteed. Explain uncertainty when appropriate.
- Focus on USD, EUR, and GOLD. Include GBP context when a GBP event materially matters to GBP/USD.
- Prioritize genuinely important events. Do not waste space explaining every minor calendar item.

For each important event, explain:
1. What the event is and why it matters.
2. What the market is currently expecting, using the forecast and previous values when available.
3. What a stronger-than-expected result could generally mean.
4. What a weaker-than-expected result could generally mean.
5. Potential impact on USD: Bullish / Bearish / Mixed / Limited, with a short reason.
6. Potential impact on EUR: Bullish / Bearish / Mixed / Limited, with a short reason.
7. Potential impact on GOLD: Bullish / Bearish / Mixed / Limited, with a short reason.
8. What could invalidate the simple textbook reaction, such as revisions, bond yields, central-bank guidance, positioning, or an already-priced expectation.

Then provide:
- WEEK AHEAD: the 3 to 7 most important macro themes/events.
- USD WATCH: the main USD-sensitive events.
- EUR WATCH: the main EUR-sensitive events.
- GOLD WATCH: the main macro drivers for gold this week.
- MACRO SUMMARY: a short plain-English summary of what matters most this week.

Use clear headings and concise bullet points. Use plain text only. Do not use Markdown tables. Do not include citations or URLs.

CALENDAR DATA:
{events_text}
"""


def generate_report(events: list[dict[str, Any]]) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    prompt = build_prompt(format_events(events))
    url = GEMINI_URL.format(model=GEMINI_MODEL)
    response = requests.post(
        url,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.25,
                "maxOutputTokens": 6000,
            },
        },
        timeout=90,
    )
    response.raise_for_status()
    payload = response.json()
    try:
        return payload["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected Gemini response: {payload}") from exc


def fallback_report(events: list[dict[str, Any]]) -> str:
    lines = [
        "M3 CAPITAL WEEKLY MACRO NEWS BRIEF",
        datetime.now(LAGOS).strftime("Week generated %d %B %Y"),
        "",
        "AI analysis was unavailable, so this is the calendar-only fallback.",
        "",
    ]
    for event in sorted(events, key=lambda item: parse_date(item["date"])):
        local_time = parse_date(event["date"]).astimezone(LAGOS)
        lines.append(
            f"• {local_time.strftime('%a %d %b %H:%M')} | {event.get('country')} | "
            f"{event.get('impact')} | {event.get('title')} | "
            f"Forecast: {event.get('forecast') or 'N/A'} | Previous: {event.get('previous') or 'N/A'}"
        )
    return "\n".join(lines)


def send_telegram(text: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")

    # Telegram text messages have a practical size limit. Split at line boundaries.
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

    for chunk in chunks:
        response = requests.post(
            TELEGRAM_URL.format(token=token),
            json={
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        response.raise_for_status()


def main() -> int:
    try:
        calendar = fetch_calendar()
        events = [event for event in calendar if is_relevant(event)]
        if not events:
            raise RuntimeError("No relevant USD/EUR/GBP events were found in the weekly calendar")

        try:
            report = generate_report(events)
        except Exception as ai_error:
            print(f"AI generation failed, using calendar fallback: {ai_error}", file=sys.stderr)
            report = fallback_report(events)

        header = "M3 CAPITAL | WEEKLY MACRO NEWS\n\n"
        send_telegram(header + report)
        print("Weekly macro report sent successfully.")
        return 0
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
