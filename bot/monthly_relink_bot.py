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
    save_linked_users,
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
    state = load_users()
    state[str(discord_id)]["resolved"] = True
    save_linked_users(state)


# ========= Cog 実装 =========
class ReLinkCog(commands.Cog):
    """月初め再リンク＆7日後再送のスケジュール運用とテスト用コマンドを提供"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler(timezone="Asia/Tokyo")
        self._scheduler_started = False

    # ===== スケジュール本体 =====
    async def notify_monthly_relink(self, *, force: bool = False) -> None:
        now = jst_now()
        if not force and now.day != 1:
            debug_print("[monthly] 月初めではないためスキップ")
            return

        state = load_users()

        sent = 0
        for discord_id in list(state.keys()):
            ok = await send_dm(
                self.bot, int(discord_id), build_relink_message(discord_id)
            )
            if ok:
                sent += 1
                state[str(discord_id)]["first_notice_at"] = now.isoformat()
                state[str(discord_id)]["last_notice_at"] = now.isoformat()
                state[str(discord_id)]["resolved"] = False
            await asyncio.sleep(1)  # レート制御（必要に応じて調整）

        save_linked_users(state)
        print(f"[monthly] 送信完了: {sent}件")

    async def resend_after_7days_if_unlinked(self) -> None:
        now = jst_now()
        users = load_users()
        resend_cnt = 0

        for discord_id in list(users.keys()):
            if users[str(discord_id)].get("resolved", False):
                continue

            first_str = users[str(discord_id)].get("first_notice_at", None)
            if first_str is None:
                continue

            try:
                first_at = dt.datetime.fromisoformat(first_str)
            except Exception:
                continue

            # 7日未満なら見送り
            if (now - first_at).days < 7:
                continue

            # 「当月に検証済み(last_verified_at)」なら解決扱い
            lu = users.get(str(discord_id))
            if lu.get("last_verified_at", None) is not None:
                try:
                    last_ver = dt.datetime.fromisoformat(lu["last_verified_at"])
                    if (last_ver.year == now.year) and (last_ver.month == now.month):
                        users["resolved"] = True
                        continue
                except Exception:
                    pass
            else:
                users["resolved"] = False

            ok = await send_dm(
                self.bot, int(discord_id), build_relink_message(discord_id)
            )
            if ok:
                users["last_notice_at"] = now.isoformat()
                resend_cnt += 1

            await asyncio.sleep(0.5)

        save_linked_users(users)
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
        unresolved = [k for k, v in state.items() if not v.get("resolved", False)]
        await ctx.respond(
            f"未解決ユーザー: {len(unresolved)}件\n"
            f"ユーザーID一覧（最大10件）: {', '.join(unresolved[:10]) if unresolved else 'なし'}",
            ephemeral=True,
        )


# ========= エクステンションエントリ =========
def setup(bot):
    """bot.load_extension で読み込むためのエントリポイント"""
    bot.add_cog(ReLinkCog(bot))
