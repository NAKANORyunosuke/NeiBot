import asyncio
import json
import threading
from typing import Coroutine, Any
import zoneinfo
import datetime as dt
import copy
import discord
from discord.ext import commands
from fastapi import FastAPI, Request, Header
from fastapi.responses import PlainTextResponse, JSONResponse
import uvicorn
import httpx
import os
import re
import sys
import subprocess
import atexit
from bot.common import debug_print
from bot.utils.streak import reconcile_and_save_link
from bot.utils.save_and_load import (
    get_broadcast_id,
    get_twitch_keys,
    get_guild_id,
    get_admin_api_token,
    save_all_guild_members,
    load_role_ids,
    save_role_ids,
    save_channel_ids,
    load_channel_ids,
    load_subscription_categories,
    save_subscription_categories,
    load_subscription_config,
    save_subscription_config,
)
from bot.utils.twitch import (
    get_user_info_and_subscription,
    register_eventsub_subscriptions,
    list_eventsub_subscriptions,
    delete_eventsub_subscription,
    create_eventsub_subscription,
)
from bot.utils.save_and_load import (
    load_users,
    get_eventsub_config,
    inbox_enqueue_event,
    inbox_mark_processed,
)
from bot.utils.eventsub_apply import apply_event_to_linked_users
import hmac
import hashlib
import io

# ==================== パス設定（絶対パス） ====================

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "./"))
TOKEN_PATH = os.path.join(PROJECT_ROOT, "venv", "token.json")

DEFAULT_SUBSCRIPTION_CONFIG: dict[str, Any] = {
    "tiers": [
        {
            "key": "tier1",
            "role_name": "Subscription Tier1",
            "category_name": "サブスクTier 1",
            "channel_name": "tier-1",
            "view_roles": ["tier1"],
        },
        {
            "key": "tier2",
            "role_name": "Subscription Tier2",
            "category_name": "サブスクTier 2",
            "channel_name": "tier-2",
            "view_roles": ["tier1", "tier2"],
        },
        {
            "key": "tier3",
            "role_name": "Subscription Tier3",
            "category_name": "サブスクTier 3",
            "channel_name": "tier-3",
            "view_roles": ["tier1", "tier2", "tier3"],
        },
    ],
    "linked_role_name": "Twitch-linked",
    "notify_role_name": "Subscription Tier1",
    "notify_channel_id": None,
}


def _load_subscription_definition() -> dict[str, Any]:
    user_config = load_subscription_config()
    if not user_config:
        save_subscription_config(DEFAULT_SUBSCRIPTION_CONFIG)
        user_config = {}

    merged = copy.deepcopy(DEFAULT_SUBSCRIPTION_CONFIG)
    if isinstance(user_config, dict):
        for key, value in user_config.items():
            if key == "tiers" or value is None:
                continue
            merged[key] = value

    tiers: list[dict[str, Any]] = []
    user_tiers = user_config.get("tiers") if isinstance(user_config, dict) else None
    if isinstance(user_tiers, list) and user_tiers:
        for idx, entry in enumerate(user_tiers):
            if not isinstance(entry, dict):
                continue
            if idx < len(DEFAULT_SUBSCRIPTION_CONFIG["tiers"]):
                base = copy.deepcopy(DEFAULT_SUBSCRIPTION_CONFIG["tiers"][idx])
            else:
                base = {
                    "key": entry.get("key") or f"tier{idx + 1}",
                    "role_name": entry.get("role_name")
                    or entry.get("key")
                    or f"Tier{idx + 1}",
                    "category_name": entry.get("category_name")
                    or entry.get("role_name")
                    or f"Category{idx + 1}",
                    "channel_name": entry.get("channel_name") or f"channel-{idx + 1}",
                    "view_roles": entry.get("view_roles")
                    or [entry.get("key") or f"tier{idx + 1}"],
                }
            for key, value in entry.items():
                if value is not None:
                    base[key] = value
            if "key" not in base or not base["key"]:
                base["key"] = f"tier{idx + 1}"
            tiers.append(base)
    if not tiers:
        tiers = copy.deepcopy(DEFAULT_SUBSCRIPTION_CONFIG["tiers"])
    merged["tiers"] = tiers
    return merged


SUBSCRIPTION_CONFIG = _load_subscription_definition()
TIER_CONFIG = SUBSCRIPTION_CONFIG.get("tiers", [])

ROLE_BY_KEY: dict[str, str] = {}
TIER_ENTRIES: list[dict[str, Any]] = []
ROLE_NAMES_LIST: list[str] = []
CHANNEL_NAMES_LIST: list[str] = []
CATEGORY_NAMES: list[str] = []
CATEGORY_ROLE_MAP: dict[str, str] = {}
CHANNEL_ROLE_MAP: dict[str, str] = {}
CATEGORY_CHANNEL_MAP: dict[str, str] = {}
CATEGORY_ALLOWED_ROLE_NAMES: dict[str, list[str]] = {}

for entry in TIER_CONFIG:
    key = str(entry.get("key") or f"tier{len(TIER_ENTRIES) + 1}")
    role_name = str(entry.get("role_name") or key)
    category_name = str(
        entry.get("category_name") or entry.get("channel_name") or role_name
    )
    channel_name = str(entry.get("channel_name") or role_name)
    allowed = entry.get("view_roles") or [key]

    ROLE_BY_KEY[key] = role_name
    if role_name not in ROLE_NAMES_LIST:
        ROLE_NAMES_LIST.append(role_name)
    if channel_name not in CHANNEL_NAMES_LIST:
        CHANNEL_NAMES_LIST.append(channel_name)
    if category_name not in CATEGORY_NAMES:
        CATEGORY_NAMES.append(category_name)

    CATEGORY_ROLE_MAP[category_name] = role_name
    CHANNEL_ROLE_MAP[channel_name] = role_name
    CATEGORY_CHANNEL_MAP[category_name] = channel_name

    allowed_role_names: list[str] = []
    for item in allowed:
        resolved = ROLE_BY_KEY.get(str(item))
        if not resolved and isinstance(item, str) and item in ROLE_BY_KEY.values():
            resolved = item
        if not resolved and item == key:
            resolved = role_name
        resolved = resolved or str(item)
        if resolved not in allowed_role_names:
            allowed_role_names.append(resolved)
    if role_name not in allowed_role_names:
        allowed_role_names.append(role_name)

    CATEGORY_ALLOWED_ROLE_NAMES[category_name] = allowed_role_names
    TIER_ENTRIES.append(
        {
            "key": key,
            "role_name": role_name,
            "category_name": category_name,
            "channel_name": channel_name,
            "view_role_names": allowed_role_names,
        }
    )

LINKED_ROLE_NAME = SUBSCRIPTION_CONFIG.get("linked_role_name") or "Twitch-linked"
if LINKED_ROLE_NAME and LINKED_ROLE_NAME not in ROLE_NAMES_LIST:
    ROLE_NAMES_LIST.append(LINKED_ROLE_NAME)
# ===== Discord Bot の準備 =====
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)
JST = zoneinfo.ZoneInfo("Asia/Tokyo")
BOT_LOOP = None  # will be captured in on_ready()

# ===== FastAPI アプリ =====
IS_PROD = (os.getenv("APP_ENV") or os.getenv("ENV") or "").lower() in (
    "prod",
    "production",
)
app = FastAPI(
    docs_url=None if IS_PROD else "/docs",
    redoc_url=None if IS_PROD else "/redoc",
    openapi_url=None if IS_PROD else "/openapi.json",
)


STREAM_NOTIFY_ROLE_NAME = SUBSCRIPTION_CONFIG.get("notify_role_name")
if not STREAM_NOTIFY_ROLE_NAME:
    notify_entry = next((e for e in TIER_ENTRIES if e.get("key") == "notify"), None)
    if notify_entry:
        STREAM_NOTIFY_ROLE_NAME = notify_entry.get("role_name")
    elif TIER_ENTRIES:
        STREAM_NOTIFY_ROLE_NAME = TIER_ENTRIES[0].get("role_name")
    elif ROLE_NAMES_LIST:
        STREAM_NOTIFY_ROLE_NAME = ROLE_NAMES_LIST[0]

cfg_channel_id = SUBSCRIPTION_CONFIG.get("notify_channel_id")
if not cfg_channel_id:
    notify_entry = next((e for e in TIER_ENTRIES if e.get("key") == "notify"), None)
    if notify_entry:
        cfg_channel_id = notify_entry.get("channel_id")
    else:
        tier1_entry = next((e for e in TIER_ENTRIES if e.get("key") == "tier1"), None)
        if tier1_entry:
            cfg_channel_id = tier1_entry.get("channel_id")

try:
    STREAM_NOTIFY_CHANNEL_ID = int(cfg_channel_id) if cfg_channel_id else None
except (TypeError, ValueError):
    STREAM_NOTIFY_CHANNEL_ID = None

if STREAM_NOTIFY_ROLE_NAME and STREAM_NOTIFY_ROLE_NAME not in ROLE_NAMES_LIST:
    ROLE_NAMES_LIST.append(STREAM_NOTIFY_ROLE_NAME)


# ---- Bot ループにコルーチンを投げる小ヘルパ ----
def run_in_bot_loop(coro: Coroutine[Any, Any, Any]):
    """Discord Bot のイベントループで coro を実行して、例外をログに出す"""
    fut = asyncio.run_coroutine_threadsafe(coro, bot.loop)

    def _done(f):
        try:
            f.result()
        except Exception as e:
            debug_print("❌ notify error:", repr(e))

    fut.add_done_callback(_done)
    return fut


def schedule_in_bot_loop(coro: Coroutine[Any, Any, Any]):
    """Safely schedule a coroutine onto the Discord bot loop with logs."""
    try:
        name = getattr(coro, "__name__", None) or str(coro)
    except Exception:
        name = str(coro)
    loop = BOT_LOOP or getattr(bot, "loop", None)
    if loop is None:
        debug_print(f"[loop] no bot loop available; cannot schedule {name}")
        return None
    debug_print(f"[loop] scheduling on {loop}: {name}")
    fut = asyncio.run_coroutine_threadsafe(coro, loop)

    def _done(f):
        try:
            f.result()
            debug_print("[loop] task completed successfully")
        except Exception as e:
            debug_print("[loop] task error:", repr(e))

    fut.add_done_callback(_done)
    return fut


# ---- Bot側で実際に送信する処理（Botのループ上で動く）----


async def notify_discord_user(
    discord_id: int, twitch_name: str, tier: str, streak: int | None = None
):
    await bot.wait_until_ready()
    user = await bot.fetch_user(discord_id)
    if not user:
        debug_print(f"⚠ fetch_user({discord_id}) が None")
        return
    msg = f"✅ Twitch `{twitch_name}` とリンクしました！Tier: {tier}"
    if streak is not None:
        msg += f", Streak: {streak}"
    await user.send(msg)


async def _send_dm(
    user: discord.User | discord.Member,
    message: str,
    attachments: list[dict[str, Any]] | None = None,
    file_url: str | None = None,
    file_path: str | None = None,
):
    MAX_FILES_PER_MESSAGE = 10

    async def _load_bytes(spec: dict[str, Any]) -> tuple[bytes, str] | None:
        path = spec.get("path")
        url = spec.get("url")
        display_name = spec.get("name")
        if path:
            try:
                if os.path.isfile(path):
                    debug_print(f"[DM] reading local attachment: {path}")
                    with open(path, "rb") as fp:
                        data = fp.read()
                    filename_local = (
                        display_name or os.path.basename(path) or "attachment"
                    )
                    return data, filename_local
                debug_print(f"[DM] local attachment not found: {path}")
            except Exception as exc:
                debug_print(f"[DM] failed reading local file: {exc!r}")
        if url:
            try:
                async with httpx.AsyncClient(timeout=20) as client:
                    debug_print(f"[DM] downloading attachment: {url}")
                    response = await client.get(url)
                    response.raise_for_status()
                    filename_remote = (
                        display_name or url.rsplit("/", 1)[-1] or "attachment"
                    )
                    debug_print(
                        f"[DM] downloaded: status={response.status_code} bytes={len(response.content)}"
                    )
                    return response.content, filename_remote
            except Exception as exc:
                debug_print(f"[DM] failed downloading file: {exc!r}")
        return None

    def _make_file_objects(items: list[tuple[bytes, str]]) -> list[discord.File]:
        files: list[discord.File] = []
        for data, filename in items:
            files.append(discord.File(io.BytesIO(data), filename))
        return files

    async def _send_sequential(
        target: discord.User | discord.Member,
        text: str,
        items: list[tuple[bytes, str]],
    ) -> None:
        if text:
            try:
                await target.send(content=text)
                debug_print(
                    f"[DM] sent text fallback -> user={getattr(target, 'id', '?')}"
                )
            except Exception as exc:
                debug_print(
                    f"[DM] fallback text failed user={getattr(target, 'id', '?')}: {exc!r}"
                )
        for data, filename in items:
            try:
                await target.send(file=discord.File(io.BytesIO(data), filename))
                debug_print(
                    f"[DM] sent attachment fallback -> user={getattr(target, 'id', '?')} {filename}"
                )
            except Exception as exc:
                debug_print(
                    f"[DM] fallback attachment failed user={getattr(target, 'id', '?')} file={filename}: {exc!r}"
                )

    try:
        attachment_specs: list[dict[str, Any]] = []
        if attachments:
            for item in attachments:
                if isinstance(item, dict):
                    attachment_specs.append(
                        {
                            "url": item.get("url") or item.get("file_url"),
                            "path": item.get("path") or item.get("file_path"),
                            "name": item.get("name"),
                        }
                    )
        if (file_url or file_path) and not attachment_specs:
            attachment_specs.append(
                {
                    "url": file_url,
                    "path": file_path,
                    "name": None,
                }
            )

        debug_print(
            f"[DM] start -> user={getattr(user, 'id', '?')} attachments={len(attachment_specs)} msg_len={(len(message or ''))}"
        )

        prepared: list[tuple[bytes, str]] = []
        for spec in attachment_specs:
            loaded = await _load_bytes(spec)
            if loaded is None:
                continue
            prepared.append(loaded)

        if not prepared:
            await user.send(content=message or None)
            debug_print(f"[DM] sent text -> user={getattr(user, 'id', '?')}")
            return

        if len(prepared) > MAX_FILES_PER_MESSAGE:
            debug_print(
                f"[DM] too many attachments ({len(prepared)}) -> sending sequentially"
            )
            await _send_sequential(user, message, prepared)
            return

        try:
            files = _make_file_objects(prepared)
            if len(files) == 1:
                await user.send(content=(message or None), file=files[0])
            else:
                await user.send(content=(message or None), files=files)
            debug_print(
                f"[DM] sent with {len(files)} attachment(s) -> user={getattr(user, 'id', '?')}"
            )
        except discord.HTTPException as exc:
            debug_print(
                f"[DM] send with attachments failed user={getattr(user, 'id', '?')}: {exc!r}; falling back"
            )
            await _send_sequential(user, message, prepared)
        except Exception as exc:
            debug_print(
                f"[DM] unexpected send error user={getattr(user, 'id', '?')}: {exc!r}; falling back"
            )
            await _send_sequential(user, message, prepared)
    except Exception as e:
        debug_print(f"[DM] failed to {getattr(user, 'id', '?')}: {e!r}")


def _coerce_int_list(value: Any) -> list[int]:
    if value is None:
        return []
    source = value if isinstance(value, list) else [value]
    result: list[int] = []
    seen: set[int] = set()
    for item in source:
        try:
            num = int(item)
        except (TypeError, ValueError):
            continue
        if num in seen:
            continue
        seen.add(num)
        result.append(num)
    return result


def _build_allowed_member_ids(streak_filters: list[int]) -> set[str]:
    filters = {int(val) for val in streak_filters if isinstance(val, int)}
    if not filters:
        return set()
    try:
        users = load_users()
    except Exception:
        users = {}
    allowed: set[str] = set()
    for discord_id, payload in (users or {}).items():
        if not isinstance(payload, dict):
            continue
        try:
            streak_val = int(payload.get("streak_months"))
        except (TypeError, ValueError):
            continue
        if streak_val in filters:
            allowed.add(str(discord_id))
    return allowed


async def notify_role_members(
    role_id: int,
    message: str,
    attachments: list[dict[str, Any]] | None = None,
    file_url: str | None = None,
    file_path: str | None = None,
    guild_id: int | None = None,
    allowed_member_ids: set[str] | frozenset[str] | None = None,
):
    await bot.wait_until_ready()
    attachments_count = len(attachments or [])
    allowed_set: set[str] | None = None
    if allowed_member_ids is not None:
        allowed_set = {str(mid) for mid in allowed_member_ids}
    debug_print(
        f"[/send_role_dm] begin notify_role_members role_id={role_id} guild_id={guild_id} msg_len={(len(message or ''))} attachments={attachments_count} has_legacy_file={bool(file_url or file_path)} streak_filter={len(allowed_set) if allowed_set is not None else 'all'}"
    )
    guild: discord.Guild | None = None
    if guild_id is not None:
        guild = bot.get_guild(int(guild_id))
    if guild is None:
        # Try to find the guild that contains this role id
        for g in bot.guilds:
            if g.get_role(int(role_id)) is not None:
                guild = g
                break
    if guild is None:
        # Fallback to configured guild
        try:
            gid = get_guild_id()
            guild = bot.get_guild(int(gid))
        except Exception:
            guild = None
    if guild is None:
        debug_print(f"[DM] guild not found for role {role_id}")
        return
    role = guild.get_role(int(role_id))
    if role is None:
        debug_print(f"[DM] role {role_id} not found in {guild.name}")
        return
    members = list(role.members)
    targets: list[discord.Member] = []
    for m in members:
        if m.bot:
            continue
        if allowed_set is not None and str(m.id) not in allowed_set:
            continue
        targets.append(m)
    debug_print(
        f"[DM] target guild={guild.id}({guild.name}) role={role.id}({role.name}) members={len(members)} filtered={len(targets)}"
    )
    if not targets:
        debug_print("[DM] no matching members for streak filter; abort send")
        return
    for m in targets:
        # Per-user placeholder replacement (minimal): {user}
        try:
            dm_text = message
            if dm_text and "{user}" in dm_text:
                username = (
                    getattr(m, "display_name", None)
                    or getattr(m, "name", None)
                    or str(m)
                )
                dm_text = dm_text.replace("{user}", str(username))
        except Exception:
            dm_text = message
        await _send_dm(
            m,
            dm_text,
            attachments=attachments,
            file_url=file_url,
            file_path=file_path,
        )
        await asyncio.sleep(0.3)
    debug_print(
        f"[/send_role_dm] done for role_id={role_id} guild_id={getattr(guild, 'id', None)}"
    )


async def notify_stream_online(event: dict[str, Any]) -> None:
    await bot.wait_until_ready()
    broadcaster_login = (
        event.get("broadcaster_user_login")
        or event.get("user_login")
        or event.get("broadcaster_user_name")
        or event.get("user_name")
    )
    display_name = (
        event.get("broadcaster_user_name")
        or event.get("user_name")
        or broadcaster_login
        or "配信者"
    )
    if not broadcaster_login:
        debug_print("[stream.online] broadcaster login missing; skip notify")
    started_at_raw = event.get("started_at") or event.get("event_timestamp")
    started_display = None
    if started_at_raw:
        try:
            ts = started_at_raw.replace("Z", "+00:00")
            dt_value = dt.datetime.fromisoformat(ts)
            started_display = dt_value.astimezone(JST).strftime("%Y-%m-%d %H:%M")
        except Exception:
            started_display = None

    twitch_url = (
        f"https://www.twitch.tv/{broadcaster_login}" if broadcaster_login else None
    )
    channel_map = load_channel_ids()

    for guild in bot.guilds:
        channel_id_value = STREAM_NOTIFY_CHANNEL_ID
        if channel_id_value is None:
            mapping = channel_map.get(str(guild.id)) or channel_map.get(guild.id)
            if isinstance(mapping, dict):
                channel_id_value = mapping.get(STREAM_NOTIFY_ROLE_NAME)
        if not channel_id_value:
            continue
        try:
            channel_id_int = int(channel_id_value)
        except (TypeError, ValueError):
            continue

        channel = guild.get_channel(channel_id_int)
        if channel is None:
            try:
                channel = await guild.fetch_channel(channel_id_int)
            except Exception as exc:
                debug_print(
                    f"[stream.online] channel {channel_id_int} missing in guild {guild.id}: {exc!r}"
                )
                continue

        role = discord.utils.get(guild.roles, name=STREAM_NOTIFY_ROLE_NAME)
        mention = role.mention if role else ""
        lines = []
        if mention:
            lines.append(mention)
        lines.append(f"{display_name} さんが配信を開始しました！")
        if started_display:
            lines.append(f"開始時刻 (JST): {started_display}")
        if twitch_url:
            lines.append(twitch_url)
        message = "\n".join(lines)
        try:
            await channel.send(message)
        except Exception as exc:
            debug_print(
                f"[stream.online] failed to send message to channel {channel_id_int}: {exc!r}"
            )


# ---- API: 直接Discordに通知する（外部/内部から叩ける）----
@app.post("/notify_link")
async def notify_link(
    discord_id: int,
    twitch_name: str,
    tier: str,
    authorization: str | None = Header(None, alias="Authorization"),
):
    if not _require_admin_token(authorization):
        return PlainTextResponse("forbidden", status_code=403)
    schedule_in_bot_loop(notify_discord_user(discord_id, twitch_name, tier))
    return {"status": "queued"}


# ===== 管理API: ロール一覧とロールDMキュー =====
ADMIN_API_TOKEN = get_admin_api_token()


def _require_admin_token(auth_header: str | None) -> bool:
    if not ADMIN_API_TOKEN:
        debug_print("[ADMIN] token check: server-side token missing (reject)")
        return False
    if not auth_header:
        debug_print("[ADMIN] token check: Authorization header missing (reject)")
        return False
    try:
        scheme, token = auth_header.split(" ", 1)
    except ValueError:
        debug_print("[ADMIN] token check: malformed Authorization header (reject)")
        return False
    ok = scheme.lower() == "bearer" and token.strip() == ADMIN_API_TOKEN
    debug_print(f"[ADMIN] token check: {'ok' if ok else 'reject'}")
    return ok


@app.get("/guilds")
async def list_guilds(authorization: str | None = Header(None, alias="Authorization")):
    if not _require_admin_token(authorization):
        return PlainTextResponse("forbidden", status_code=403)
    await bot.wait_until_ready()
    guilds = [
        {"id": g.id, "name": g.name}
        for g in sorted(bot.guilds, key=lambda x: x.name.lower())
    ]
    debug_print(f"[/guilds] return {len(guilds)} guild(s)")
    return {"guilds": guilds}


@app.get("/roles")
async def list_roles(
    authorization: str | None = Header(None, alias="Authorization"),
    guild_id: int | None = None,
):
    if not _require_admin_token(authorization):
        return PlainTextResponse("forbidden", status_code=403)
    await bot.wait_until_ready()
    guild: discord.Guild | None = None
    if guild_id is not None:
        guild = bot.get_guild(int(guild_id))
    if guild is None:
        try:
            gid = get_guild_id()
            guild = bot.get_guild(int(gid))
        except Exception:
            guild = None
    if guild is None:
        debug_print(f"[/roles] guild not found (guild_id={guild_id}) -> []")
        return JSONResponse({"roles": []})
    roles = [
        {"id": r.id, "name": r.name}
        for r in sorted(guild.roles, key=lambda x: x.position, reverse=True)
        if r.name != "@everyone"
    ]
    debug_print(f"[/roles] guild={guild.id}({guild.name}) roles={len(roles)}")
    return {"roles": roles}


@app.post("/send_role_dm")
async def send_role_dm(
    request: Request,
    authorization: str | None = Header(None, alias="Authorization"),
):
    if not _require_admin_token(authorization):
        return PlainTextResponse("forbidden", status_code=403)
    payload = await request.json()
    role_id = int(payload.get("role_id"))
    message = str(payload.get("message") or "")
    file_url = payload.get("file_url")
    file_path = payload.get("file_path")
    attachments_payload = payload.get("attachments") or []
    attachments: list[dict[str, Any]] = []
    if isinstance(attachments_payload, list):
        for item in attachments_payload:
            if not isinstance(item, dict):
                continue
            attachments.append(
                {
                    "url": item.get("url") or item.get("file_url"),
                    "path": item.get("path") or item.get("file_path"),
                    "name": item.get("name"),
                }
            )
    guild_id_value = payload.get("guild_id")
    # Validate placeholders and reject unknown ones
    unknown = _unknown_placeholders(message)
    if unknown:
        return JSONResponse(
            {
                "error": "unknown_placeholders",
                "unknown": unknown,
                "allowed": sorted(ALLOWED_PLACEHOLDERS),
            },
            status_code=400,
        )
    attachments_count = len(attachments)
    if attachments_count == 0 and (file_url or file_path):
        attachments.append({"url": file_url, "path": file_path, "name": None})
        attachments_count = len(attachments)

    streak_filters = _coerce_int_list(payload.get("streak_filters"))
    allowed_member_ids: set[str] | None = None
    if streak_filters:
        allowed_member_ids = _build_allowed_member_ids(streak_filters)
    preview_only = bool(payload.get("preview_only"))

    guild_id: int | None = int(guild_id_value) if guild_id_value else None

    debug_print(
        f"[/send_role_dm] payload role_id={role_id} guild_id={guild_id} msg_len={(len(message or ''))} attachments={attachments_count} streak_filters={streak_filters or 'all'} preview_only={preview_only}"
    )

    await bot.wait_until_ready()
    resolved_guild: discord.Guild | None = None
    resolved_role: discord.Role | None = None
    recipients: list[dict[str, Any]] = []
    try:
        if guild_id is not None:
            resolved_guild = bot.get_guild(int(guild_id))
        if resolved_guild is None:
            for guild in bot.guilds:
                role_candidate = guild.get_role(int(role_id))
                if role_candidate is not None:
                    resolved_guild = guild
                    resolved_role = role_candidate
                    break
        if resolved_guild is None:
            try:
                fallback_gid = get_guild_id()
                resolved_guild = bot.get_guild(int(fallback_gid))
            except Exception:
                resolved_guild = None
        if resolved_guild and resolved_role is None:
            resolved_role = resolved_guild.get_role(int(role_id))
        if resolved_role:
            for member in resolved_role.members:
                if getattr(member, "bot", False):
                    continue
                if allowed_member_ids is not None and str(member.id) not in allowed_member_ids:
                    continue
                display_name = (
                    getattr(member, "display_name", None)
                    or getattr(member, "nick", None)
                    or getattr(member, "name", None)
                    or str(member)
                )
                username = getattr(member, "name", None) or str(member)
                discriminator = getattr(member, "discriminator", None)
                recipients.append(
                    {
                        "id": int(member.id),
                        "display_name": str(display_name),
                        "username": str(username),
                        "discriminator": discriminator,
                    }
                )
        debug_print(
            f"[/send_role_dm] resolved recipients={len(recipients)} guild={getattr(resolved_guild, 'id', None)}"
        )
    except Exception as exc:
        debug_print(f"[/send_role_dm] failed to resolve recipients: {exc!r}")

    allowed_member_ids_frozen = (
        frozenset(allowed_member_ids) if allowed_member_ids is not None else None
    )

    if not preview_only:
        schedule_in_bot_loop(
            notify_role_members(
                role_id,
                message,
                attachments,
                file_url,
                file_path,
                (
                    int(guild_id)
                    if guild_id is not None
                    else getattr(resolved_guild, "id", None)
                ),
                allowed_member_ids=allowed_member_ids_frozen,
            )
        )
        debug_print("[/send_role_dm] queued notify task")
    else:
        debug_print("[/send_role_dm] preview_only=True -> skip notify task")
    return {
        "status": "preview" if preview_only else "queued",
        "recipients": recipients,
        "recipient_count": len(recipients),
        "guild_id": getattr(resolved_guild, "id", None),
        "guild_name": getattr(resolved_guild, "name", None),
        "role_id": role_id,
        "role_name": getattr(resolved_role, "name", None),
        "preview_only": preview_only,
    }


@app.get("/eventsub/subscriptions")
async def eventsub_list(
    authorization: str | None = Header(None, alias="Authorization"),
    status: str | None = None,
):
    if not _require_admin_token(authorization):
        return PlainTextResponse("forbidden", status_code=403)
    try:
        subs = await list_eventsub_subscriptions(status=status)
        callback_url, _ = get_eventsub_config()
        return {"subscriptions": subs, "default_callback": callback_url}
    except Exception as exc:
        debug_print(f"[EventSub][list] failed: {exc!r}")
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/eventsub/subscriptions")
async def eventsub_create(
    request: Request,
    authorization: str | None = Header(None, alias="Authorization"),
):
    if not _require_admin_token(authorization):
        return PlainTextResponse("forbidden", status_code=403)
    payload = await request.json()
    sub_type = str(payload.get("type") or "").strip()
    if not sub_type:
        return JSONResponse({"error": "missing_type"}, status_code=400)
    version = str(payload.get("version") or "1").strip() or "1"
    condition = payload.get("condition")
    callback = payload.get("callback")
    secret = payload.get("secret")
    try:
        twitch_status, response_payload = await create_eventsub_subscription(
            sub_type,
            version=version,
            condition=condition if isinstance(condition, dict) else None,
            callback_url=callback,
            secret=secret,
        )
    except Exception as exc:
        debug_print(f"[EventSub][create] failed: {exc!r}")
        return JSONResponse({"error": str(exc)}, status_code=500)

    ok = 200 <= twitch_status < 300
    body = {
        "status": "ok" if ok else "error",
        "twitch_status": twitch_status,
        "response": response_payload,
    }
    return JSONResponse(body, status_code=200 if ok else 400)


@app.delete("/eventsub/subscriptions/{subscription_id}")
async def eventsub_delete(
    subscription_id: str,
    authorization: str | None = Header(None, alias="Authorization"),
):
    if not _require_admin_token(authorization):
        return PlainTextResponse("forbidden", status_code=403)
    try:
        twitch_status = await delete_eventsub_subscription(subscription_id)
    except Exception as exc:
        debug_print(f"[EventSub][delete] failed: {exc!r}")
        return JSONResponse({"error": str(exc)}, status_code=500)

    ok = 200 <= twitch_status < 300
    body = {
        "status": "ok" if ok else "error",
        "twitch_status": twitch_status,
    }
    # Twitch DELETE success = 204
    return JSONResponse(body, status_code=200 if ok else 400)


# ---- API: Twitch OAuth コールバック ----
@app.get("/twitch_callback")
async def twitch_callback(request: Request):
    debug_print("✅ [twitch_callback] にアクセスがありました")
    code = request.query_params.get("code")
    state = request.query_params.get("state")  # DiscordのユーザーID（str）

    if not code or not state:
        return PlainTextResponse("Missing code or state", status_code=400)

    # 1) Twitch クレデンシャル
    try:
        client_id, client_secret, redirect_uri = get_twitch_keys()
    except Exception as e:
        return PlainTextResponse(f"Failed to read credentials: {e!r}", status_code=500)

    # 2) アクセストークン取得（非同期 httpx）
    token_url = "https://id.twitch.tv/oauth2/token"
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(token_url, data=payload, headers=headers)
    except httpx.HTTPError as e:
        return PlainTextResponse(f"Token request failed: {e!r}", status_code=502)

    if resp.status_code != 200:
        return PlainTextResponse(f"Failed to get token: {resp.text}", status_code=502)

    data = resp.json()
    access_token = data.get("access_token")
    if not access_token:
        return PlainTextResponse("Access token not found", status_code=502)

    # 3) broadcaster_id を解決
    try:
        broadcaster_id_raw = get_broadcast_id()
        BROADCASTER_ID = str(broadcaster_id_raw)
        debug_print(f"[DEBUG] get_broadcast_id -> {BROADCASTER_ID!r}")
    except Exception as e:
        return PlainTextResponse(
            f"Failed to resolve broadcaster_id: {e!r}", status_code=500
        )

    # 4) ユーザー情報 & サブスク情報（dict 返り値）
    try:
        info = await get_user_info_and_subscription(
            viewer_access_token=access_token,
            client_id=client_id,
            broadcaster_id=BROADCASTER_ID,
        )
    except httpx.HTTPError as e:
        return PlainTextResponse(f"Helix request failed: {e!r}", status_code=502)
    except Exception as e:
        return PlainTextResponse(
            f"Failed to fetch user/sub info: {e!r}", status_code=500
        )

    twitch_user_name = info.get("twitch_username")
    if not twitch_user_name:
        return PlainTextResponse("Failed to get Twitch user info", status_code=502)

    # 5) リンク情報を保存（streak自前更新版）
    try:
        # debug_print("reconcile_and_save_link success")
        rec = reconcile_and_save_link(str(state), info)
    except Exception as e:
        debug_print(f"❌ reconcile_and_save_link failed: {e!r}")
        rec = info  # 万一失敗したら元のinfoを使う

    # 6) Discord通知
    try:
        # Disabled to avoid duplicate DM; handled in LinkCog
        if False:
            schedule_in_bot_loop(
                notify_discord_user(
                    int(state),
                    rec.get("twitch_username"),
                    rec.get("tier"),
                    rec.get("streak_months", 0),
                )
            )
    except Exception as e:
        debug_print("❌ failed to schedule notify:", repr(e))

    return PlainTextResponse("連携完了", status_code=200)


# ---- EventSub Webhook ----
def _hmac_sha256(secret: str, message: bytes) -> str:
    mac = hmac.new(secret.encode("utf-8"), message, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def _verify_signature(
    secret: str, msg_id: str, msg_ts: str, body: bytes, signature: str
) -> bool:
    message = (msg_id + msg_ts).encode("utf-8") + body
    expected = _hmac_sha256(secret, message)
    return hmac.compare_digest(expected, signature)


def _find_discord_ids_by_twitch_id(twitch_user_id: str) -> list[str]:
    users = load_users()
    res = []
    for did, info in users.items():
        if isinstance(info, dict) and str(info.get("twitch_user_id")) == str(
            twitch_user_id
        ):
            res.append(str(did))
    return res


@app.get("/twitch_eventsub")
async def twitch_eventsub_probe() -> PlainTextResponse:
    """Health check endpoint for Twitch verification pings (GET)."""
    return PlainTextResponse("ok", status_code=200)


@app.head("/twitch_eventsub")
async def twitch_eventsub_head() -> PlainTextResponse:
    """Respond to HEAD requests with empty 200 to satisfy preflight checks."""
    return PlainTextResponse("", status_code=200)


@app.post("/twitch_eventsub")
async def twitch_eventsub(
    request: Request,
    twitch_msg_id: str = Header(None, alias="Twitch-Eventsub-Message-Id"),
    twitch_msg_type: str = Header(None, alias="Twitch-Eventsub-Message-Type"),
    twitch_msg_ts: str = Header(None, alias="Twitch-Eventsub-Message-Timestamp"),
    twitch_signature: str = Header(None, alias="Twitch-Eventsub-Message-Signature"),
):
    body_bytes = await request.body()
    if body_bytes is None:
        body_bytes = b""
    try:
        data = json.loads(body_bytes.decode("utf-8") or "{}")
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}

    if twitch_msg_type == "webhook_callback_verification":
        challenge = data.get("challenge")
        debug_print("[EventSub] verification")
        return PlainTextResponse(challenge or "", status_code=200)

    try:
        _, secret = get_eventsub_config()
    except Exception as e:
        return PlainTextResponse(f"EventSub secret missing: {e}", status_code=500)

    if not (twitch_msg_id and twitch_msg_ts and twitch_signature):
        return PlainTextResponse("missing headers", status_code=400)

    if not _verify_signature(
        secret, twitch_msg_id, twitch_msg_ts, body_bytes, twitch_signature
    ):
        return PlainTextResponse("invalid signature", status_code=403)

    if twitch_msg_type == "notification":
        sub_type = (data.get("subscription") or {}).get("type")
        event = data.get("event") or {}
        debug_print(f"[EventSub] notify: {sub_type}")

        try:
            inbox_enqueue_event(
                source="twitch",
                delivery_id=str(twitch_msg_id),
                event_type=str(sub_type or ""),
                twitch_user_id=str(
                    event.get("user_id")
                    or event.get("user")
                    or event.get("user_login")
                    or ""
                )
                or None,
                payload=data,
                headers={
                    "Twitch-Eventsub-Message-Id": twitch_msg_id,
                    "Twitch-Eventsub-Message-Type": twitch_msg_type,
                    "Twitch-Eventsub-Message-Timestamp": twitch_msg_ts,
                },
                status="pending",
            )
        except Exception as e:
            debug_print(f"[EventSub][inbox] enqueue failed: {e!r}")

        try:
            matched = apply_event_to_linked_users(sub_type, event, twitch_msg_ts)
            if sub_type == "stream.online":
                schedule_in_bot_loop(notify_stream_online(event))
            inbox_mark_processed("twitch", str(twitch_msg_id), ok=True)
            return JSONResponse({"status": "ok", "matched": matched})
        except Exception as e:
            debug_print(f"[EventSub] apply failed: {e!r}")
            inbox_mark_processed("twitch", str(twitch_msg_id), ok=False, error=str(e))
            return JSONResponse({"status": "ok", "matched": 0})

    if twitch_msg_type == "revocation":
        debug_print("[EventSub] revoked:", data)
        return JSONResponse({"status": "revoked"})

    return PlainTextResponse("ignored", status_code=200)


# ===== FastAPI を別スレッドで起動 =====
def start_api():
    host = os.getenv("FASTAPI_HOST", "127.0.0.1")
    try:
        port = int(os.getenv("FASTAPI_PORT", "8000"))
    except ValueError:
        port = 8000
    uvicorn.run(app, host=host, port=port, log_level="info")


# ===== Discord Bot を起動 =====
async def run_discord_bot():
    with open("./venv/token.json", "r", encoding="utf-8") as f:
        token = json.load(f)["discord_token"]

    bot.load_extension("bot.cogs.link")
    bot.load_extension("bot.cogs.unlink")
    bot.load_extension("bot.monthly_relink_bot")
    bot.load_extension("bot.cogs.auto_link_dm")

    await bot.start(token)


@bot.event
async def on_ready():
    debug_print(f"login: {bot.user}")
    # Capture running loop for cross-thread scheduling
    try:
        global BOT_LOOP
        BOT_LOOP = asyncio.get_running_loop()
        debug_print(f"[loop] captured: {BOT_LOOP}")
    except Exception as e:
        debug_print(f"[loop] capture failed: {e!r}")
    save_all_guild_members(bot)
    await make_subrole(bot)
    await make_category_and_channel(bot)
    # EventSub購読を（可能なら）登録
    try:
        await register_eventsub_subscriptions()
    except Exception as e:
        debug_print(f"[EventSub] registration skipped or failed: {e!r}")


async def ensure_role_exists(
    guild: discord.Guild,
    role_name: str,
    color: discord.Colour = discord.Colour.default(),
):
    # 既に存在しているかチェック
    role = discord.utils.get(guild.roles, name=role_name)
    if role is None:
        debug_print(f"ロール「{role_name}」が存在しないため作成します")
        role = await guild.create_role(
            name=role_name, colour=color, reason="Twitchサブスク用自動作成"
        )
    else:
        debug_print(f"ロール「{role_name}」は既に存在します")
    return role


async def make_subrole(bot):
    guilds = bot.guilds

    role_data = load_role_ids()

    for guild in guilds:
        role_id_dic = role_data.get(str(guild.id), {})

        for role_name in ROLE_NAMES_LIST:
            role = await ensure_role_exists(guild, role_name)
            role_id_dic[role.name] = role.id
        role_data[str(guild.id)] = role_id_dic
    save_role_ids(role_data)


async def ensure_text_channel_exists(
    guild: discord.Guild,
    channel_name: str,
    overwrites: dict[discord.Role, discord.PermissionOverwrite] | None = None,
    category: discord.CategoryChannel | None = None,
    reason: str = "Twitchサブスク用自動生成",
) -> discord.TextChannel:
    channel = discord.utils.get(guild.text_channels, name=channel_name)
    if channel is None:
        debug_print(
            f"チャンネル「{channel_name}」が存在しないため作成します in {guild.name}"
        )
        channel = await guild.create_text_channel(
            name=channel_name, category=category, reason=reason, overwrites=overwrites
        )
    else:
        debug_print(f"チャンネル「{channel_name}」はすでに存在します in {guild.name}")

    return channel


async def ensure_category_exists(
    guild: discord.Guild,
    category_name: str,
    overwrites: dict[discord.Role, discord.PermissionOverwrite] | None = None,
    reason: str = "Twitchサブスク用自動生成",
) -> discord.CategoryChannel:
    category = discord.utils.get(guild.categories, name=category_name)
    if category is None:
        debug_print(
            f"カテゴリー「{category_name}」が存在しないため作成します in {guild.name}"
        )
        category = await guild.create_category(
            name=category_name, reason=reason, overwrites=overwrites
        )
    else:
        debug_print(f"カテゴリー「{category_name}」はすでに存在します in {guild.name}")

    return category


async def make_category_and_channel(bot):
    guilds = bot.guilds
    category_data = load_subscription_categories()
    channel_data = load_channel_ids()
    for guild in guilds:
        everyone_role = guild.default_role
        tier_role_dic: dict[str, discord.Role | None] = {}
        for entry in TIER_ENTRIES:
            role_obj = discord.utils.get(guild.roles, name=entry["role_name"])
            tier_role_dic[entry["role_name"]] = role_obj
        if LINKED_ROLE_NAME:
            linked_role_obj = discord.utils.get(guild.roles, name=LINKED_ROLE_NAME)
            tier_role_dic[LINKED_ROLE_NAME] = linked_role_obj

        category_id_dic = category_data.get(str(guild.id), {})
        channel_id_dic = channel_data.get(str(guild.id), {})

        for entry in TIER_ENTRIES:
            category_name = entry["category_name"]
            channel_name = entry["channel_name"]
            primary_role_name = entry["role_name"]
            allowed_role_names = entry.get("view_role_names") or [primary_role_name]

            overwrites: dict[Any, discord.PermissionOverwrite] = {}
            overwrites[everyone_role] = discord.PermissionOverwrite(view_channel=False)

            for role_name in allowed_role_names:
                if role_name in {"@everyone", "everyone", "*"}:
                    role_obj = everyone_role
                else:
                    role_obj = tier_role_dic.get(role_name)
                if role_obj is None:
                    role_obj = discord.utils.get(guild.roles, name=role_name)
                    if role_obj:
                        tier_role_dic[role_name] = role_obj
                if role_obj is None:
                    continue
                overwrites[role_obj] = discord.PermissionOverwrite(view_channel=True)

    category = None
    if category_name and category_name.lower() not in {"none", "", "null"}:
        category = await ensure_category_exists(
            guild,
            category_name,
            overwrites,
        )
        category_id_dic[primary_role_name] = category.id

    channel = await ensure_text_channel_exists(
        guild=guild,
        channel_name=channel_name,
        overwrites=overwrites,
        category=category,
    )
    channel_id_dic[primary_role_name] = channel.id
    entry["channel_id"] = channel.id

    category_data[str(guild.id)] = category_id_dic
    channel_data[str(guild.id)] = channel_id_dic

    save_subscription_categories(category_data)
    save_channel_ids(channel_data)
    save_subscription_config(SUBSCRIPTION_CONFIG)


def start_django_admin():
    """Run Django admin panel (webadmin) on 127.0.0.1:8001 for debugging.

    Starts a child process for `python webadmin/manage.py runserver` and
    registers an atexit hook to terminate it when this process exits.
    Controlled via env RUN_DJANGO (truthy to enable).
    """
    try:

        # PROJECT_ROOT points to .../bot, repo root is parent
        repo_root = os.path.abspath(os.path.join(PROJECT_ROOT, ".."))
        manage_py = os.path.join(repo_root, "webadmin", "manage.py")
        if not os.path.exists(manage_py):
            debug_print(f"[Django] manage.py not found at {manage_py}; skip starting.")
            return

        env = os.environ.copy()
        env.setdefault("BOT_ADMIN_API_BASE", "http://127.0.0.1:8000")
        # ADMIN_API_TOKEN は必要に応じてこのプロセスの環境から継承してください

        args = [sys.executable, manage_py, "runserver", "127.0.0.1:8001"]
        proc = subprocess.Popen(args, cwd=os.path.dirname(manage_py), env=env)
        debug_print("[Django] runserver started on http://127.0.0.1:8001")

        def _stop():
            try:
                if proc.poll() is None:
                    proc.terminate()
            except Exception:
                try:
                    if proc.poll() is None:
                        proc.kill()
                except Exception:
                    pass

        atexit.register(_stop)
    except Exception as e:
        debug_print(f"[Django] failed to start: {e!r}")


# ---- メッセージ中のプレースホルダ検証 ----
ALLOWED_PLACEHOLDERS = {"user"}
PLACEHOLDER_RE = re.compile(r"(?<!\{)\{([^\{\}]+)\}(?!\})")


def _unknown_placeholders(msg: str | None) -> list[str]:
    text = msg or ""
    unknown: list[str] = []
    for m in PLACEHOLDER_RE.finditer(text):
        key = (m.group(1) or "").strip().lower()
        if key not in ALLOWED_PLACEHOLDERS:
            unknown.append(m.group(1).strip())
    # unique, keep readable order (by first occurrence)
    seen = set()
    uniq = []
    for u in unknown:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


# RUN_DJANGO=1 なら Django 管理画面を並行起動（デバッグ用）
if os.getenv("RUN_DJANGO", "").strip() not in ("", "0", "false", "False"):
    start_django_admin()

if __name__ == "__main__":
    # FastAPI を別スレッドで開始（独自ループ）
    threading.Thread(target=start_api, daemon=True).start()

    # Discord Bot はメインスレッドで実行（bot.loop が基準になる）
    try:
        asyncio.run(run_discord_bot())
    except KeyboardInterrupt:
        debug_print("[shutdown] KeyboardInterrupt received")
    except asyncio.CancelledError:
        debug_print("[shutdown] asyncio tasks cancelled")
