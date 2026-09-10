from __future__ import annotations

import sys
from datetime import datetime, timezone

from telegram_listener import (
    load_state,
    parse_iso,
    persist_state,
    save_state,
    target_chat_id,
    telegram_call_local,
)


def cleanup() -> int:
    state = load_state()
    message_ids = state.get("message_ids", [])
    chat_id = state.get("chat_id")
    expires_at = parse_iso(state.get("expires_at"))

    if not message_ids or not chat_id:
        print("No temporary explanation messages are currently tracked.")
        return 0

    if expires_at is not None and datetime.now(timezone.utc) < expires_at:
        print(f"Explanation is still active until {expires_at.isoformat()}.")
        return 0

    if expires_at is None:
        print("Tracked explanation has no expiry timestamp. Treating it as legacy state and cleaning it up.")

    if str(chat_id) != target_chat_id():
        print(f"Tracked chat {chat_id} does not match configured chat. Refusing cleanup.")
        return 1

    print(f"Deleting expired explanation messages: {message_ids}")
    try:
        telegram_call_local(
            "deleteMessages",
            {
                "chat_id": chat_id,
                "message_ids": message_ids,
            },
        )
    except Exception as error:
        print(f"Batch delete failed, trying individual deletes: {error}")
        for message_id in message_ids:
            try:
                telegram_call_local(
                    "deleteMessage",
                    {"chat_id": chat_id, "message_id": message_id},
                )
            except Exception as individual_error:
                print(f"Could not delete message {message_id}: {individual_error}")

    save_state(None, [], None, status="idle", generation_started_at=None)
    persist_state()
    print("Temporary explanation cleanup completed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(cleanup())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise
