import datetime as dt
from typing import Any, Dict, List, Optional

from .save_and_load import (
    load_users,
    get_linked_user,
    patch_linked_user,
    record_cheer_event,
)

JST = dt.timezone(dt.timedelta(hours=9))


def _parse_iso_datetime(value: Any) -> Optional[dt.datetime]:
    """Best-effort ISO8601 parser that returns an aware datetime in JST."""
    if not value:
        return None
    if isinstance(value, dt.datetime):
        dt_value = value
    elif isinstance(value, dt.date):
        dt_value = dt.datetime.combine(value, dt.time.min)
    else:
        text = str(value).strip()
        if not text:
            return None
        text = text.replace("Z", "+00:00")
        try:
            dt_value = dt.datetime.fromisoformat(text)
        except ValueError:
            if "." in text:
                base, frac = text.split(".", 1)
                tz_part = ""
                digits = frac
                for sep in ("+", "-"):
                    if sep in frac:
                        digits, tz_part = frac.split(sep, 1)
                        tz_part = sep + tz_part
                        break
                digits = "".join(ch for ch in digits if ch.isdigit())[:6]
                text = f"{base}.{digits}{tz_part}"
                try:
                    dt_value = dt.datetime.fromisoformat(text)
                except ValueError:
                    return None
            else:
                return None
    if dt_value.tzinfo is None:
        dt_value = dt_value.replace(tzinfo=dt.timezone.utc)
    return dt_value.astimezone(JST)


def _resolve_event_datetime(event: Dict[str, Any], header_ts: str | None) -> dt.datetime:
    """Choose the most relevant timestamp from payload/header, fallback to now."""
    for candidate in (
        event.get("event_timestamp"),
        event.get("created_at"),
        event.get("updated_at"),
        event.get("timestamp"),
        header_ts,
    ):
        resolved = _parse_iso_datetime(candidate)
        if resolved is not None:
            return resolved
    return dt.datetime.now(JST)


def _first_day_next_month(d: dt.date) -> dt.date:
    month = d.month + 1
    year = d.year
    if month > 12:
        month = 1
        year += 1
    return dt.date(year, month, 1)


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

    event_dt = _resolve_event_datetime(event, twitch_msg_ts)
    event_date = event_dt.date()
    event_iso = event_dt.isoformat()

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
            or event_iso
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

    due_next_month = _first_day_next_month(event_date).isoformat()

    matched = 0
    for did in dids:
        updates: Dict[str, Any] = {
            "last_eventsub_type": sub_type,
            "last_eventsub_at": event_iso,
        }

        if sub_type == "channel.subscribe":
            updates["is_subscriber"] = True
            if event.get("tier"):
                updates["tier"] = event.get("tier")
            try:
                current = get_linked_user(did)
            except Exception:
                current = {}
            if not (current or {}).get("subscribed_since"):
                updates["subscribed_since"] = event_date.isoformat()
            updates["last_verified_at"] = event_date.isoformat()
            updates["next_reverify_due_at"] = due_next_month
            updates["resolved"] = True
            updates["roles_revoked"] = False
            updates["roles_revoked_at"] = None

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
            updates["last_verified_at"] = event_date.isoformat()
            updates["next_reverify_due_at"] = due_next_month
            updates["resolved"] = True
            updates["roles_revoked"] = False
            updates["roles_revoked_at"] = None

        elif sub_type == "channel.subscription.end":
            updates["is_subscriber"] = False
            updates["last_verified_at"] = event_date.isoformat()
            updates["next_reverify_due_at"] = event_date.isoformat()
            updates["resolved"] = True
            updates["roles_revoked"] = True
            updates["roles_revoked_at"] = event_iso

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
                elif event_iso:
                    updates["last_cheer_at"] = event_iso
                msg = event.get("message")
                if msg:
                    updates["last_cheer_message"] = str(msg)
            updates.setdefault("last_verified_at", event_date.isoformat())
            updates.setdefault("next_reverify_due_at", due_next_month)

        if updates:
            patch_linked_user(did, updates)
            matched += 1

    return matched
