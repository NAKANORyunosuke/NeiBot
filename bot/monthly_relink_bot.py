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
import json
import asyncio
import datetime as dt
from typing import Dict, Any

import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bot.common import save_all_guild_members, load_guild_members, get_guild_id

# ========= 定数・パス =========
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "venv")
LINKED_USERS_FILE = os.path.join(DATA_DIR, "linked_users.json")
RELINK_STATE_FILE = os.path.join(DATA_DIR, "relink_state.json")
JST = dt.timezone(dt.timedelta(hours=9))


# ========= 共通ユーティリティ =========
def _load_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def load_linked_users() -> Dict[str, Any]:
    """
    linked_users.json 例:
    {
        "123456789012345678": {
            "twitch_login": "foo",
            "linked_at": "2025-08-01T12:34:56+09:00",
            "last_verified_at": "2025-08-10T09:00:00+09:00",
            "tier": "Tier1"
        }
    }
    """
    return _load_json(LINKED_USERS_FILE)


def load_relink_state() -> Dict[str, Any]:
    """
    relink_state.json 例:
    {
        "123456789012345678": {
            "first_notice_at": "2025-09-01T09:05:00+09:00",
            "last_notice_at": "2025-09-08T09:05:00+09:00",
            "resolved": false
        }
    }
    """
    return _load_json(RELINK_STATE_FILE)


def save_relink_state(data: Dict[str, Any]) -> None:
    _save_json(RELINK_STATE_FILE, data)


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
        print(f"[DM送信失敗] user={discord_user_id} err={e!r}")
        return False


def mark_resolved(discord_id: str) -> None:
    """
    OAuth完了や当月の購読確認がとれたタイミングで呼ぶと、再送対象から外れる。
    既存のOAuthコールバックや検証処理から利用してください。
    """
    state = load_relink_state()
    if discord_id in state:
        # print(discord_id, state)
        state[discord_id]["resolved"] = True
        save_relink_state(state)


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
            print("[monthly] 月初めではないためスキップ")
            return

        linked = load_linked_users()
        state = load_relink_state()
        guild_id = get_guild_id()
        members_list = load_guild_members()[guild_id].keys()
        sent = 0
        for discord_id in set(map(str, linked.keys())) | set(map(str, members_list)):
            ok = await send_dm(self.bot, int(discord_id), build_relink_message(discord_id))
            if ok:
                sent += 1
                s = state.setdefault(discord_id, {})
                s["first_notice_at"] = now.isoformat()
                s["last_notice_at"] = now.isoformat()
                s["resolved"] = False
            await asyncio.sleep(1)  # レート制御（必要に応じて調整）

        save_relink_state(state)
        print(f"[monthly] 送信完了: {sent}件")

    async def resend_after_7days_if_unlinked(self) -> None:
        now = jst_now()
        linked = load_linked_users()
        state = load_relink_state()

        resend_cnt = 0
        guild_id = get_guild_id()
        members_list = load_guild_members()[guild_id].keys()
        
        for discord_id, s in list(state.items()):
            if (s.get("resolved") is True) or (discord_id not in members_list):
                continue

            first_str = s.get("first_notice_at")
            if not first_str:
                continue

            try:
                first_at = dt.datetime.fromisoformat(first_str)
            except Exception:
                continue

            # 7日未満なら見送り
            if (now - first_at).days < 7:
                continue

            # 「当月に検証済み(last_verified_at)」なら解決扱い
            lu = linked.get(discord_id)
            if lu and lu.get("last_verified_at"):
                try:
                    last_ver = dt.datetime.fromisoformat(lu["last_verified_at"])
                    if (last_ver.year == now.year) and (last_ver.month == now.month):
                        s["resolved"] = True
                        continue
                except Exception:
                    pass

            ok = await send_dm(self.bot, int(discord_id), build_relink_message(discord_id))
            if ok:
                s["last_notice_at"] = now.isoformat()
                resend_cnt += 1

            await asyncio.sleep(0.5)

        save_relink_state(state)
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
        print("[scheduler] started")
        
        save_all_guild_members(self.bot)

    # ===== テスト用スラッシュコマンド =====
    @discord.slash_command(name="force_relink", description="（テスト）今すぐ全員に再リンクDMを送ります")
    async def force_relink(self, ctx: discord.ApplicationContext):
        await ctx.respond("今から再リンクDMを送ります…", ephemeral=True)
        await self.notify_monthly_relink(force=True)
        await ctx.followup.send("送信が完了しました。", ephemeral=True)

    @discord.slash_command(name="force_resend", description="（テスト）今すぐ『7日経過・未解決』へ再送します")
    async def force_resend(self, ctx: discord.ApplicationContext):
        await ctx.respond("今から未解決ユーザーへ再送します…", ephemeral=True)
        await self.resend_after_7days_if_unlinked()
        await ctx.followup.send("再送が完了しました。", ephemeral=True)

    @discord.slash_command(name="relink_status", description="（テスト）再リンク状態の要約を表示します")
    async def relink_status(self, ctx: discord.ApplicationContext):
        state = load_relink_state()
        unresolved = [k for k, v in state.items() if not v.get("resolved")]
        await ctx.respond(
            f"未解決ユーザー: {len(unresolved)}件\n"
            f"ユーザーID一覧（最大10件）: {', '.join(unresolved[:10]) if unresolved else 'なし'}",
            ephemeral=True,
        )


# ========= エクステンションエントリ =========
def setup(bot):
    """bot.load_extension で読み込むためのエントリポイント"""
    bot.add_cog(ReLinkCog(bot))
