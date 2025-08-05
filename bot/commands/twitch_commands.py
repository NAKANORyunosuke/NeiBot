import discord
from discord import app_commands
from discord.ext import commands
import json
import os

LINKED_USERS_FILE = "data/linked_users.json"


# JSONを読み込む
def load_linked_users():
    if not os.path.exists(LINKED_USERS_FILE):
        return {}
    with open(LINKED_USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# JSONに保存する
def save_linked_users(data):
    os.makedirs(os.path.dirname(LINKED_USERS_FILE), exist_ok=True)
    with open(LINKED_USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


class Link(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="link", description="Twitchユーザー名をあなたのアカウントにリンクします")
    @app_commands.describe(twitch_username="あなたのTwitchユーザー名")
    async def link(self, interaction: discord.Interaction, twitch_username: str):
        linked_users = load_linked_users()
        discord_id = str(interaction.user.id)

        linked_users[discord_id] = twitch_username
        save_linked_users(linked_users)

        await interaction.response.send_message(
            f"Twitchユーザー名 `{twitch_username}` をあなたのDiscordアカウントにリンクしました ✅", ephemeral=True
        )


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

    @app_commands.command(name="unlink", description="あなたのTwitchリンクを解除します")
    async def unlink(self, interaction: discord.Interaction):
        linked_users = load_linked_users()
        discord_id = str(interaction.user.id)

        if discord_id in linked_users:
            del linked_users[discord_id]
            save_linked_users(linked_users)
            await interaction.response.send_message("Twitchアカウントとのリンクを解除しました。", ephemeral=True)
        else:
            await interaction.response.send_message("あなたのアカウントはTwitchとリンクされていません。", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Link(bot))
    await bot.add_cog(Unlink(bot))