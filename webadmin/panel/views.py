from __future__ import annotations

import datetime as dt
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from .forms import RoleBroadcastForm
from .models import LinkedUser, WebhookEvent

TIER_LABELS: List[Tuple[str, str]] = [
    ("1000", "Tier 1"),
    ("2000", "Tier 2"),
    ("3000", "Tier 3"),
]

STATUS_LABELS: Dict[str, Tuple[str, str]] = {
    "done": ("処理済み", "success"),
    "pending": ("処理待ち", "warning"),
    "failed": ("エラー", "danger"),
}


def _parse_iso_datetime(value: Any) -> Optional[dt.datetime]:
    if not value:
        return None
    if isinstance(value, dt.datetime):
        dt_value = value
    elif isinstance(value, dt.date):
        dt_value = dt.datetime.combine(value, dt.time.min)
    else:
        value_str = str(value).strip()
        if not value_str:
            return None
        if value_str.endswith("Z"):
            value_str = value_str[:-1] + "+00:00"
        dt_value = parse_datetime(value_str)
        if dt_value is None:
            try:
                dt_value = dt.datetime.fromisoformat(value_str)
            except ValueError:
                return None
    if timezone.is_naive(dt_value):
        dt_value = timezone.make_aware(dt_value, timezone.get_default_timezone())
    return dt_value


def _parse_iso_date(value: Any) -> Optional[dt.date]:
    if not value:
        return None
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.datetime):
        return value.date()
    value_str = str(value).strip()
    if not value_str:
        return None
    parsed = parse_date(value_str)
    if parsed:
        return parsed
    dt_value = parse_datetime(value_str)
    if dt_value:
        return dt_value.date()
    try:
        return dt.date.fromisoformat(value_str[:10])
    except ValueError:
        return None


def _first_day_next_month(d: dt.date) -> dt.date:
    if d.month == 12:
        return dt.date(d.year + 1, 1, 1)
    return dt.date(d.year, d.month + 1, 1)


def _to_local(dt_value: Optional[dt.datetime]) -> Optional[dt.datetime]:
    if dt_value is None:
        return None
    if timezone.is_naive(dt_value):
        dt_value = timezone.make_aware(dt_value, timezone.get_default_timezone())
    return timezone.localtime(dt_value)


def _build_dashboard_context() -> Dict[str, Any]:
    now = timezone.now()
    today = timezone.localdate()
    first_of_month = today.replace(day=1)

    try:
        linked_users = list(LinkedUser.objects.all())
    except Exception:
        linked_users = []

    user_stats: Dict[str, Any] = {
        "total": 0,
        "active": 0,
        "verified_this_month": 0,
        "stale_records": 0,
        "pending_relink": 0,
        "dm_failures": 0,
        "last_updated": None,
    }
    reminder_stats: Dict[str, Any] = {
        "reminders_sent_this_month": 0,
        "pending_over_7_days": 0,
    }
    tier_counter: Counter[str] = Counter()
    unresolved_samples: List[Dict[str, Any]] = []
    dm_failure_samples: List[Dict[str, Any]] = []
    latest_update: Optional[dt.datetime] = None

    for linked in linked_users:
        data = linked.data if isinstance(linked.data, dict) else {}
        if not isinstance(data, dict):
            data = {}

        user_stats["total"] += 1

        tier = str(data.get("tier") or "")
        if tier:
            tier_counter[tier] += 1
        if data.get("is_subscriber") or tier:
            user_stats["active"] += 1

        resolved = bool(data.get("resolved", True))
        if not resolved:
            user_stats["pending_relink"] += 1

        if data.get("dm_failed"):
            user_stats["dm_failures"] += 1

        last_verified_at = _parse_iso_date(data.get("last_verified_at"))
        if last_verified_at and last_verified_at >= first_of_month:
            user_stats["verified_this_month"] += 1
        else:
            user_stats["stale_records"] += 1

        last_notice_dt = _to_local(_parse_iso_datetime(data.get("last_notice_at")))
        if last_notice_dt and last_notice_dt.date() >= first_of_month:
            reminder_stats["reminders_sent_this_month"] += 1

        days_since_notice = None
        if last_notice_dt:
            days_since_notice = (today - last_notice_dt.date()).days
        if not resolved and days_since_notice is not None and days_since_notice >= 7:
            reminder_stats["pending_over_7_days"] += 1

        if not resolved:
            unresolved_samples.append(
                {
                    "discord_id": linked.discord_id,
                    "twitch_username": data.get("twitch_username") or "",
                    "last_notice_at": last_notice_dt,
                    "days_since_notice": days_since_notice,
                    "last_verified_at": last_verified_at,
                }
            )

        if data.get("dm_failed"):
            dm_failure_samples.append(
                {
                    "discord_id": linked.discord_id,
                    "twitch_username": data.get("twitch_username") or "",
                    "reason": data.get("dm_failed_reason") or "",
                    "last_notice_at": last_notice_dt,
                    "updated_at": _to_local(_parse_iso_datetime(linked.updated_at)),
                }
            )

        updated_at = _parse_iso_datetime(linked.updated_at)
        if updated_at and (latest_update is None or updated_at > latest_update):
            latest_update = updated_at

    user_stats["last_updated"] = _to_local(latest_update)
    if user_stats["total"]:
        user_stats["active_ratio"] = round(
            (user_stats["active"] / user_stats["total"]) * 100, 1
        )
    else:
        user_stats["active_ratio"] = 0

    tier_breakdown: List[Dict[str, Any]] = []
    counted = 0
    for code, label in TIER_LABELS:
        count = tier_counter.get(code, 0)
        counted += count
        tier_breakdown.append({"code": code, "label": label, "count": count})
    tier_breakdown.append(
        {
            "code": "none",
            "label": "未サブスク",
            "count": max(user_stats["total"] - counted, 0),
        }
    )

    try:
        events_queryset = list(WebhookEvent.objects.order_by("-received_at")[:200])
    except Exception:
        events_queryset = []

    event_stats: Dict[str, Any] = {
        "pending": 0,
        "failed": 0,
        "events_last_24h": 0,
        "events_last_7d": 0,
        "last_event_at": None,
    }
    try:
        event_stats["pending"] = WebhookEvent.objects.filter(status="pending").count()
    except Exception:
        event_stats["pending"] = 0
    try:
        event_stats["failed"] = WebhookEvent.objects.filter(status="failed").count()
    except Exception:
        event_stats["failed"] = 0

    day_cutoff = now - dt.timedelta(days=1)
    week_cutoff = now - dt.timedelta(days=7)

    recent_events: List[Dict[str, Any]] = []
    recent_failures: List[Dict[str, Any]] = []

    for event in events_queryset:
        received_dt = _parse_iso_datetime(event.received_at)
        local_received = _to_local(received_dt)
        if event_stats["last_event_at"] is None and local_received:
            event_stats["last_event_at"] = local_received

        if received_dt:
            if received_dt >= day_cutoff:
                event_stats["events_last_24h"] += 1
            if received_dt >= week_cutoff:
                event_stats["events_last_7d"] += 1

        status_key = str(event.status or "").lower()
        status_label: str
        status_level: str
        status_pair = STATUS_LABELS.get(status_key)
        if status_pair:
            status_label, status_level = status_pair
        else:
            status_label = str(event.status or "不明")
            status_level = "muted"

        if len(recent_events) < 12:
            recent_events.append(
                {
                    "delivery_id": event.delivery_id,
                    "event_type": event.event_type,
                    "source": event.source,
                    "status": status_label,
                    "status_level": status_level,
                    "twitch_user_id": event.twitch_user_id,
                    "received_at": local_received,
                    "retries": event.retries,
                    "error": event.error,
                }
            )

        if status_level == "danger" and len(recent_failures) < 3:
            recent_failures.append(
                {
                    "delivery_id": event.delivery_id,
                    "event_type": event.event_type,
                    "received_at": local_received,
                    "error": event.error,
                    "retries": event.retries,
                }
            )

    fallback_dt = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)

    unresolved_samples_sorted = sorted(
        unresolved_samples,
        key=lambda item: (
            item.get("last_notice_at") or fallback_dt,
            item.get("discord_id"),
        ),
    )[:5]

    dm_failure_samples_sorted = sorted(
        dm_failure_samples,
        key=lambda item: (
            item.get("updated_at") or fallback_dt,
            item.get("discord_id"),
        ),
        reverse=True,
    )[:5]

    return {
        "user_stats": user_stats,
        "tier_breakdown": tier_breakdown,
        "reminder_stats": reminder_stats,
        "unresolved_samples": unresolved_samples_sorted,
        "dm_failure_samples": dm_failure_samples_sorted,
        "event_stats": event_stats,
        "recent_events": recent_events,
        "recent_failures": recent_failures,
    }


def _build_self_service_entry(
    linked: LinkedUser, data: Dict[str, Any], *, today: dt.date
) -> Dict[str, Any]:
    tier = str(data.get("tier") or "")
    tier_label = dict(TIER_LABELS).get(tier, "未登録") if tier else "未登録"
    is_subscriber = bool(data.get("is_subscriber")) or bool(tier)

    last_verified_at = _parse_iso_date(data.get("last_verified_at"))
    linked_date = _parse_iso_date(data.get("linked_date"))
    subscribed_since = _parse_iso_date(data.get("subscribed_since"))
    basis = last_verified_at or linked_date
    next_due = _first_day_next_month(basis.replace(day=1)) if basis else None
    days_until_due = (next_due - today).days if next_due else None
    days_until_due_abs = abs(days_until_due) if days_until_due is not None else None

    last_notice_dt = _to_local(_parse_iso_datetime(data.get("last_notice_at")))
    first_notice_dt = _to_local(_parse_iso_datetime(data.get("first_notice_at")))
    days_since_notice = (
        (today - last_notice_dt.date()).days if last_notice_dt else None
    )

    dm_failed = bool(data.get("dm_failed"))
    resolved = bool(data.get("resolved", True))

    status_badges: List[Dict[str, str]] = []
    if not resolved:
        status_badges.append({"label": "要対応", "level": "warning"})
    if dm_failed:
        status_badges.append({"label": "DM未達", "level": "danger"})
    if not is_subscriber:
        status_badges.append({"label": "未サブスク", "level": "muted"})
    if next_due is not None and days_until_due is not None:
        if days_until_due < 0:
            status_badges.append({"label": "期限超過", "level": "danger"})
        elif days_until_due <= 5:
            status_badges.append({"label": "まもなく更新", "level": "warning"})
    if not status_badges:
        status_badges.append({"label": "良好", "level": "success"})

    status_notes: List[str] = []
    if not resolved:
        status_notes.append("サーバーで /link を実行すると解消されます。")
        if days_since_notice is not None and days_since_notice >= 7:
            status_notes.append("自動リマインドの再送が間もなく実行されます。")
    if dm_failed:
        reason = data.get("dm_failed_reason")
        if reason:
            status_notes.append(f"直近のDM送信が失敗しました: {reason}")
        else:
            status_notes.append("Discordのプライバシー設定でサーバーからのDMを許可してください。")
    if next_due is not None and days_until_due is not None:
        if days_until_due < 0:
            status_notes.append("今月分の再リンクが未確認です。/link を実行して更新してください。")
        elif days_until_due <= 5:
            status_notes.append("まもなく翌月の確認タイミングです。月初に /link を実行するとスムーズです。")
    if not is_subscriber:
        status_notes.append("現在Twitchサブスク登録が確認できません。登録状況をご確認ください。")

    deduped_notes = list(dict.fromkeys(status_notes))

    bits_score = data.get("bits_score")
    if isinstance(bits_score, int) and bits_score <= 0:
        bits_score = None

    return {
        "discord_id": linked.discord_id,
        "discord_profile_url": f"https://discord.com/users/{linked.discord_id}",
        "twitch_username": data.get("twitch_username") or "",
        "twitch_user_id": data.get("twitch_user_id") or "",
        "tier": tier,
        "tier_label": tier_label,
        "is_subscriber": is_subscriber,
        "streak_months": int(data.get("streak_months") or 0),
        "cumulative_months": int(data.get("cumulative_months") or 0),
        "bits_score": bits_score,
        "bits_rank": data.get("bits_rank"),
        "linked_date": linked_date,
        "subscribed_since": subscribed_since,
        "last_verified_at": last_verified_at,
        "first_notice_at": first_notice_dt,
        "last_notice_at": last_notice_dt,
        "days_since_notice": days_since_notice,
        "resolved": resolved,
        "dm_failed": dm_failed,
        "dm_failed_reason": data.get("dm_failed_reason"),
        "next_due_date": next_due,
        "days_until_due": days_until_due,
        "days_until_due_abs": days_until_due_abs,
        "status_badges": status_badges,
        "status_notes": deduped_notes,
        "updated_at": _to_local(_parse_iso_datetime(linked.updated_at)),
    }


def _collect_self_service_entries(
    twitch_profile: Dict[str, Any]
) -> List[Dict[str, Any]]:
    today = timezone.localdate()
    twitch_id = str(twitch_profile.get("id") or "").strip()
    twitch_login = str(twitch_profile.get("login") or "").lower().strip()

    try:
        linked_users = list(LinkedUser.objects.all())
    except Exception:
        linked_users = []

    entries: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for linked in linked_users:
        data = linked.data if isinstance(linked.data, dict) else {}
        if not isinstance(data, dict):
            data = {}
        user_id = str(data.get("twitch_user_id") or "").strip()
        login = str(data.get("twitch_username") or "").lower().strip()

        matched = False
        if twitch_id and user_id and twitch_id == user_id:
            matched = True
        elif twitch_login and login and twitch_login == login:
            matched = True
        if not matched:
            continue

        entry = _build_self_service_entry(linked, data, today=today)
        if entry["discord_id"] in seen:
            continue
        seen.add(entry["discord_id"])
        entries.append(entry)

    entries.sort(key=lambda item: item["discord_id"])
    return entries


def index(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated and not request.user.is_staff:
        return redirect("self_service")

    dashboard = None
    if request.user.is_authenticated and request.user.is_staff:
        dashboard = _build_dashboard_context()

    return render(request, "panel/index.html", {"dashboard": dashboard})


@login_required
def self_service(request: HttpRequest) -> HttpResponse:
    twitch_account = None
    try:
        twitch_account = SocialAccount.objects.filter(
            user=request.user, provider="twitch"
        ).first()
    except Exception:
        twitch_account = None

    profile: Dict[str, Any] = {
        "display_name": "",
        "login": "",
        "id": "",
        "profile_image_url": "",
    }
    if twitch_account:
        extra = twitch_account.extra_data or {}
        profile = {
            "display_name": extra.get("display_name")
            or extra.get("preferred_username")
            or request.user.username,
            "login": extra.get("login") or extra.get("preferred_username") or "",
            "id": str(extra.get("id") or extra.get("sub") or ""),
            "profile_image_url": extra.get("profile_image_url") or "",
        }

    entries = _collect_self_service_entries(profile)

    return render(
        request,
        "panel/status.html",
        {
            "twitch_profile": profile,
            "linked_entries": entries,
        },
    )


@login_required
def broadcast(request: HttpRequest) -> HttpResponse:
    if not request.user.is_staff:
        return HttpResponseForbidden("このページへアクセスする権限がありません。")

    if request.method == "POST":
        form = RoleBroadcastForm(request.POST, request.FILES)
        refresh_requested = bool(request.POST.get("refresh"))
        if not refresh_requested and form.is_valid():
            role_ids = [int(r) for r in form.cleaned_data["role_ids"]]
            guild_id_value = form.cleaned_data.get("guild_id")
            guild_id = int(guild_id_value) if guild_id_value else None
            message = form.cleaned_data["message"] or ""
            file_url = None
            file_path = None

            f = form.cleaned_data.get("attachment")
            if f:
                from django.core.files.base import ContentFile
                from django.core.files.storage import default_storage

                rel_path = default_storage.save(
                    f"uploads/{f.name}", ContentFile(f.read())
                )
                url_path = str(rel_path).replace("\\", "/").lstrip("/")
                file_url = request.build_absolute_uri(settings.MEDIA_URL + url_path)
                file_path = str((Path(settings.MEDIA_ROOT) / rel_path).resolve())

            headers = (
                {"Authorization": f"Bearer {settings.ADMIN_API_TOKEN}"}
                if settings.ADMIN_API_TOKEN
                else {}
            )
            role_labels = {
                str(value): label for value, label in form.fields["role_ids"].choices
            }
            success_roles: List[str] = []
            failed_roles: List[Tuple[str, str]] = []

            for rid in role_ids:
                payload: Dict[str, Any] = {"role_id": rid, "message": message}
                if guild_id:
                    payload["guild_id"] = guild_id
                if file_url:
                    payload["file_url"] = file_url
                if file_path:
                    payload["file_path"] = file_path
                try:
                    resp = requests.post(
                        f"{settings.BOT_ADMIN_API_BASE}/send_role_dm",
                        json=payload,
                        headers=headers,
                        timeout=10,
                    )
                except Exception as exc:
                    failed_roles.append(
                        (role_labels.get(str(rid), str(rid)), str(exc))
                    )
                    continue

                if resp.status_code == 200:
                    success_roles.append(role_labels.get(str(rid), str(rid)))
                else:
                    reason = f"{resp.status_code} {resp.text}".strip()
                    failed_roles.append((role_labels.get(str(rid), str(rid)), reason))

            if success_roles:
                if len(success_roles) == 1:
                    messages.success(
                        request, f"「{success_roles[0]}」への送信をキューに投入しました。"
                    )
                else:
                    joined = "、".join(success_roles)
                    messages.success(
                        request,
                        f"{len(success_roles)}件のロール（{joined}）への送信をキューに投入しました。",
                    )
            for label, reason in failed_roles:
                messages.error(
                    request, f"ロール「{label}」への送信に失敗しました: {reason}"
                )

            if not failed_roles:
                return redirect("broadcast")
    else:
        form = RoleBroadcastForm()

    twitch_account = None
    try:
        twitch_account = SocialAccount.objects.filter(
            user=request.user, provider="twitch"
        ).first()
    except Exception:
        twitch_account = None

    return render(
        request,
        "panel/broadcast.html",
        {"form": form, "twitch_account": twitch_account},
    )
