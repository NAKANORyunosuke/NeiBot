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
                # streak = info.get("streak", 0)
                tier = info.get("is_subscriber", 0)

                msg = (
                    f"✅ Twitch連携が完了しました！\n"
                    f"・Twitch名: **{twitch_name}**\n"
                    f"・サブスク状態: {'✅ 登録中' if is_sub else '❌ 未登録'}\n"
                    # f"・継続月数: {streak} ヶ月"
                    f"・Tier: {int(tier/1000.0)}"
                )
                await ctx.author.send(msg)
                return

        # タイムアウト
        await ctx.author.send("⏳ 60秒経っても連携が完了しませんでした。もう一度 `/link` をお試しください。")


def setup(bot):
    bot.add_cog(LinkCog(bot))
