import discord
from discord.ext import commands
from fastapi import FastAPI
import uvicorn
import threading
import asyncio
import json

# ① Botの初期化
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ② APIサーバ用FastAPIインスタンス
app = FastAPI()


# ③ チャンネルにメッセージを送る関数（Django側からも使えるように）
async def send_message_to_channel(channel_id: int, message: str):
    channel = bot.get_channel(channel_id)
    if channel:
        await channel.send(message)
    else:
        print(f"❌ チャンネルが見つかりません: {channel_id}")


# ④ Bot起動イベント
@bot.event
async def on_ready():
    print(f"✅ {bot.user} が起動しました。")


# ⑤ メッセージエコー機能（例）
@bot.event
async def on_message(message):
    if message.author.bot:
        return
    await message.channel.send(message.content)


# ⑥ FastAPIのエンドポイント（外部から通知を受ける）
@app.post("/notify_link")
async def notify_link(discord_id: int, twitch_name: str, tier: str):
    try:
        user = await bot.fetch_user(discord_id)
        if user:
            await user.send(f"✅ Twitch `{twitch_name}` とリンクしました！Tier: {tier}")
            return {"status": "ok"}
        else:
            return {"status": "user_not_found"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


# ⑦ FastAPIのスレッド起動
def start_api():
    uvicorn.run(app, host="0.0.0.0", port=6000)


# ⑧ Bot起動関数（メイン）
async def run_discord_bot():
    with open("./venv/token.json", "r", encoding="utf-8") as f:
        token = json.load(f)["discord_token"]
    bot.load_extension("bot.cogs.link")
    bot.load_extension("bot.cogs.unlink")
    await bot.start(token)


# ⑨ メイン：APIとBotを同時起動
if __name__ == "__main__":
    threading.Thread(target=start_api, daemon=True).start()
    asyncio.run(run_discord_bot())
