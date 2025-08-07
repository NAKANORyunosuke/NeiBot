# import discord
# from discord.ext import commands
# import json
# import os
# from bot import common
# from bot.utils import twitch

# LINKED_USERS_FILE = "./venv/linked_users.json"


# with open("./venv/token.json", "r", encoding="utf-8") as f:
#     GUILD_ID = common.get_taken_json()["guild_id"]


# def load_linked_users():
#     if not os.path.exists(LINKED_USERS_FILE):
#         return {}
#     with open(LINKED_USERS_FILE, "r", encoding="utf-8") as f:
#         return json.load(f)


# def save_linked_users(data):
#     os.makedirs(os.path.dirname(LINKED_USERS_FILE), exist_ok=True)
#     with open(LINKED_USERS_FILE, "w", encoding="utf-8") as f:
#         json.dump(data, f, indent=4, ensure_ascii=False)


# class Link(commands.Cog):
#     def __init__(self, bot):
#         self.bot = bot

#     @discord.slash_command(
#         name="link",
#         description="Twitchユーザー名をあなたのアカウントにリンクします",
#         guild_ids=[GUILD_ID]
#     )
#     async def link(self, ctx: discord.ApplicationContext):
#         user_id = ctx.author.id
#         state = str(user_id)  # CSRF対策にも使える
#         auth_url = twitch.get_auth_url(state)
#         await ctx.respond(f"Twitch認証ページ: {auth_url}", ephemeral=True)


# def setup(bot):
#     bot.add_cog(Link(bot))

# bot/cogs/link.py
from discord.ext import commands
import discord
import asyncio
from bot.utils.twitch import get_auth_url, load_linked_users


class LinkCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.slash_command(
        name="link",
        description="あなたのDiscordアカウントとTwitchアカウントをリンクします"
    )
    async def link(self, ctx: discord.ApplicationContext):
        discord_id = str(ctx.author.id)
        auth_url = get_auth_url(discord_id)

        await ctx.respond(f"🔗 以下のリンクからTwitchと連携してください：\n{auth_url}", ephemeral=True)

        # ⏳ 連携完了を10秒ごとに最大60秒間ポーリング
        for i in range(6):  # 最大 60秒（10秒 * 6回）
            await asyncio.sleep(10)
            linked_users = load_linked_users()

            if discord_id in linked_users:
                info = linked_users[discord_id]
                twitch_name = info.get("twitch_username", "不明")
                is_sub = info.get("is_subscriber", False)
                streak = info.get("streak", 0)

                msg = (
                    f"✅ Twitch連携が完了しました！\n"
                    f"・Twitch名: **{twitch_name}**\n"
                    f"・サブスク状態: {'✅ 登録中' if is_sub else '❌ 未登録'}\n"
                    f"・継続月数: {streak} ヶ月"
                )
                await ctx.author.send(msg)
                return

        # タイムアウト
        await ctx.author.send("⏳ 60秒経っても連携が完了しませんでした。もう一度 `/link` をお試しください。")


def setup(bot):
    bot.add_cog(LinkCog(bot))
