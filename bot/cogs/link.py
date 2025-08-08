# bot/cogs/link.py
from discord.ext import commands
import discord
import asyncio
from bot.utils.twitch import get_auth_url, load_linked_users

POLL_INTERVAL = 10
POLL_MAX_COUNT = 9


def _tier_label(tier: str | None) -> str:
    if tier in ("1000", "2000", "3000"):
        num = {"1000": "1", "2000": "2", "3000": "3"}[tier]
        return f"Tier {num} ({tier})"
    if tier in (None, "none", "not_subscribed", "unknown"):
        return "なし"
    return str(tier)


def _format_result(entry: dict) -> str:
    tname = entry.get("twitch_username", "unknown")
    tier = entry.get("tier")
    is_sub = tier in ("1000", "2000", "3000")
    streak = entry.get("streak")
    streak_txt = f"{streak} ヶ月" if isinstance(streak, int) else "取得なし"
    tier_txt = _tier_label(tier)

    return (
        "✅ Twitch連携が完了しました！\n"
        f"・Twitch名: **{tname}**\n"
        f"・サブスク状態: {'✅ 登録中' if is_sub else '❌ 未登録'}\n"
        f"・Tier: {tier_txt}\n"
        f"・継続月数: {streak_txt}"
    )


class LinkCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.slash_command(
        name="link",
        description="あなたのDiscordアカウントとTwitchアカウントをリンクします"
    )
    async def link(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        discord_id = str(ctx.author.id)
        auth_url = get_auth_url(discord_id)

        await ctx.followup.send(
            f"🔗 以下のリンクからTwitchと連携してください：\n{auth_url}\n"
            f"（自動で {POLL_INTERVAL * POLL_MAX_COUNT} 秒ほど進捗を確認します）",
            ephemeral=True
        )

        for _ in range(POLL_MAX_COUNT):
            await asyncio.sleep(POLL_INTERVAL)
            entry = load_linked_users().get(discord_id)
            if entry:
                msg = _format_result(entry)
                try:
                    await ctx.author.send(msg)
                    where = "DMでお送りしました。"
                except discord.Forbidden:
                    await ctx.followup.send(
                        "⚠️ DMが送れませんでした（相手の設定によりブロックされている可能性）。\n"
                        "この場で結果を表示します👇",
                        ephemeral=True
                    )
                    await ctx.followup.send(msg, ephemeral=True)
                    where = "このメッセージで表示しました。"

                await ctx.followup.send(f"✅ 連携を確認しました。結果は {where}", ephemeral=True)
                return

        await ctx.followup.send(
            f"⏳ {POLL_INTERVAL * POLL_MAX_COUNT} 秒待ちましたが連携を確認できませんでした。"
            " もう一度 `/link` をお試しください。",
            ephemeral=True
        )


def setup(bot):
    bot.add_cog(LinkCog(bot))
