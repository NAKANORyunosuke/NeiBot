import asyncio
import json
import threading
from typing import Coroutine, Any
import zoneinfo
import discord
from discord.ext import commands
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import uvicorn
import httpx
import os
from bot.common import save_all_guild_members
from bot.utils.streak import reconcile_and_save_link
from bot.utils.twitch import (
    get_twitch_keys,
    get_user_info_and_subscription,
    get_broadcast_id,
)


# ==================== パス設定（絶対パス） ====================

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "./"))
TOKEN_PATH = os.path.join(PROJECT_ROOT, "venv", "token.json")
LINKED_USERS_FILE = os.path.join(PROJECT_ROOT, "venv", "linked_users.json")


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
            print("❌ notify error:", repr(e))
    fut.add_done_callback(_done)
    return fut


# ---- Bot側で実際に送信する処理（Botのループ上で動く）----
async def notify_discord_user(discord_id: int, twitch_name: str, tier: str, streak: int | None = None):
    await bot.wait_until_ready()
    user = await bot.fetch_user(discord_id)
    if not user:
        print(f"⚠ fetch_user({discord_id}) が None")
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

# 既存ユーティリティ想定:
# - get_twitch_keys() -> (client_id, client_secret, redirect_uri)
# - get_broadcast_id() -> broadcaster_id(str or int)
# - get_user_info_and_subscription(viewer_access_token, client_id, broadcaster_id) -> dict
#   返り値例:
#   {
#     "twitch_username": str,
#     "twitch_user_id": str,
#     "tier": "1000"|"2000"|"3000"|None,
#     "streak_months": int,
#     "cumulative_months": int,
#     "bits_rank": Optional[int],
#     "bits_score": int,
#     "is_subscriber": bool,
#   }
# - save_linked_user(...) は旧版(引数: discord_id, twitch_username, tier, streak) or
#                         新版(引数: discord_id, twitch_username, tier, streak_months, cumulative_months, bits_score, bits_rank)
# - run_in_bot_loop(coro) / notify_discord_user(user_id:int, name:str, tier, streak)

@app.get("/twitch_callback")
async def twitch_callback(request: Request):
    print("✅ [twitch_callback] にアクセスがありました")
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
        print(f"[DEBUG] get_broadcast_id -> {BROADCASTER_ID!r}")
    except Exception as e:
        return PlainTextResponse(f"Failed to resolve broadcaster_id: {e!r}", status_code=500)

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
        return PlainTextResponse(f"Failed to fetch user/sub info: {e!r}", status_code=500)

    twitch_user_name = info.get("twitch_username")
    if not twitch_user_name:
        return PlainTextResponse("Failed to get Twitch user info", status_code=502)

    # 5) リンク情報を保存（streak自前更新版）
    try:
        rec = reconcile_and_save_link(str(state), info)
    except Exception as e:
        print(f"❌ reconcile_and_save_link failed: {e!r}")
        rec = info  # 万一失敗したら元のinfoを使う

    # 6) Discord通知
    try:
        print("notify_discord_user の呼び出し")
        run_in_bot_loop(
            notify_discord_user(
                int(state),
                rec.get("twitch_username"),
                rec.get("tier"),
                rec.get("streak_months", 0),
            )
        )
    except Exception as e:
        print("❌ failed to schedule notify:", repr(e))

    return PlainTextResponse("Notified in background", status_code=200)


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

    await bot.start(token)


if __name__ == "__main__":
    # FastAPI を別スレッドで開始（独自ループ）
    threading.Thread(target=start_api, daemon=True).start()

    # Discord Bot はメインスレッドで実行（bot.loop が基準になる）
    asyncio.run(run_discord_bot())
