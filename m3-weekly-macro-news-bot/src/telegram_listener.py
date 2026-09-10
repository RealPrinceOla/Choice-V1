from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from main import fetch_calendar, generate_explanation, is_relevant, send_to_chat

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
POLL_SECONDS = 240
LONG_POLL_TIMEOUT = 25
EXPLANATION_TTL_SECONDS = 3600
GENERATION_STALE_SECONDS = 900
STATE_FILE = Path(__file__).resolve().parent.parent / "state" / "explain_state.json"


def token() -> str:
    value = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not value:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
    return value


def target_chat_id() -> str:
    value = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not value:
        raise RuntimeError("TELEGRAM_CHAT_ID is required")
    return value


def telegram_call_local(method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    response = requests.post(
        TELEGRAM_API.format(token=token(), method=method),
        json=payload or {},
        timeout=LONG_POLL_TIMEOUT + 10,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {data}")
    return data


def register_commands() -> None:
    telegram_call_local(
        "setMyCommands",
        {
            "commands": [
                {"command": "explain", "description": "Explain this week's macro news"}
            ]
        },
    )


def answer_callback(callback_id: str, text: str) -> None:
    telegram_call_local(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id,
            "text": text,
            "show_alert": False,
        },
    )


def empty_state() -> dict[str, Any]:
    return {
        "chat_id": None,
        "message_ids": [],
        "expires_at": None,
        "status": "idle",
        "generation_started_at": None,
    }


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return empty_state()
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return empty_state()
        ids = data.get("message_ids", [])
        if not isinstance(ids, list):
            ids = []
        return {
            "chat_id": data.get("chat_id"),
            "message_ids": [int(x) for x in ids if str(x).isdigit()],
            "expires_at": data.get("expires_at"),
            "status": str(data.get("status", "idle")),
            "generation_started_at": data.get("generation_started_at"),
        }
    except Exception as error:
        print(f"Could not read explanation state: {error}")
        return empty_state()


def save_state(
    chat_id: str | int | None,
    message_ids: list[int],
    expires_at: str | None,
    status: str = "idle",
    generation_started_at: str | None = None,
) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(
            {
                "chat_id": str(chat_id) if chat_id is not None else None,
                "message_ids": message_ids,
                "expires_at": expires_at,
                "status": status,
                "generation_started_at": generation_started_at,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def persist_state() -> None:
    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
        subprocess.run(
            ["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
            check=True,
        )
        subprocess.run(
            ["git", "add", str(STATE_FILE.relative_to(Path.cwd()))],
            check=True,
        )
        result = subprocess.run(
            ["git", "commit", "-m", "Update Telegram explanation state"],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            subprocess.run(["git", "push", "origin", "HEAD:main"], check=True)
            print("Explanation state persisted to main.")
        elif "nothing to commit" in (result.stdout + result.stderr).lower():
            print("Explanation state unchanged.")
        else:
            raise RuntimeError(result.stdout + result.stderr)
    except Exception as error:
        print(f"Could not persist explanation state: {error}")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def explanation_is_active(state: dict[str, Any]) -> bool:
    if state.get("status") == "generating":
        started = parse_iso(state.get("generation_started_at"))
        if started and datetime.now(timezone.utc) - started < timedelta(seconds=GENERATION_STALE_SECONDS):
            return True
        return False

    ids = state.get("message_ids", [])
    expires = parse_iso(state.get("expires_at"))
    return bool(ids and expires and datetime.now(timezone.utc) < expires)


def begin_generation(chat_id: str | int) -> bool:
    state = load_state()
    if explanation_is_active(state):
        return False

    started = datetime.now(timezone.utc)
    save_state(
        chat_id,
        [],
        None,
        status="generating",
        generation_started_at=started.isoformat(),
    )
    persist_state()
    return True


def finish_generation_with_error() -> None:
    save_state(None, [], None, status="idle", generation_started_at=None)
    persist_state()


def send_explanation(chat_id: str | int, text: str) -> list[int]:
    message_ids = send_to_chat(chat_id, text)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=EXPLANATION_TTL_SECONDS)
    save_state(
        chat_id,
        message_ids,
        expires_at.isoformat(),
        status="idle",
        generation_started_at=None,
    )
    persist_state()
    return message_ids


def explain_for_chat(chat_id: str | int) -> str:
    configured_chat = target_chat_id()
    if str(chat_id) != configured_chat:
        print(f"Ignoring explain request from unconfigured chat {chat_id}.")
        return "ignored"

    if not begin_generation(chat_id):
        return "active"

    try:
        calendar = fetch_calendar()
        events = [event for event in calendar if is_relevant(event)]
        if not events:
            send_explanation(
                chat_id,
                "M3 CAPITAL | WEEKLY MACRO NEWS\n\nNo Medium or High impact USD, EUR, or GBP events were found.",
            )
            return "sent"

        explanation = generate_explanation(events)
        message_ids = send_explanation(
            chat_id,
            "M3 CAPITAL | WEEKLY MACRO NEWS EXPLANATION\n\n" + explanation,
        )
        print(f"Sent temporary explanation messages: {message_ids}")
        return "sent"
    except Exception:
        finish_generation_with_error()
        raise


def handle_update(update: dict[str, Any]) -> None:
    callback = update.get("callback_query")
    if callback:
        if callback.get("data") != "explain_week":
            return
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            return

        callback_id = str(callback.get("id", ""))
        try:
            result = explain_for_chat(chat_id)
            if result == "active":
                answer_callback(callback_id, "An explanation is already available or being generated.")
            elif result == "ignored":
                answer_callback(callback_id, "This chat is not configured for the macro bot.")
            else:
                answer_callback(callback_id, "Explanation generated. It will auto-delete after 1 hour.")
        except Exception as error:
            print(f"Explain button failed: {error}")
            answer_callback(callback_id, "The explanation could not be generated right now. Please try again.")
            send_to_chat(
                chat_id,
                "M3 CAPITAL | WEEKLY MACRO NEWS\n\nThe detailed explanation could not be generated right now. Please try again.",
            )
        return

    message = update.get("message")
    if not message:
        return
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None or str(chat_id) != target_chat_id():
        return

    text = str(message.get("text", "")).strip()
    command = text.split()[0].split("@", 1)[0].lower() if text else ""
    if command != "/explain":
        return

    try:
        result = explain_for_chat(chat_id)
        if result == "active":
            print("/explain ignored because an explanation is already active or generating.")
        elif result == "sent":
            print("/explain completed successfully.")
    except Exception as error:
        print(f"/explain failed: {error}")
        send_to_chat(
            chat_id,
            "M3 CAPITAL | WEEKLY MACRO NEWS\n\nThe detailed explanation could not be generated right now. Please try again.",
        )


def get_updates(offset: int | None) -> list[dict[str, Any]]:
    payload: dict[str, Any] = {
        "timeout": LONG_POLL_TIMEOUT,
        "limit": 100,
        "allowed_updates": ["message", "callback_query"],
    }
    if offset is not None:
        payload["offset"] = offset
    result = telegram_call_local("getUpdates", payload)
    return result.get("result", [])


def main() -> int:
    try:
        telegram_call_local("deleteWebhook", {"drop_pending_updates": False})
        register_commands()

        deadline = time.time() + POLL_SECONDS
        offset: int | None = None

        print("Telegram explain listener started.")
        while time.time() < deadline:
            updates = get_updates(offset)
            if not updates:
                continue

            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = update_id + 1
                handle_update(update)

        print("Telegram explain listener finished its polling window.")
        return 0
    except Exception as error:
        print(f"ERROR: {error}")
        return 1
