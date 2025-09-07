import datetime as dt
from typing import Any, Dict, List

from .save_and_load import (
    load_users,
    get_linked_user,
    patch_linked_user,
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

        if updates:
            patch_linked_user(did, updates)
            matched += 1

    return matched

