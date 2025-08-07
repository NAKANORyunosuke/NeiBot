# bot/callback_handler.py
from discord import Client, Intents

intents = Intents.default()
client = Client(intents=intents)


@client.event
async def on_ready():
    print(f"Bot Ready: {client.user}")


async def notify_link_complete(discord_id: str, twitch_username: str):
    user = await client.fetch_user(int(discord_id))
    await user.send(f"✅ Twitch連携が完了しました！ようこそ **{twitch_username}** さん🎉")