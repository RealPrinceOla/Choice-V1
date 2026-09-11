from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from main import fetch_calendar, generate_explanation, is_relevant, parse_date, send_to_chat, telegram_call, LAGOS

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
POLL_SECONDS = 240
LONG_POLL_TIMEOUT = 25
EXPLANATION_TTL_SECONDS = 3600
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
        {"commands": [{"command": "explain", "description": "Explain today's and upcoming macro news"}]},
    )


def answer_callback(callback_id: str, text: str = "Generating the detailed macro explanation...") -> None:
    telegram_call_local(
        "answerCallbackQuery",
        {"callback_query_id": callback_id, "text": text, "show_alert": False},
    )


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"chat_id": None, "message_ids": [], "expires_at": None, "status": "idle"}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"chat_id": None, "message_ids": [], "expires_at": None, "status": "idle"}
        ids = data.get("message_ids", [])
        if not isinstance(ids, list):
            ids = []
        return {
            "chat_id": data.get("chat_id"),
            "message_ids": [int(x) for x in ids if str(x).isdigit()],
            "expires_at": data.get("expires_at"),
            "status": str(data.get("status", "idle")),
        }
    except Exception as error:
        print(f"Could not read explanation state: {error}")
        return {"chat_id": None, "message_ids": [], "expires_at": None, "status": "idle"}


def save_state(chat_id: str | int, message_ids: list[int], expires_at: str | None, status: str = "active") -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps({"chat_id": str(chat_id), "message_ids": message_ids, "expires_at": expires_at, "status": status}, indent=2) + "\n",
        encoding="utf-8",
    )


def delete_message_ids(chat_id: str | int, message_ids: list[int]) -> None:
    if not message_ids:
        return
    try:
        telegram_call_local("deleteMessages", {"chat_id": chat_id, "message_ids": message_ids})
        return
    except Exception as error:
        print(f"Batch delete failed, trying individual deletes: {error}")
    for message_id in message_ids:
        try:
            telegram_call_local("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
        except Exception as individual_error:
            print(f"Could not delete message {message_id}: {individual_error}")


def clear_state() -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps({"chat_id": None, "message_ids": [], "expires_at": None, "status": "idle"}, indent=2) + "\n",
        encoding="utf-8",
    )


def state_is_active(state: dict[str, Any]) -> bool:
    if state.get("status") != "active" or not state.get("message_ids"):
        return False
    expires_at = state.get("expires_at")
    if not expires_at:
        return False
    try:
        return datetime.fromisoformat(expires_at).timestamp() > time.time()
    except Exception:
        return False


def persist_state_to_git() -> None:
    import subprocess

    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
        subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
        subprocess.run(["git", "add", str(STATE_FILE.relative_to(Path.cwd()))], check=True)
        result = subprocess.run(["git", "commit", "-m", "Update Telegram explanation state"], text=True, capture_output=True, check=False)
        if result.returncode == 0:
            subprocess.run(["git", "push", "origin", "HEAD:main"], check=True)
            print("Explanation state persisted to main.")
        elif "nothing to commit" in (result.stdout + result.stderr).lower():
            print("Explanation state unchanged.")
        else:
            print(f"Could not commit explanation state: {result.stdout}{result.stderr}")
    except Exception as error:
        print(f"Could not persist explanation state: {error}")


def cleanup_expired_explanation() -> bool:
    state = load_state()
    if state.get("status") != "active" or not state.get("message_ids"):
        return False

    expires_at = state.get("expires_at")
    if not expires_at:
        return False

    try:
        expired = datetime.fromisoformat(expires_at).timestamp() <= time.time()
    except Exception:
        print(f"Invalid explanation expiry timestamp: {expires_at}")
        return False

    if not expired:
        return False

    chat_id = state.get("chat_id")
    message_ids = state.get("message_ids", [])
    if not chat_id or str(chat_id) != target_chat_id():
        print("Expired explanation state does not match configured chat. Refusing cleanup.")
        return False

    print(f"Explanation expired at {expires_at}. Deleting message IDs: {message_ids}")
    delete_message_ids(chat_id, message_ids)
    clear_state()
    persist_state_to_git()
    print("Expired explanation cleaned up.")
    return True


def filter_current_and_future(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = datetime.now(LAGOS)
    return [
        event for event in events
        if parse_date(event["date"]).astimezone(LAGOS) >= now
    ]


def explain_for_chat(chat_id: str | int) -> None:
    if str(chat_id) != target_chat_id():
        print(f"Ignoring explain request from unconfigured chat {chat_id}.")
        return

    cleanup_expired_explanation()
    state = load_state()
    if state_is_active(state):
        send_to_chat(chat_id, "M3 CAPITAL | WEEKLY MACRO NEWS\n\nAn explanation is already available. It will automatically disappear after one hour.")
        return

    calendar = fetch_calendar()
    events = filter_current_and_future([event for event in calendar if is_relevant(event)])
    if not events:
        message_ids = send_to_chat(chat_id, "M3 CAPITAL | WEEKLY MACRO NEWS\n\nThere are no remaining Medium or High impact USD, EUR, or GBP events for today or the rest of the week.")
    else:
        print(f"Generating explanation for {len(events)} current/upcoming events.")
        explanation = generate_explanation(events)
        message_ids = send_to_chat(chat_id, "M3 CAPITAL | WEEKLY MACRO NEWS EXPLANATION\n\n" + explanation)

    expires_at = (datetime.now().astimezone() + timedelta(seconds=EXPLANATION_TTL_SECONDS)).isoformat()
    save_state(chat_id, message_ids, expires_at, "active")
    persist_state_to_git()
    print(f"Sent explanation messages: {message_ids}; expires at {expires_at}")


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
        state = load_state()
        if state_is_active(state):
            answer_callback(str(callback.get("id", "")), "An explanation is already active.")
        else:
            answer_callback(str(callback.get("id", "")))
        try:
            explain_for_chat(chat_id)
        except Exception as error:
            print(f"Explain button failed: {error}")
            send_to_chat(chat_id, "M3 CAPITAL | WEEKLY MACRO NEWS\n\nThe detailed explanation could not be generated right now. Please try again.")
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
        explain_for_chat(chat_id)
    except Exception as error:
        print(f"/explain failed: {error}")
        send_to_chat(chat_id, "M3 CAPITAL | WEEKLY MACRO NEWS\n\nThe detailed explanation could not be generated right now. Please try again.")


def get_updates(offset: int | None) -> list[dict[str, Any]]:
    payload: dict[str, Any] = {"timeout": LONG_POLL_TIMEOUT, "limit": 100, "allowed_updates": ["message", "callback_query"]}
    if offset is not None:
        payload["offset"] = offset
    return telegram_call_local("getUpdates", payload).get("result", [])


def main() -> int:
    try:
        register_commands()
        telegram_call_local("deleteWebhook", {"drop_pending_updates": False})
        cleanup_expired_explanation()
        deadline = time.time() + POLL_SECONDS
        offset: int | None = None
        print("Telegram explain listener started.", flush=True)
        while time.time() < deadline:
            cleanup_expired_explanation()
            updates = get_updates(offset)
            if not updates:
                continue
            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = update_id + 1
                handle_update(update)
        print("Telegram explain listener finished its polling window.", flush=True)
        return 0
    except Exception as error:
        print(f"ERROR: {error}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
