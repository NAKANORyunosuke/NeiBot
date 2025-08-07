import discord
from discord.ext import commands
import json
import asyncio

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

bot_loop = None


async def run_discord_bot():
    with open("./venv/token.json", "r", encoding="utf-8") as f:
        token = json.load(f)["discord_token"]
    bot.load_extension("bot.cogs.link")
    bot.load_extension("bot.cogs.unlink")
    await bot.start(token)


async def send_message_to_channel(channel_id: int, message: str):
    channel = bot.get_channel(channel_id)
    if channel:
        await channel.send(message)
    else:
        print("チャンネルが見つかりません")


@bot.event
async def on_ready():
    global bot_loop
    bot_loop = asyncio.get_running_loop()
    print(f"{bot.user} が起動しました。")


@bot.event
async def on_message(message):
    if message.author.bot:
        return
    await message.channel.send(message.content)


# 今後の参考用: メンバー参加時にTwitch Tierを確認してロール付与
# @bot.event
# async def on_member_join(member: discord.Member):
#     twitch_user_id = linked_accounts.get(member.id)
#     if not twitch_user_id:
#         print(f"{member.name} はTwitch連携されていません")
#         return

#     broadcaster_id = "<あなたのTwitchユーザーID>"
#     tier = get_twitch_tier(twitch_user_id, broadcaster_id)
#     if not tier:
#         print(f"{member.name} のTier情報を取得できませんでした")
#         return

#     role_map = {
#         "1000": "Tier1",
#         "2000": "Tier2",
#         "3000": "Tier3"
#     }

#     role_name = role_map.get(tier)
#     if role_name:
#         role = discord.utils.get(member.guild.roles, name=role_name)
#         if role:
#             await member.add_roles(role)
#             print(f"{member.name} にロール {role_name} を付与しました")
