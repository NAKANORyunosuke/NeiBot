# bot/monthly_relink_bot.py
# -*- coding: utf-8 -*-
"""
月初めにTwitch再リンクDMを送る機能（APScheduler + py-cord）
- Django非依存
- Cog化：bot.load_extension("bot.monthly_relink_bot") で読み込み可能
- 単体実行も可（__main__）
対応コマンド:
  /force_relink      : すぐ全員に再リンクDM
  /force_resend      : すぐ「7日経過未解決」へ再送
  /relink_status     : 未解決ユーザー数と一部一覧
"""

from __future__ import annotations
import os
import asyncio
import datetime as dt

import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bot.utils.save_and_load import (
    load_users,
    patch_linked_user,
)
from bot.common import debug_print

# ========= 定数・パス =========
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "venv")
USERS_FILE = os.path.join(DATA_DIR, "all_users.json")
JST = dt.timezone(dt.timedelta(hours=9))


def jst_now() -> dt.datetime:
    return dt.datetime.now(tz=JST)


def build_relink_message(discord_id: str) -> str:
    # auth_url = get_auth_url(discord_id)
    lines = [
        "こんにちは！毎月のTwitch再リンクのお願いです 👇",
        "",
        "今月もサブスク特典を適用するため、サーバーで /link コマンドを実行してください。",
        "（すでに連携済みならこのメッセージは無視してOKです）",
        # "",
        # f"{auth_url}",
        # "",
        "※ 1週間後に未連携の場合は自動で再送します。",
    ]
    return "\n".join(lines)


async def send_dm(bot: commands.Bot, discord_user_id: int, content: str) -> bool:
    try:
        user = await bot.fetch_user(discord_user_id)
        await user.send(content)
        return True
    except Exception as e:
        debug_print(f"[DM送信失敗] user={discord_user_id} err={e!r}")
        return False


def mark_resolved(discord_id: str) -> None:
    """
    OAuth完了や当月の購読確認がとれたタイミングで呼ぶと、再送対象から外れる。
    既存のOAuthコールバックや検証処理から利用してください。
    """
    did = str(discord_id)
    patch_linked_user(
        did,
        {"resolved": True, "roles_revoked": False, "roles_revoked_at": None},
        include_none=True,
    )


# ========= Cog 実装 =========
class ReLinkCog(commands.Cog):
    """月初め再リンク＆7日後再送のスケジュール運用とテスト用コマンドを提供"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler(timezone="Asia/Tokyo")
        self._scheduler_started = False

    async def _revoke_link_roles(
        self, discord_id: str, role_map: dict[str, dict[str, int]] | None = None
    ) -> bool:
        if role_map is None:
            role_map = load_role_ids() or {}
        removed_any = False
        try:
            discord_int = int(discord_id)
        except (TypeError, ValueError):
            return False

        for guild in self.bot.guilds:
            role_conf = (role_map or {}).get(str(guild.id))
            if not role_conf:
                continue
            try:
                member = guild.get_member(discord_int) or await guild.fetch_member(discord_int)
            except (discord.NotFound, discord.Forbidden):
                continue
            except discord.HTTPException:
                continue

            target_role_ids: set[int] = set()
            for rid in role_conf.values():
                if isinstance(rid, int):
                    target_role_ids.add(rid)
                else:
                    try:
                        target_role_ids.add(int(rid))
                    except (TypeError, ValueError):
                        continue
            if not target_role_ids:
                continue

            roles_to_remove = [role for role in member.roles if role.id in target_role_ids]
            if not roles_to_remove:
                continue

            try:
                await member.remove_roles(
                    *roles_to_remove, reason="Twitch link: revoke roles (unresolved)"
                )
                removed_any = True
            except discord.Forbidden:
                continue
            except discord.HTTPException:
                continue

            if removed_any:
                break

        return removed_any

    # ===== スケジュール本体 =====
    async def notify_monthly_relink(self, *, force: bool = False) -> None:
        now = jst_now()
        if not force and now.day != 1:
            debug_print("[monthly] 月初めではないためスキップ")
            return

        sent = 0
        state = load_users()
        # 値が辞書でない（誤って混入した）トップレベルキーを無視
        for discord_id, user in list(state.items()):
            if not isinstance(user, dict):
                continue
            ok = await send_dm(
                self.bot, int(discord_id), build_relink_message(discord_id)
            )
            if ok:
                sent += 1
                patch_linked_user(
                    str(discord_id),
                    {
                        "first_notice_at": now.isoformat(),
                        "last_notice_at": now.isoformat(),
                        "resolved": False,
                    },
                )
            await asyncio.sleep(1)  # レート制御（必要に応じて調整）
        print(f"[monthly] 送信完了: {sent}件")

    async def resend_after_7days_if_unlinked(self) -> None:
        now = jst_now()
        users = load_users()
        role_map = load_role_ids() or {}
        resend_cnt = 0

        # 値が辞書でない（誤って混入した）トップレベルキーを無視
        for discord_id, lu in list(users.items()):
            if not isinstance(lu, dict):
                continue
            if lu.get("resolved", False):
                continue

            last_str = lu.get("last_notice_at", None)
            if last_str is None:
                continue

            try:
                last_at = dt.datetime.fromisoformat(last_str)
                if last_at.tzinfo is None:
                    last_at = last_at.replace(tzinfo=JST)
            except Exception:
                continue
            # 7日未満なら見送り
            print(discord_id, lu)
            if (now - last_at).days < 7:
                continue
            print((now - last_at).days)
            # 「当月に検証済み(last_verified_at)」なら解決扱い
            if lu.get("last_verified_at", None):
                try:
                    last_ver = dt.datetime.fromisoformat(lu["last_verified_at"])
                    if last_ver.tzinfo is None:
                        last_ver = last_ver.replace(tzinfo=JST)
                    if (last_ver.year == now.year) and (last_ver.month == now.month):
                        lu["resolved"] = True
                        continue
                except Exception:
                    pass
            else:
                lu["resolved"] = False

            revoked = await self._revoke_link_roles(discord_id, role_map=role_map)
            if revoked:
                patch_linked_user(
                    str(discord_id),
                    {"roles_revoked": True, "roles_revoked_at": now.isoformat()},
                )

            ok = await send_dm(
                self.bot, int(discord_id), build_relink_message(discord_id)
            )
            if ok:
                resend_cnt += 1
                patch_linked_user(
                    str(discord_id),
                    {"last_notice_at": now.isoformat(), "resolved": False},
                )

            await asyncio.sleep(0.5)
        print(f"[resend] 再送完了: {resend_cnt}件")

    # ===== イベントでスケジューラ起動 =====
    @commands.Cog.listener()
    async def on_ready(self):

        # 複数回 on_ready が来ても二重起動しないように
        if self._scheduler_started:
            return
        # 毎月1日 09:05 JST に初回通知
        self.scheduler.add_job(
            self.notify_monthly_relink,
            CronTrigger(day="1", hour=9, minute=5, timezone="Asia/Tokyo"),
            kwargs={"force": False},
            id="monthly_relink_first_day",
            replace_existing=True,
        )
        # 毎日 09:10 JST に「7日経過未解決へ再送」
        self.scheduler.add_job(
            self.resend_after_7days_if_unlinked,
            CronTrigger(hour=9, minute=10, timezone="Asia/Tokyo"),
            id="monthly_relink_resend",
            replace_existing=True,
        )
        self.scheduler.start()
        self._scheduler_started = True
        debug_print("[scheduler] started")

    # ===== テスト用スラッシュコマンド =====
    @discord.slash_command(
        name="force_relink", description="（テスト）今すぐ全員に再リンクDMを送ります"
    )
    async def force_relink(self, ctx: discord.ApplicationContext):
        await ctx.respond("今から再リンクDMを送ります…", ephemeral=True)
        await self.notify_monthly_relink(force=True)
        await ctx.followup.send("送信が完了しました。", ephemeral=True)

    @discord.slash_command(
        name="force_resend",
        description="（テスト）今すぐ『7日経過・未解決』へ再送します",
    )
    async def force_resend(self, ctx: discord.ApplicationContext):
        await ctx.respond("今から未解決ユーザーへ再送します…", ephemeral=True)
        await self.resend_after_7days_if_unlinked()
        await ctx.followup.send("再送が完了しました。", ephemeral=True)

    @discord.slash_command(
        name="relink_status", description="（テスト）再リンク状態の要約を表示します"
    )
    async def relink_status(self, ctx: discord.ApplicationContext):
        state = load_users()
        # 値が辞書のエントリのみ対象にし、安全に集計
        unresolved = [
            k
            for k, v in state.items()
            if isinstance(v, dict) and not v.get("resolved", False)
        ]
        await ctx.respond(
            f"未解決ユーザー: {len(unresolved)}件\n"
            f"ユーザーID一覧（最大10件）: {', '.join(unresolved[:10]) if unresolved else 'なし'}",
            ephemeral=True,
        )


# ========= エクステンションエントリ =========
def setup(bot):
    """bot.load_extension で読み込むためのエントリポイント"""
    bot.add_cog(ReLinkCog(bot))
