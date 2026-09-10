from __future__ import annotations

from datetime import datetime, timezone
import sys

from telegram_listener import clear_state, delete_message_ids, load_state, persist_state_to_git, target_chat_id


def cleanup() -> int:
    state = load_state()
    message_ids = state.get("message_ids", [])
    chat_id = state.get("chat_id")
    expires_at = state.get("expires_at")

    if state.get("status") != "active" or not message_ids or not chat_id:
        print("No active explanation messages are currently tracked.")
        return 0

    if str(chat_id) != target_chat_id():
        print(f"Tracked chat {chat_id} does not match configured chat. Refusing cleanup.")
        return 1

    if not expires_at:
        print("Active explanation has no expiry timestamp. Refusing automatic deletion.")
        return 1

    try:
        expiry = datetime.fromisoformat(expires_at).astimezone(timezone.utc)
    except ValueError:
        print(f"Invalid expiry timestamp: {expires_at}. Refusing automatic deletion.")
        return 1

    now = datetime.now(timezone.utc)
    if now < expiry:
        print(f"Explanation is still active until {expiry.isoformat()}.")
        return 0

    print(f"Explanation expired at {expiry.isoformat()}. Deleting message IDs: {message_ids}")
    delete_message_ids(chat_id, message_ids)
    clear_state()
    persist_state_to_git()
    print("Temporary explanation cleanup completed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(cleanup())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise
