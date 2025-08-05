import discord
from discord.ext import commands
import json
import os
from bot import common


LINKED_USERS_FILE = "./venv/linked_users.json"


with open("./venv/token.json", "r", encoding="utf-8") as f:
    GUILD_ID = common.get_taken_json()["guild_id"]


def load_linked_users():
    if not os.path.exists(LINKED_USERS_FILE):
        return {}
    with open(LINKED_USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_linked_users(data):
    os.makedirs(os.path.dirname(LINKED_USERS_FILE), exist_ok=True)
    with open(LINKED_USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


class Link(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.slash_command(
        name="link",
        description="Twitchユーザー名をあなたのアカウントにリンクします",
        guild_ids=[GUILD_ID]
    )
    async def link(self, ctx: discord.ApplicationContext, twitch_username: str):
        discord_id = str(ctx.author.id)
        linked_users = load_linked_users()
        linked_users[discord_id] = twitch_username
        save_linked_users(linked_users)
        await ctx.respond(f"Twitchユーザー名 `{twitch_username}` をリンクしました ✅", ephemeral=True)


def setup(bot):
    bot.add_cog(Link(bot))
