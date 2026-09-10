from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import requests

from main import fetch_calendar, generate_explanation, is_relevant, send_to_chat, telegram_call

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
POLL_SECONDS = 240
LONG_POLL_TIMEOUT = 25
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


def answer_callback(callback_id: str) -> None:
    telegram_call_local(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id,
            "text": "Generating the detailed macro explanation...",
            "show_alert": False,
        },
    )


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"chat_id": None, "message_ids": []}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"chat_id": None, "message_ids": []}
        ids = data.get("message_ids", [])
        if not isinstance(ids, list):
            ids = []
        return {"chat_id": data.get("chat_id"), "message_ids": [int(x) for x in ids if str(x).isdigit()]}
    except Exception as error:
        print(f"Could not read explanation state: {error}")
        return {"chat_id": None, "message_ids": []}


def save_state(chat_id: str | int, message_ids: list[int]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps({"chat_id": str(chat_id), "message_ids": message_ids}, indent=2) + "\n",
        encoding="utf-8",
    )


def persist_state() -> None:
    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
        subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
        subprocess.run(["git", "add", str(STATE_FILE.relative_to(Path.cwd()))], check=True)
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


def delete_previous_explanation() -> None:
    state = load_state()
    previous_ids = state.get("message_ids", [])
    previous_chat = state.get("chat_id")
    if not previous_ids or not previous_chat:
        return

    print(f"Deleting previous explanation messages: {previous_ids}")
    try:
        telegram_call(
            "deleteMessages",
            {
                "chat_id": previous_chat,
                "message_ids": previous_ids,
            },
        )
    except Exception as error:
        print(f"Batch delete failed, trying individual deletes: {error}")
        for message_id in previous_ids:
            try:
                telegram_call_local(
                    "deleteMessage",
                    {"chat_id": previous_chat, "message_id": message_id},
                )
            except Exception as individual_error:
                print(f"Could not delete message {message_id}: {individual_error}")


def send_explanation(chat_id: str | int, text: str) -> list[int]:
    message_ids = send_to_chat(chat_id, text)
    save_state(chat_id, message_ids)
    return message_ids


def explain_for_chat(chat_id: str | int) -> None:
    configured_chat = target_chat_id()
    if str(chat_id) != configured_chat:
        print(f"Ignoring explain request from unconfigured chat {chat_id}.")
        return

    calendar = fetch_calendar()
    events = [event for event in calendar if is_relevant(event)]
    if not events:
        delete_previous_explanation()
        message_ids = send_explanation(
            chat_id,
            "M3 CAPITAL | WEEKLY MACRO NEWS\n\nNo Medium or High impact USD, EUR, or GBP events were found.",
        )
        persist_state()
        print(f"Sent no-events response: {message_ids}")
        return

    explanation = generate_explanation(events)
    delete_previous_explanation()
    message_ids = send_explanation(
        chat_id,
        "M3 CAPITAL | WEEKLY MACRO NEWS EXPLANATION\n\n" + explanation,
    )
    persist_state()
    print(f"Sent new explanation messages: {message_ids}")


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
        answer_callback(str(callback.get("id", "")))
        try:
            explain_for_chat(chat_id)
        except Exception as error:
            print(f"Explain button failed: {error}")
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
        send_to_chat(
            chat_id,
            "M3 CAPITAL | WEEKLY MACRO NEWS\n\nGenerating the detailed macro explanation...",
        )
        explain_for_chat(chat_id)
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


if __name__ == "__main__":
    raise SystemExit(main())
