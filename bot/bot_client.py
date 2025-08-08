import asyncio
import json
import threading
from typing import Coroutine, Any  # ★ 追加
import zoneinfo
import datetime
from bot.utils.twitch import load_linked_users, save_linked_users, get_auth_url

import discord
from discord.ext import commands, tasks
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import uvicorn
import httpx  # ★ requests → httpx に置換
# import requests  # ★ もう使わない
from bot.utils.twitch import (
    get_twitch_keys,
    get_user_info_and_subscription,
    save_linked_user,
    get_broadcast_id,
)

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
@app.get("/twitch_callback")
async def twitch_callback(request: Request):
    print("✅ [twitch_callback] にアクセスがありました")
    code = request.query_params.get("code")
    state = request.query_params.get("state")  # DiscordのユーザーID（str）

    if not code or not state:
        return PlainTextResponse("Missing code or state", status_code=400)

    # 1) Twitch クレデンシャル
    client_id, client_secret, redirect_uri = get_twitch_keys()

    # 2) アクセストークン取得（★ 非同期 httpx に置換）
    token_url = "https://id.twitch.tv/oauth2/token"
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(token_url, data=payload, headers=headers)

    if resp.status_code != 200:
        return PlainTextResponse(f"Failed to get token: {resp.text}", status_code=502)

    access_token = resp.json().get("access_token")
    if not access_token:
        return PlainTextResponse("Access token not found", status_code=502)

    # 3) broadcaster_id を解決（★ await が必要）
    val = get_broadcast_id()
    print(f"[DEBUG] get_broadcast_id -> {val!r} ({type(val)})")
    BROADCASTER_ID = str(get_broadcast_id())

    # 4) ユーザー情報 & サブスク情報（★ await）
    twitch_user_name, twitch_user_id, tier, streak = await get_user_info_and_subscription(
        viewer_access_token=access_token,
        client_id=client_id,
        broadcaster_id=BROADCASTER_ID,
    )

    if not twitch_user_name:
        return PlainTextResponse("Failed to get Twitch user info", status_code=502)

    # 5) リンク情報を保存（同期I/OでもOK。重い場合は to_thread 化）
    save_linked_user(state, twitch_user_name, tier, streak)

    # 6) Discord通知は Bot ループへ投げる
    try:
        print("notify_discord_userの呼び出し")
        run_in_bot_loop(
            notify_discord_user(int(state), twitch_user_name, tier, streak)
        )
    except Exception as e:
        print("❌ failed to schedule notify:", repr(e))

    return PlainTextResponse("Notified in background", status_code=200)


@tasks.loop(time=datetime.time(hour=0, minute=5, tzinfo=JST))
async def monthly_relink_sweeper():
    """毎日0:05(JST)に起動。1日だけ全員に再リンクを促す＆データ削除。"""
    await bot.wait_until_ready()
    today = datetime.datetime.now(JST).date()
    if today.day != 1:
        return  # 月初のみ実行

    data = load_linked_users()
    if not data:
        print("ℹ linked_users.json が空/未作成: スキップ")
        return

    # 抽出: is_subscriber が True のユーザー
    targets = []
    for discord_id, info in list(data.items()):
        if info.get("is_subscriber") is True:
            targets.append(discord_id)
            # データ削除（完全削除）
            data.pop(discord_id, None)

    if not targets:
        print("ℹ 月初の再リンク対象なし")
        return

    # 先に保存してからDM送信（クラッシュしても削除は確定させる）
    save_linked_users(data)
    print(f"🧹 月初再リンク: {len(targets)} 件を削除しました")

    # DM送信（レート制限に優しく、少し間隔を空ける）
    for did in targets:
        try:
            user = await bot.fetch_user(int(did))
            auth_url = get_auth_url(str(did))  # state=discord_id で再リンク
            msg = (
                "📅 毎月初めの再認証のお知らせ\n"
                "サブスク状況の確認のため、もう一度リンクをお願いします。\n"
                f"{auth_url}\n\n"
                "※ リンク後は自動でロールが同期されます。"
            )
            await user.send(msg)
            await asyncio.sleep(0.5)  # 送信間隔（調整可）
        except Exception as e:
            print(f"❌ DM送信失敗 {did}: {e!r}")


@monthly_relink_sweeper.before_loop
async def _before_monthly_relink_sweeper():
    await bot.wait_until_ready()
    print("⏰ monthly_relink_sweeper scheduled (JST 00:05)")


# ===== FastAPI を別スレッドで起動 =====
def start_api():
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")


# ===== Discord Bot を起動 =====
async def run_discord_bot():
    with open("./venv/token.json", "r", encoding="utf-8") as f:
        token = json.load(f)["discord_token"]

    bot.load_extension("bot.cogs.link")
    bot.load_extension("bot.cogs.unlink")

    await bot.start(token)


@bot.event
async def on_ready():
    if not monthly_relink_sweeper.is_running():
        monthly_relink_sweeper.start()
    # すでに daily_tier_sync を start しているならそれはそれでそのまま
    print("✅ monthly_relink_sweeper started")


if __name__ == "__main__":
    # FastAPI を別スレッドで開始（独自ループ）
    threading.Thread(target=start_api, daemon=True).start()

    # Discord Bot はメインスレッドで実行（bot.loop が基準になる）
    asyncio.run(run_discord_bot())
