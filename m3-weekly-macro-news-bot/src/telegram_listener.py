from __future__ import annotations

import os
import time
from typing import Any

import requests

from main import fetch_calendar, generate_explanation, is_relevant, send_to_chat

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
POLL_SECONDS = 240
LONG_POLL_TIMEOUT = 25


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


def telegram_call(method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
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
    telegram_call(
        "setMyCommands",
        {
            "commands": [
                {"command": "explain", "description": "Explain this week's macro news"}
            ]
        },
    )


def answer_callback(callback_id: str) -> None:
    telegram_call(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id,
            "text": "Generating the detailed macro explanation...",
            "show_alert": False,
        },
    )


def explain_for_chat(chat_id: str | int) -> None:
    configured_chat = target_chat_id()
    if str(chat_id) != configured_chat:
        print(f"Ignoring explain request from unconfigured chat {chat_id}.")
        return

    calendar = fetch_calendar()
    events = [event for event in calendar if is_relevant(event)]
    if not events:
        send_to_chat(
            chat_id,
            "M3 CAPITAL | WEEKLY MACRO NEWS\n\nNo Medium or High impact USD, EUR, or GBP events were found.",
        )
        return

    explanation = generate_explanation(events)
    send_to_chat(
        chat_id,
        "M3 CAPITAL | WEEKLY MACRO NEWS EXPLANATION\n\n" + explanation,
    )


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
    result = telegram_call("getUpdates", payload)
    return result.get("result", [])


def main() -> int:
    try:
        # Ensure long polling can be used if a webhook was previously configured.
        telegram_call("deleteWebhook", {"drop_pending_updates": False})
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
