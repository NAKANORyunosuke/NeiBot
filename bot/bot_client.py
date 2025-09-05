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
from bot.common import debug_print
from bot.utils.streak import reconcile_and_save_link
from bot.utils.save_and_load import (
    get_broadcast_id,
    get_twitch_keys,
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
from bot.utils.save_and_load import load_users, save_linked_users, get_eventsub_config
import hmac
import hashlib
import datetime as dt

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

# ===== FastAPI アプリ =====
app = FastAPI()


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


# ---- API: 直接Discordに通知する（外部/内部から叩ける）----
@app.post("/notify_link")
async def notify_link(discord_id: int, twitch_name: str, tier: str):
    run_in_bot_loop(notify_discord_user(discord_id, twitch_name, tier))
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
        run_in_bot_loop(
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
        users = load_users()

        # Map twitch user -> discord ids
        t_user_id = event.get("user_id") or event.get("user") or event.get("user_login")
        dids = _find_discord_ids_by_twitch_id(str(t_user_id)) if t_user_id else []
        if not dids:
            # 未リンク（Discord不明）はスキップ
            return JSONResponse({"status": "ok", "matched": 0})

        now = dt.datetime.now(JST).date()

        for did in dids:
            info = users.get(did, {}) if isinstance(users.get(did, {}), dict) else {}

            if sub_type == "channel.subscribe":
                info["is_subscriber"] = True
                if event.get("tier"):
                    info["tier"] = event.get("tier")
                # 初回開始日の候補（ヘッダのタイムスタンプの日付）
                if not info.get("subscribed_since"):
                    ts = twitch_msg_ts[:10] if twitch_msg_ts else now.isoformat()
                    info["subscribed_since"] = ts
                info["last_verified_at"] = now

            elif sub_type == "channel.subscription.message":
                # 再サブメッセージに cumulative/streak が含まれる
                cum = event.get("cumulative_months")
                if isinstance(cum, int) and cum >= 0:
                    info["cumulative_months"] = cum
                # streak_months は int または {"months": int} の場合がある
                streak_val = event.get("streak_months")
                if isinstance(streak_val, dict):
                    sm = streak_val.get("months")
                    if isinstance(sm, int) and sm >= 0:
                        info["streak_months"] = sm
                elif isinstance(streak_val, int) and streak_val >= 0:
                    info["streak_months"] = streak_val

                if event.get("tier"):
                    info["tier"] = event.get("tier")
                info["is_subscriber"] = True
                info["last_verified_at"] = now

            elif sub_type == "channel.subscription.end":
                info["is_subscriber"] = False
                info["last_verified_at"] = now

            users[str(did)] = info

        save_linked_users(users)
        return JSONResponse({"status": "ok", "matched": len(dids)})

    if twitch_msg_type == "revocation":
        debug_print("[EventSub] revoked:", data)
        return JSONResponse({"status": "revoked"})

    return PlainTextResponse("ignored", status_code=200)


# ===== FastAPI を別スレッドで起動 =====
def start_api():
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")


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


if __name__ == "__main__":
    # FastAPI を別スレッドで開始（独自ループ）
    threading.Thread(target=start_api, daemon=True).start()

    # Discord Bot はメインスレッドで実行（bot.loop が基準になる）
    asyncio.run(run_discord_bot())
