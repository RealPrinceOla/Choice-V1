from __future__ import annotations

import json
import os
import subprocess
import sys
import time
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


DIVIDER = "━━━━━━━━━━━━━━━━━━"


def local_time(event: dict[str, Any]) -> datetime:
    return parse_date(event["date"]).astimezone(LAGOS)


def group_by_day(events: list[dict[str, Any]]) -> list[tuple[datetime, list[dict[str, Any]]]]:
    groups: list[tuple[datetime, list[dict[str, Any]]]] = []
    for event in sorted(events, key=local_time):
        when = local_time(event)
        if groups and groups[-1][0].date() == when.date():
            groups[-1][1].append(event)
        else:
            groups.append((when, [event]))
    return groups


def day_header(day: datetime, count: int | None = None) -> str:
    label = f"📅 {day.strftime('%a %d %b').upper()}"
    if count is not None:
        label += f"  ({count} event{'' if count == 1 else 's'})"
    return label


def split_blocks(text: str, size: int = 3900) -> list[str]:
    """Split text for Telegram, breaking only before a day divider when possible."""
    blocks: list[str] = []
    current: list[str] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        starts_day = line == DIVIDER and index + 1 < len(lines) and lines[index + 1].startswith("📅 ")
        if starts_day and current:
            blocks.append("\n".join(current))
            current = []
        current.append(line)
    if current:
        blocks.append("\n".join(current))

    chunks: list[str] = []
    buffer = ""
    for block in blocks:
        candidate = block if not buffer else buffer.rstrip() + "\n\n" + block
        if len(candidate) <= size:
            buffer = candidate
            continue
        if buffer:
            chunks.append(buffer.rstrip())
        buffer = ""
        if len(block) <= size:
            buffer = block
            continue
        for line in block.splitlines():
            candidate = line if not buffer else buffer + "\n" + line
            if len(candidate) > size:
                chunks.append(buffer.rstrip())
                buffer = line
            else:
                buffer = candidate
    if buffer.strip():
        chunks.append(buffer.rstrip())
    return chunks


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
    sections = []
    for day, items in group_by_day(events):
        rows = "\n".join(
            " | ".join([
                local_time(event).strftime("%H:%M"),
                str(event.get("country", "")),
                str(event.get("impact", "")),
                str(event.get("title", "")),
                f"Forecast={event.get('forecast') or 'N/A'}",
                f"Previous={event.get('previous') or 'N/A'}",
            ])
            for event in items
        )
        sections.append(f"{DIVIDER}\n{day_header(day)}\n{DIVIDER}\n{rows}")
    events_text = "\n\n".join(sections)
    return f"""You are the macroeconomic news analyst for M3 Capital FX.

Today is {today}. A user requested a detailed explanation of this week's Medium and High impact macroeconomic calendar. All times are WAT (Lagos).

PURPOSE:
- Explain what the week's relevant news means and why it matters.
- This is macroeconomic intelligence only.
- NEVER give buy/sell signals, entries, stop losses, take profits, position sizes, or trade instructions.
- Focus on USD, EUR/USD, GOLD, and GBP/USD when GBP is materially relevant.
- Use ONLY the supplied Medium and High impact events.
- Do not omit a materially important eligible event.
- You MAY combine tightly linked releases into one section when they are part of the same economic decision or release window, but mention every component event.
- Do not treat any market reaction as guaranteed.

LAYOUT (follow exactly):
- The calendar data below is grouped by day. Each day starts with a 3-line header: a divider line, a day line starting with 📅, and another divider line.
- Reproduce every day header EXACTLY as given (same characters, same order), then explain only that day's events underneath it.
- Never mix events from different days under one header, and never add headers for days that are not in the data.

FOR EVERY EVENT OR TIGHTLY LINKED EVENT GROUP, INCLUDE:
🔴 or 🟠 HH:MM | CURRENCY | EVENT NAME
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

AFTER THE LAST DAY, output this exact 3-line header:
{DIVIDER}
🧭 WEEK SUMMARY
{DIVIDER}
Then include:
WEEK AHEAD
USD WATCH
EUR WATCH
GOLD WATCH
MACRO SUMMARY

Formatting:
- Plain text only. No markdown, no asterisks, no hashes.
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


def send_chunks(chat_id: str, text: str) -> list[int]:
    message_ids: list[int] = []
    for chunk in split_blocks(text):
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


def git(*args: str) -> int:
    return subprocess.run(["git", *args], check=False).returncode


def save_state(chat_id: str, message_ids: list[int]) -> None:
    git("config", "user.name", "M3 Bot")
    git("config", "user.email", "m3bot@users.noreply.github.com")
    for attempt in range(5):
        git("fetch", "origin", "main")
        git("reset", "--hard", "origin/main")
        state = load_state()
        state[chat_id] = message_ids
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        git("add", str(STATE_PATH))
        if git("diff", "--cached", "--quiet") == 0:
            print("Explanation state unchanged.")
            return
        git("commit", "-m", "Update explanation state")
        if git("push", "origin", "HEAD:main") == 0:
            print(f"Explanation state saved on attempt {attempt + 1}.")
            return
        print(f"State push rejected, retrying (attempt {attempt + 1}).", file=sys.stderr)
        time.sleep(3 * (attempt + 1))
    print("WARNING: could not save explanation state after 5 attempts.", file=sys.stderr)


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
            text = "M3 CAPITAL FX | WEEKLY MACRO EXPLANATION\nAll times WAT (Lagos)\n\n" + generate_explanation(events)

        message_ids = send_chunks(chat_id, text)
        save_state(chat_id, message_ids)
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
