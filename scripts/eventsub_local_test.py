#!/usr/bin/env python
"""
Local EventSub E2E tester (no Discord required)

Usage examples:
  # assuming venv/token.json is configured
  python scripts/eventsub_local_test.py --discord-id 999999999999999999 --twitch-user-id 111111111

  # start uvicorn automatically (127.0.0.1:8000)
  python scripts/eventsub_local_test.py --start-server
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
import uuid
import hmac
import hashlib
from typing import Tuple

import requests


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sign(secret: str, msg_id: str, msg_ts: str, body_bytes: bytes) -> str:
    mac = hmac.new(secret.encode("utf-8"), (msg_id + msg_ts).encode("utf-8") + body_bytes, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def _post_verification(base_url: str, challenge: str = "hello") -> Tuple[int, str]:
    body = {"challenge": challenge}
    b = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {
        "Twitch-Eventsub-Message-Id": str(uuid.uuid4()),
        "Twitch-Eventsub-Message-Timestamp": _now_iso(),
        "Twitch-Eventsub-Message-Type": "webhook_callback_verification",
        "Content-Type": "application/json",
    }
    r = requests.post(f"{base_url}/twitch_eventsub", data=b, headers=headers, timeout=10)
    return r.status_code, r.text


def _post_event(base_url: str, secret: str, sub_type: str, event: dict) -> Tuple[int, str | dict]:
    body = {
        "subscription": {"type": sub_type, "version": "1"},
        "event": event,
    }
    b = json.dumps(body, ensure_ascii=False).encode("utf-8")
    msg_id = str(uuid.uuid4())
    msg_ts = _now_iso()
    headers = {
        "Twitch-Eventsub-Message-Id": msg_id,
        "Twitch-Eventsub-Message-Timestamp": msg_ts,
        "Twitch-Eventsub-Message-Type": "notification",
        "Twitch-Eventsub-Message-Signature": _sign(secret, msg_id, msg_ts, b),
        "Content-Type": "application/json",
    }
    r = requests.post(f"{base_url}/twitch_eventsub", data=b, headers=headers, timeout=10)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text


def _start_uvicorn_in_thread(host: str, port: int) -> None:
    os.environ.setdefault("DEBUG", "1")
    from bot.bot_client import app  # import here to avoid side effects in global scope
    import uvicorn

    def _run():
        uvicorn.run(app, host=host, port=port, log_level="info")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    time.sleep(1.0)


def main() -> None:
    p = argparse.ArgumentParser(description="Local EventSub tester for NeiBot")
    p.add_argument("--base-url", default="http://127.0.0.1:8000", help="FastAPI base URL")
    p.add_argument("--discord-id", default="999999999999999999", help="Test Discord ID to link")
    p.add_argument("--twitch-user-id", default="111111111", help="Test Twitch user_id")
    p.add_argument("--start-server", action="store_true", help="Start uvicorn for bot.bot_client:app")
    args = p.parse_args()

    if args.start_server:
        _start_uvicorn_in_thread("127.0.0.1", 8000)

    # secret
    from bot.utils.save_and_load import get_eventsub_config, patch_linked_user, get_linked_user

    cb, secret = get_eventsub_config()
    print(f"Callback={cb}")
    print(f"Secret  ={'*' * len(secret) if secret else '(missing)'}")

    # create link mapping
    patch_linked_user(str(args.discord_id), {
        "twitch_user_id": str(args.twitch_user_id),
        "twitch_username": "test_user",
        "is_subscriber": False,
        "tier": None,
    })
    print("Before:", get_linked_user(str(args.discord_id)))

    # 1) verification
    s, body = _post_verification(args.base_url, "challenge-ok")
    print("verify:", s, body)

    # 2) subscribe
    s, body = _post_event(args.base_url, secret, "channel.subscribe", {
        "user_id": str(args.twitch_user_id),
        "tier": "1000",
    })
    print("subscribe:", s, body)
    print("After subscribe:", get_linked_user(str(args.discord_id)))

    # 3) resub message
    s, body = _post_event(args.base_url, secret, "channel.subscription.message", {
        "user_id": str(args.twitch_user_id),
        "tier": "2000",
        "cumulative_months": 7,
        "streak_months": {"months": 4},
    })
    print("message:", s, body)
    print("After message:", get_linked_user(str(args.discord_id)))

    # 4) end
    s, body = _post_event(args.base_url, secret, "channel.subscription.end", {
        "user_id": str(args.twitch_user_id),
    })
    print("end:", s, body)
    print("After end:", get_linked_user(str(args.discord_id)))


if __name__ == "__main__":
    main()

