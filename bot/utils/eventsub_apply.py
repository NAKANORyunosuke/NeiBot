import datetime as dt
from typing import Any, Dict, List

from .save_and_load import (
    load_users,
    get_linked_user,
    patch_linked_user,
    record_cheer_event,
)

JST = dt.timezone(dt.timedelta(hours=9))


def _find_discord_ids_by_twitch_id(
    users: Dict[str, Any], twitch_user_id: str
) -> List[str]:
    dids: List[str] = []
    for did, info in users.items():
        if isinstance(info, dict) and str(info.get("twitch_user_id")) == str(
            twitch_user_id
        ):
            dids.append(str(did))
    return dids


def apply_event_to_linked_users(
    sub_type: str | None, event: Dict[str, Any], twitch_msg_ts: str | None
) -> int:
    """Apply a Twitch EventSub notification to linked_users.

    Returns the number of matched Discord IDs updated.
    """
    if not sub_type:
        return 0

    users = load_users()

    t_user_id = (
        event.get("user_id")
        or event.get("user")
        or event.get("user_login")
        or event.get("broadcaster_user_id")
    )

    cheer_bits: int | None = None
    cheer_timestamp: str | None = None
    is_cheer = sub_type == "channel.cheer"
    if is_cheer:
        bits_val = event.get("bits")
        try:
            cheer_bits = int(bits_val)
        except (TypeError, ValueError):
            cheer_bits = None
        cheer_timestamp = (
            event.get("event_timestamp")
            or event.get("created_at")
            or twitch_msg_ts
            or dt.datetime.now(dt.timezone.utc).isoformat()
        )
        try:
            record_cheer_event(
                twitch_user_id=str(t_user_id) if t_user_id else None,
                bits=cheer_bits or 0,
                is_anonymous=bool(event.get("is_anonymous")),
                message=event.get("message"),
                payload=event,
                cheer_at=cheer_timestamp,
            )
        except Exception:
            pass

    if not t_user_id:
        return 0

    dids = _find_discord_ids_by_twitch_id(users, str(t_user_id))
    if not dids:
        return 0

    now = dt.datetime.now(JST).date()

    matched = 0
    for did in dids:
        updates: Dict[str, Any] = {}
        if sub_type == "channel.subscribe":
            updates["is_subscriber"] = True
            if event.get("tier"):
                updates["tier"] = event.get("tier")
            try:
                current = get_linked_user(did)
            except Exception:
                current = {}
            if not (current or {}).get("subscribed_since"):
                ts = (twitch_msg_ts or "")[:10] if twitch_msg_ts else now.isoformat()
                updates["subscribed_since"] = ts
            updates["last_verified_at"] = now

        elif sub_type == "channel.subscription.message":
            cum = event.get("cumulative_months")
            if isinstance(cum, int) and cum >= 0:
                updates["cumulative_months"] = cum
            streak_val = event.get("streak_months")
            if isinstance(streak_val, dict):
                sm = streak_val.get("months")
                if isinstance(sm, int) and sm >= 0:
                    updates["streak_months"] = sm
            elif isinstance(streak_val, int) and streak_val >= 0:
                updates["streak_months"] = streak_val
            if event.get("tier"):
                updates["tier"] = event.get("tier")
            updates["is_subscriber"] = True
            updates["last_verified_at"] = now

        elif sub_type == "channel.subscription.end":
            updates["is_subscriber"] = False
            updates["last_verified_at"] = now

        elif is_cheer:
            if cheer_bits and cheer_bits > 0 and not bool(event.get("is_anonymous")):
                try:
                    current = get_linked_user(did)
                except Exception:
                    current = {}
                total_prev = (
                    current.get("total_cheer_bits") if isinstance(current, dict) else 0
                )
                try:
                    total_prev = int(total_prev or 0)
                except (TypeError, ValueError):
                    total_prev = 0
                updates["total_cheer_bits"] = total_prev + cheer_bits
                updates["last_cheer_bits"] = cheer_bits
                if cheer_timestamp:
                    updates["last_cheer_at"] = cheer_timestamp
                msg = event.get("message")
                if msg:
                    updates["last_cheer_message"] = str(msg)
            # 匿名cheerの場合でも最終確認日時は更新しておく
            updates.setdefault("last_verified_at", now)

        if updates:
            patch_linked_user(did, updates)
            matched += 1

    return matched
