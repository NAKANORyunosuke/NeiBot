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


class Unlink(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.slash_command(
        name="unlink",
        description="あなたのTwitchリンクを解除します",
        guild_ids=[GUILD_ID]
    )
    async def unlink(self, ctx: discord.ApplicationContext):
        discord_id = str(ctx.author.id)
        linked_users = load_linked_users()

        if discord_id in linked_users:
            del linked_users[discord_id]
            save_linked_users(linked_users)
            await ctx.respond("Twitchアカウントとのリンクを解除しました ✅", ephemeral=True)
        else:
            await ctx.respond("あなたのアカウントはTwitchとリンクされていません。", ephemeral=True)


def setup(bot):
    bot.add_cog(Unlink(bot))
