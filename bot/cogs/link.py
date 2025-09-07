# bot/cogs/link.py
from __future__ import annotations

import asyncio
from typing import Optional, Dict, Any

import discord
from discord.ext import commands
from bot.utils.twitch import get_auth_url
from bot.monthly_relink_bot import mark_resolved

from bot.utils.save_and_load import (
    load_role_ids,
    load_users,
    patch_linked_user,
)


class LinkCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @staticmethod
    def _tier_role_map(role_conf: Dict[str, int]) -> Dict[str, Optional[int]]:
        return {
            "1000": role_conf.get("Subscription Tier1"),
            "2000": role_conf.get("Subscription Tier2"),
            "3000": role_conf.get("Subscription Tier3"),
        }

    @staticmethod
    def _all_tier_role_ids(role_conf: Dict[str, int]) -> set[int]:
        return {
            rid
            for key, rid in role_conf.items()
            if key.startswith("Subscription Tier") and isinstance(rid, int)
        }

    async def _ensure_roles_for_member(
        self,
        member: discord.Member,
        tier: Optional[str],
        role_conf: Dict[str, int],
    ) -> None:
        guild = member.guild

        linked_role_id = role_conf.get("Twitch-linked")
        tier_role_id = self._tier_role_map(role_conf).get(tier) if tier else None
        all_tier_ids = self._all_tier_role_ids(role_conf)

        linked_role = guild.get_role(linked_role_id) if linked_role_id else None
        tier_role_to_add = guild.get_role(tier_role_id) if tier_role_id else None

        roles_to_add = [
            r for r in (linked_role, tier_role_to_add) if r and r not in member.roles
        ]

        current_role_ids = {r.id for r in member.roles}
        tier_roles_to_remove = [
            guild.get_role(rid) for rid in all_tier_ids if rid in current_role_ids
        ]
        tier_roles_to_remove = [
            r
            for r in tier_roles_to_remove
            if r and (tier_role_to_add is None or r.id != tier_role_to_add.id)
        ]

        if roles_to_add:
            await member.add_roles(*roles_to_add, reason="Twitch link: add roles")
        if tier_roles_to_remove:
            await member.remove_roles(
                *tier_roles_to_remove, reason="Twitch link: remove old tier"
            )

    @discord.slash_command(
        name="link",
        description="あなたのDiscordアカウントとTwitchアカウントをリンクします",
    )
    async def link(self, ctx: discord.ApplicationContext):
        discord_id = str(ctx.author.id)
        auth_url = get_auth_url(discord_id)
        await ctx.respond(
            f"🔗 以下のリンクからTwitchと連携してください：\n{auth_url}", ephemeral=True
        )

        if ctx.guild is None:
            try:
                await ctx.author.send(
                    "⚠ このコマンドはサーバー内で実行してください。連携は可能ですがロール付与はできません。"
                )
            finally:
                return

        role_conf = load_role_ids()[str(ctx.guild_id)]

        for _ in range(6):  # 10s x 6 = 60s
            await asyncio.sleep(10)
            users = load_users()
            if discord_id not in users:
                continue

            info: Dict[str, Any] = users[discord_id]
            twitch_name = info.get("twitch_username")
            is_sub = info.get("is_subscriber", False)
            tier = info.get("tier")  # "1000"/"2000"/"3000" or None
            streak = int(info.get("streak_months", 0) or 0)
            cumulative = int(info.get("cumulative_months", 0) or 0)
            since = info.get("subscribed_since")  # ISO文字列 or None

            try:
                member = ctx.guild.get_member(
                    ctx.author.id
                ) or await ctx.guild.fetch_member(ctx.author.id)
            except discord.NotFound:
                member = None

            if member is not None:
                try:
                    await self._ensure_roles_for_member(member, tier, role_conf)
                except discord.Forbidden:
                    await ctx.author.send(
                        "⚠ Botにロール管理権限が不足しているため、ロール付与に失敗しました。管理者に連絡してください。"
                    )
                except Exception as e:
                    await ctx.author.send(
                        f"⚠ ロール付与中にエラーが発生しました: {e!r}"
                    )

            mark_resolved(discord_id)

            # 念のため、直近で保存された値を再読込（初回リンク直後のズレ対策）
            users = load_users()
            info = users.get(discord_id, info)
            streak = int(info.get("streak_months", streak) or streak)
            cumulative = int(info.get("cumulative_months", cumulative) or cumulative)
            since = info.get("subscribed_since", since)

            # フォールバック: サブであれば累計は最低1
            if is_sub and cumulative <= 0:
                cumulative = 1

            tier_msg = {"1000": "1", "2000": "2", "3000": "3"}.get(tier, "0")
            msg = (
                "✅ Twitch連携が完了しました！\n"
                f"・Twitch名: **{twitch_name}**\n"
                f"・サブスク状態: {'✅ 登録中' if is_sub else '❌ 未登録'}\n"
                f"・Tier: {tier_msg}\n"
                # f"・連続月数(streak): {streak}ヶ月\n"
                # f"・累計月数(cumulative): {cumulative}ヶ月\n"
                # f"・サブスク開始日: {since if since else '不明'}\n"
                "※ ロールが反映されていない場合は、数秒待ってから再度ご確認ください。"
            )

            try:
                await ctx.author.send(msg)
                patch_linked_user(discord_id, {"dm_failed": False, "dm_failed_reason": None}, include_none=True)
            except discord.Forbidden:
                patch_linked_user(discord_id, {"dm_failed": True, "dm_failed_reason": "DM拒否 (Forbidden)"})
            except discord.HTTPException as e:
                patch_linked_user(discord_id, {"dm_failed": True, "dm_failed_reason": f"HTTPエラー: {e}"})
            finally:
                return

        try:
            await ctx.author.send(
                "⏳ 60秒経っても連携が完了しませんでした。もう一度 `/link` をお試しください。"
            )
        except discord.Forbidden:
            pass


def setup(bot: commands.Bot):
    bot.add_cog(LinkCog(bot))
