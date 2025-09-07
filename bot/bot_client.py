import asyncio
import json
import threading
from typing import Coroutine, Any
import zoneinfo
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
)
from bot.utils.twitch import (
    get_user_info_and_subscription,
    register_eventsub_subscriptions,
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
import datetime as dt
import io
import os

# ==================== パス設定（絶対パス） ====================

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "./"))
TOKEN_PATH = os.path.join(PROJECT_ROOT, "venv", "token.json")
USERS_FILE = os.path.join(PROJECT_ROOT, "venv", "all_users.json")

ROLE_NAMES_LIST = [
    "Subscription Tier1",
    "Subscription Tier2",
    "Subscription Tier3",
    "Twitch-linked",
]
CHANNEL_NAMES_LIST = [
    "tier-1",
    "tier-2",
    "tier-3",
]
CATEGORY_NAMES = [
    "サブスクTier 1",
    "サブスクTier 2",
    "サブスクTier 3",
]
CATEGORY_ROLE_MAP = {
    CATEGORY_NAMES[j]: ROLE_NAMES_LIST[j] for j in range(len(CATEGORY_NAMES))
}
CHANNEL_ROLE_MAP = {
    CHANNEL_NAMES_LIST[j]: ROLE_NAMES_LIST[j] for j in range(len(CATEGORY_NAMES))
}
CATEGORY_CHANNEL_MAP = {
    CATEGORY_NAMES[j]: CHANNEL_NAMES_LIST[j] for j in range(len(CATEGORY_NAMES))
}
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
    file_url: str | None = None,
    file_path: str | None = None,
):
    try:
        debug_print(
            f"[DM] start -> user={getattr(user, 'id', '?')} has_url={bool(file_url)} has_path={bool(file_path)} msg_len={(len(message or ''))}"
        )
        buf = None
        filename = None
        # Prefer local filesystem if provided (same host)
        if file_path:
            try:
                if os.path.isfile(file_path):
                    debug_print(f"[DM] reading local attachment: {file_path}")
                    with open(file_path, "rb") as fp:
                        data = fp.read()
                    buf = io.BytesIO(data)
                    filename = os.path.basename(file_path) or "attachment"
                else:
                    debug_print(f"[DM] local attachment not found: {file_path}")
            except Exception as e:
                debug_print(f"[DM] failed reading local file: {e!r}")
        # Fallback to HTTP(S) download
        if buf is None and file_url:
            async with httpx.AsyncClient(timeout=20) as client:
                debug_print(f"[DM] downloading attachment: {file_url}")
                r = await client.get(file_url)
                r.raise_for_status()
                buf = io.BytesIO(r.content)
                debug_print(
                    f"[DM] downloaded: status={r.status_code} bytes={len(r.content)}"
                )
            filename = filename or file_url.rsplit("/", 1)[-1] or "attachment"

        if buf is not None:
            await user.send(content=(message or None), file=discord.File(buf, filename))
            debug_print(
                f"[DM] sent with file -> user={getattr(user, 'id', '?')} {filename}"
            )
        else:
            await user.send(content=message)
            debug_print(f"[DM] sent text -> user={getattr(user, 'id', '?')}")
    except Exception as e:
        debug_print(f"[DM] failed to {getattr(user, 'id', '?')}: {e!r}")


async def notify_role_members(
    role_id: int,
    message: str,
    file_url: str | None = None,
    file_path: str | None = None,
    guild_id: int | None = None,
):
    await bot.wait_until_ready()
    debug_print(
        f"[/send_role_dm] begin notify_role_members role_id={role_id} guild_id={guild_id} msg_len={(len(message or ''))} has_file={bool(file_url)}"
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
    debug_print(
        f"[DM] target guild={guild.id}({guild.name}) role={role.id}({role.name}) members={len(members)}"
    )
    for m in members:
        if m.bot:
            continue
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
        await _send_dm(m, dm_text, file_url, file_path)
        await asyncio.sleep(0.3)
    debug_print(
        f"[/send_role_dm] done for role_id={role_id} guild_id={getattr(guild, 'id', None)}"
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
    guild_id = payload.get("guild_id")
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
    debug_print(
        f"[/send_role_dm] payload role_id={role_id} guild_id={guild_id} msg_len={(len(message or ''))} has_file={bool(file_url)}"
    )
    schedule_in_bot_loop(
        notify_role_members(
            role_id,
            message,
            file_url,
            file_path,
            int(guild_id) if guild_id else None,
        )
    )
    debug_print("[/send_role_dm] queued notify task")
    return {"status": "queued"}


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


@app.post("/twitch_eventsub")
async def twitch_eventsub(
    request: Request,
    twitch_msg_id: str = Header(None, alias="Twitch-Eventsub-Message-Id"),
    twitch_msg_type: str = Header(None, alias="Twitch-Eventsub-Message-Type"),
    twitch_msg_ts: str = Header(None, alias="Twitch-Eventsub-Message-Timestamp"),
    twitch_signature: str = Header(None, alias="Twitch-Eventsub-Message-Signature"),
):
    body = await request.body()
    try:
        data = await request.json()
    except Exception:
        return PlainTextResponse("invalid json", status_code=400)

    # webhook verification
    if twitch_msg_type == "webhook_callback_verification":
        challenge = data.get("challenge")
        debug_print("[EventSub] verification")
        return PlainTextResponse(challenge or "", status_code=200)

    # notification / revocation must verify signature
    try:
        _, secret = get_eventsub_config()
    except Exception as e:
        return PlainTextResponse(f"EventSub secret missing: {e}", status_code=500)

    if not (twitch_msg_id and twitch_msg_ts and twitch_signature):
        return PlainTextResponse("missing headers", status_code=400)

    if not _verify_signature(
        secret, twitch_msg_id, twitch_msg_ts, body, twitch_signature
    ):
        return PlainTextResponse("invalid signature", status_code=403)

    if twitch_msg_type == "notification":
        sub_type = (data.get("subscription") or {}).get("type")
        event = data.get("event") or {}
        debug_print(f"[EventSub] notify: {sub_type}")

        # Persist to inbox (best-effort)
        try:
            inbox_enqueue_event(
                source="twitch",
                delivery_id=str(twitch_msg_id),
                event_type=str(sub_type or ""),
                twitch_user_id=str(
                    event.get("user_id") or event.get("user") or event.get("user_login") or ""
                ) or None,
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

        # Apply immediately
        try:
            matched = apply_event_to_linked_users(sub_type, event, twitch_msg_ts)
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
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


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
        role_id_dic = role_data.get(guild.id, {})

        for role_name in ROLE_NAMES_LIST:
            role = await ensure_role_exists(guild, role_name)
            role_id_dic[role.name] = role.id
        role_data[guild.id] = role_id_dic
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
        tier_role_dic = {
            role_name: discord.utils.get(guild.roles, name=role_name)
            for role_name in ROLE_NAMES_LIST
        }
        category_id_dic = category_data.get(guild.id, {})
        channel_id_dic = channel_data.get(guild.id, {})

        for category_name in CATEGORY_NAMES:
            overwrites = {}
            overwrites[everyone_role] = discord.PermissionOverwrite(view_channel=False)
            for key, value in tier_role_dic.items():
                subscriber_role = value
                if re.search("3", key):
                    # debug_print("DEBUG: ", key, category_name)
                    overwrites[subscriber_role] = discord.PermissionOverwrite(
                        view_channel=True
                    )

                if re.search("2", key) and re.search(r"[1,2]", category_name):
                    # debug_print("DEBUG: ", key, category_name)
                    overwrites[subscriber_role] = discord.PermissionOverwrite(
                        view_channel=True
                    )

                if re.search("1", key) and re.search(r"1", category_name):
                    # debug_print("DEBUG: ", key, category_name)
                    overwrites[subscriber_role] = discord.PermissionOverwrite(
                        view_channel=True
                    )
            # debug_print("DEBUG: ", overwrites)
            category = await ensure_category_exists(guild, category_name, overwrites)
            category_id_dic[CATEGORY_ROLE_MAP[category.name]] = category.id

            channel = await ensure_text_channel_exists(
                guild=guild,
                channel_name=CATEGORY_CHANNEL_MAP[category.name],
                overwrites=overwrites,
                category=category,
            )
            channel_id_dic[CATEGORY_ROLE_MAP[category.name]] = channel.id

        category_data[str(guild.id)] = category_id_dic
        channel_data[str(guild.id)] = channel_id_dic
    save_subscription_categories(category_data)
    save_channel_ids(channel_data)


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
    asyncio.run(run_discord_bot())
