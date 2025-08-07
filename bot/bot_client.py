import asyncio
import json
import threading

import discord
from discord.ext import commands
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
import uvicorn
import requests

from bot.utils.twitch import get_twitch_keys, get_user_info_and_subscription, save_linked_user

# Discord Bot の準備
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# FastAPI アプリの準備
app = FastAPI()


@app.post("/notify_link")
async def notify_link(discord_id: int, twitch_name: str, tier: str):
    user = await bot.fetch_user(discord_id)
    if user:
        await user.send(f"✅ Twitch `{twitch_name}` とリンクしました！Tier: {tier}")
    return {"status": "ok"}


@app.get("/twitch_callback")
async def twitch_callback(request: Request):
    print("✅ [twitch_callback] にアクセスがありました")
    code = request.query_params.get("code")
    state = request.query_params.get("state")  # DiscordのユーザーID（str）

    if not code or not state:
        return {"error": "Missing code or state"}

    # 1. Twitchクレデンシャルを取得
    client_id, client_secret, redirect_uri = get_twitch_keys()

    # 2. アクセストークンを取得
    token_url = "https://id.twitch.tv/oauth2/token"
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    response = requests.post(token_url, data=payload, headers=headers)
    if response.status_code != 200:
        return {"error": "Failed to get token", "detail": response.text}

    access_token = response.json().get("access_token")
    if not access_token:
        return {"error": "Access token not found"}

    # 3. ユーザー情報とサブスク情報を取得
    twitch_user_name, twitch_user_id, tier, streak = get_user_info_and_subscription(access_token, client_id)
    if not twitch_user_name:
        return {"error": "Failed to get Twitch user info"}

    # 4. ユーザー情報を保存
    save_linked_user(state, twitch_user_name, tier, streak)

    # 5. Discordに通知（非同期に実行）
    async def notify():
        user = await bot.fetch_user(int(state))
        if user:
            await user.send(f"✅ Twitch `{twitch_user_name}` とリンクしました！Tier: {tier}, Streak: {streak}")
    await notify()

    return RedirectResponse(url="https://discord.com")


# FastAPIを別スレッドで起動
def start_api():
    uvicorn.run(app, host="0.0.0.0", port=8000)


# Discord Botを起動
async def run_discord_bot():
    with open("./venv/token.json", "r", encoding="utf-8") as f:
        token = json.load(f)["discord_token"]

    bot.load_extension("bot.cogs.link")
    bot.load_extension("bot.cogs.unlink")
    await bot.start(token)

if __name__ == "__main__":
    threading.Thread(target=start_api, daemon=True).start()
    asyncio.run(run_discord_bot())