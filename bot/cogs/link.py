from discord.ext import commands
import discord
import asyncio
from bot.utils.twitch import get_auth_url, load_linked_users

TIER_ROLE_MAP = {
    "1000": "Subscription Tier1",
    "2000": "Subscription Tier2",
    "3000": "Subscription Tier3",
}


# bot/cogs/link.py
from __future__ import annotations

import asyncio
from typing import Optional

import discord
from discord.ext import commands
from bot.utils.twitch import get_auth_url, load_linked_users

# ==== ロールID（あなたのサーバ設定） ====
ROLE_TWITCH_LINKED = 1403053988991205509  # Twitch-linked
ROLE_TIER1 = 1402175000252776491          # Subscription Tier1
ROLE_TIER2 = 1402175113092136990          # Subscription Tier2
ROLE_TIER3 = 1402175191802576926          # Subscription Tier3

TIER_ROLE_MAP = {
    "1000": ROLE_TIER1,
    "2000": ROLE_TIER2,
    "3000": ROLE_TIER3,
}
ALL_TIER_ROLE_IDS = {ROLE_TIER1, ROLE_TIER2, ROLE_TIER3}


class LinkCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _ensure_roles_for_member(
        self,
        member: discord.Member,
        tier: Optional[str],
    ) -> None:
        """Twitch-linked を必ず付与。Tier は該当だけ付与し他Tierは除去。"""
        # 取得（存在しないロールIDは None になる可能性があるのでフィルタ）
        guild = member.guild
        linked_role = guild.get_role(ROLE_TWITCH_LINKED)
        tier_role_to_add = guild.get_role(TIER_ROLE_MAP.get(tier)) if tier else None

        # 付与対象ロール
        roles_to_add = [r for r in (linked_role, tier_role_to_add) if r and r not in member.roles]

        # 除去対象（他Tierロール）
        current_role_ids = {r.id for r in member.roles}
        tier_roles_to_remove = [guild.get_role(rid) for rid in ALL_TIER_ROLE_IDS if rid in current_role_ids]
        tier_roles_to_remove = [r for r in tier_roles_to_remove if r and (tier_role_to_add is None or r.id != tier_role_to_add.id)]

        # 実行（権限・階層に注意）
        if roles_to_add:
            await member.add_roles(*roles_to_add, reason="Twitch link: add roles")
        if tier_roles_to_remove:
            await member.remove_roles(*tier_roles_to_remove, reason="Twitch link: remove old tier")

    @discord.slash_command(
        name="link",
        description="あなたのDiscordアカウントとTwitchアカウントをリンクします",
    )
    async def link(self, ctx: discord.ApplicationContext):
        # スラッシュコマンドは必ず応答（ephemeral）
        discord_id = str(ctx.author.id)
        auth_url = get_auth_url(discord_id)
        await ctx.respond(f"🔗 以下のリンクからTwitchと連携してください：\n{auth_url}", ephemeral=True)

        # サーバ外で実行された場合、ロール付与はできないのでDMのみ
        if ctx.guild is None:
            await ctx.author.send("⚠ このコマンドはサーバー内で実行してください。連携は可能ですがロール付与はできません。")
            return

        # ⏳ 連携完了を10秒ごとに最大60秒間ポーリング
        for _ in range(6):  # 10s x 6 = 60s
            await asyncio.sleep(10)
            linked_users = load_linked_users()
            if discord_id not in linked_users:
                continue

            info = linked_users[discord_id]
            twitch_name = info.get("twitch_username")
            is_sub = info.get("is_subscriber", False)
            tier = info.get("tier")  # "1000"/"2000"/"3000" or None

            # ロール付与（ギルド&メンバー解決）
            try:
                member = ctx.guild.get_member(ctx.author.id) or await ctx.guild.fetch_member(ctx.author.id)
            except discord.NotFound:
                member = None

            if member is not None:
                try:
                    await self._ensure_roles_for_member(member, tier)
                except discord.Forbidden:
                    await ctx.author.send("⚠ Botにロール管理権限が不足しているため、ロール付与に失敗しました。管理者に連絡してください。")
                except Exception as e:
                    await ctx.author.send(f"⚠ ロール付与中にエラーが発生しました: {e!r}")

            # DM通知（Tier番号の見やすい表記）
            tier_msg = "0"
            if tier == "1000":
                tier_msg = "1"
            elif tier == "2000":
                tier_msg = "2"
            elif tier == "3000":
                tier_msg = "3"

            msg = (
                "✅ Twitch連携が完了しました！\n"
                f"・Twitch名: **{twitch_name}**\n"
                f"・サブスク状態: {'✅ 登録中' if is_sub else '❌ 未登録'}\n"
                f"・Tier: {tier_msg}\n"
                "※ ロールが反映されていない場合は、数秒待ってから再度ご確認ください。"
            )
            await ctx.author.send(msg)
            return

        # タイムアウト
        await ctx.author.send("⏳ 60秒経っても連携が完了しませんでした。もう一度 `/link` をお試しください。")


def setup(bot: commands.Bot):
    bot.add_cog(LinkCog(bot))
