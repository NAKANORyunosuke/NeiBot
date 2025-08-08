# bot/bot_client.py
import asyncio
import json
import logging
import threading
from typing import Optional

import discord
from discord.ext import commands
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import uvicorn
import requests

# --- プロジェクト内ユーティリティ（あなたの最新版） ---
from bot.utils.twitch import (
    get_twitch_keys,
    get_user_info_and_subscription,
    save_linked_user,
)

# ===================== 基本セットアップ =====================

log = logging.getLogger("neibot")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

app = FastAPI()

# ===================== DM 送信コルーチン（Bot側） =====================

async def notify_discord_user(discord_id: int, twitch_name: str, tier: Optional[str], streak: Optional[int]):
    """Bot のイベントループ上で DM 送信する"""
    await bot.wait_until_ready()

    try:
        user = await bot.fetch_user(discord_id)
        if user is None:
            log.warning(f"[notify] fetch_user({discord_id}) -> None")
            return

        def tier_label(t: Optional[str]) -> str:
            if t in ("1000", "2000", "3000"):
                m = {"1000": "1", "2000": "2", "3000": "3"}[t]
                return f"Tier {m} ({t})"
            if t in (None, "none", "not_subscribed", "unknown"):
                return "なし"
            return str(t)

        is_sub = tier in ("1000", "2000", "3000")
        streak_txt = f"{streak} ヶ月" if isinstance(streak, int) else "取得なし"

        msg = (
            "✅ Twitch連携が完了しました！\n"
            f"・Twitch名: **{twitch_name}**\n"
            f"・サブスク状態: {'✅ 登録中' if is_sub else '❌ 未登録'}\n"
            f"・Tier: {tier_label(tier)}\n"
            f"・継続月数: {streak_txt}"
        )
        await user.send(msg)
        log.info(f"[notify] DM sent to {discord_id}")

    except discord.Forbidden:
        log.error(f"[notify] Forbidden: cannot DM {discord_id} (DM閉鎖の可能性)")
    except discord.HTTPException as e:
        log.exception(f"[notify] HTTPException while DM to {discord_id}: {e}")
    except Exception as e:
        log.exception(f"[notify] Unexpected error while DM to {discord_id}: {e}")

def run_in_bot_loop(coro):
    """FastAPI 側から Bot ループへ投げる（例外はログに出す）"""
    fut = asyncio.run_coroutine_threadsafe(coro, bot.loop)
    def _cb(f):
        try:
            f.result()
        except Exception as e:
            log.exception(f"❌ notify error: {e}")
    fut.add_done_callback(_cb)
    return fut

# ===================== FastAPI ルート =====================

@app.get("/healthz")
async def healthz():
    return PlainTextResponse("ok")

@app.post("/notify_link")
async def notify_link(discord_id: int, twitch_name: str, tier: str = "unknown", streak: int | None = None):
    """外部/内部テスト用：手動でDMを投げる"""
    run_in_bot_loop(notify_discord_user(discord_id, twitch_name, tier, streak))
    return {"status": "queued"}

@app.get("/twitch_callback")
async def twitch_callback(request: Request):
    """
    Twitch OAuth リダイレクト受け口（https://YOURDOMAIN/twitch_callback に統一）
    - authorize で取得した code は 視聴者のアクセストークン に交換される
    - サブスク判定は 配信者トークン（channel:read:subscriptions）で行う
    """
    log.info("✅ [twitch_callback] accessed")
    code = request.query_params.get("code")
    state = request.query_params.get("state")  # DiscordのユーザーID（str）

    if not code or not state:
        return PlainTextResponse("Missing code or state", status_code=400)

    # 1) キー類
    client_id, client_secret, redirect_uri = get_twitch_keys()
    log.info(f"[twitch_callback] redirect_uri: {redirect_uri}")

    # 2) 視聴者アクセストークンを取得（authorization_code）
    token_url = "https://id.twitch.tv/oauth2/token"
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri
    }
    try:
        resp = requests.post(token_url, data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=20)
    except requests.RequestException as e:
        log.exception(f"[twitch_callback] token exchange error: {e}")
        return PlainTextResponse("Token request failed", status_code=502)

    if resp.status_code != 200:
        log.error(f"[twitch_callback] token resp {resp.status_code}: {resp.text}")
        return PlainTextResponse("Failed to get token", status_code=resp.status_code)

    viewer_token = resp.json().get("access_token")
    if not viewer_token:
        return PlainTextResponse("Access token not found", status_code=502)

    # 3) 配信者トークン（channel:read:subscriptions 付き）を token.json から読む
    #    ※ない場合は tier 判定ができないので unknown 扱いにする（ログを出す）
    try:
        with open("./venv/token.json", "r", encoding="utf-8") as f:
            token_conf = json.load(f)
        broadcaster_token = token_conf.get("twitch_broadcaster_access_token")
        if not broadcaster_token:
            log.warning("[twitch_callback] broadcaster token missing -> tier 判定は unknown になります")
    except Exception as e:
        log.exception(f"[twitch_callback] failed to read broadcaster token: {e}")
        broadcaster_token = None

    # 4) サブスク判定（viewer_id, viewer_login, tier, streak を取得）
    try:
        if broadcaster_token:
            twitch_user_name, twitch_user_id, tier, streak = get_user_info_and_subscription(
                broadcaster_token, client_id, viewer_token
            )
        else:
            # 配信者トークンが無いと Helix で tier 判定不可。ユーザー名だけ viewer_token で取得する簡易版。
            headers_viewer = {"Authorization": f"Bearer {viewer_token}", "Client-Id": client_id}
            r_user = requests.get("https://api.twitch.tv/helix/users", headers=headers_viewer, timeout=15)
            r_user.raise_for_status()
            data = r_user.json()["data"][0]
            twitch_user_name = data["login"]
            twitch_user_id = data["id"]
            tier, streak = "unknown", None
    except requests.HTTPException as e:
        log.exception(f"[twitch_callback] helix error: {e}")
        return PlainTextResponse("Helix error", status_code=502)
    except Exception as e:
        log.exception(f"[twitch_callback] helix unexpected: {e}")
        return PlainTextResponse("Helix unexpected error", status_code=502)

    # 5) 保存（tier を JSON に確実に入れる）
    save_linked_user({
        "discord_id": state,
        "twitch_username": twitch_user_name,
        "tier": tier,
        "streak": streak
    })
    log.info(f"[twitch_callback] saved: discord_id={state}, twitch={twitch_user_name}, tier={tier}, streak={streak}")

    # 6) Discord へ DM（Bot ループへ投げる）
    try:
        run_in_bot_loop(notify_discord_user(int(state), twitch_user_name, tier, streak))
    except Exception as e:
        log.exception(f"[twitch_callback] failed to schedule notify: {e}")

    # 7) 呼び出し元へ即応答
    return PlainTextResponse("Notified in background", status_code=200)

# ===================== 起動まわり =====================

def start_api():
    # 前段に IIS/NGINX のリバースプロキシ (443/https) を想定
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info", proxy_headers=True)

async def run_discord_bot():
    with open("./venv/token.json", "r", encoding="utf-8") as f:
        token = json.load(f)["discord_token"]

    # 必要な Cog をロード（存在しなければコメントアウト）
    try:
        bot.load_extension("bot.cogs.link")
        bot.load_extension("bot.cogs.unlink")
    except Exception as e:
        log.warning(f"[bot] failed to load cogs: {e}")

    await bot.start(token)

if __name__ == "__main__":
    # FastAPI を別スレッドで起動
    threading.Thread(target=start_api, daemon=True).start()
    # Discord Bot はメインスレッドで起動（bot.loop が“主”になる）
    asyncio.run(run_discord_bot())
