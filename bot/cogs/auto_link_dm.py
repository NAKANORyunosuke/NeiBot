import discord
from discord.ext import commands
from bot.utils.save_and_load import *


class AutoLinkDM(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # サーバーに参加した時に呼ばれる
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        discord_id = str(member.id)

        # 既にリンク済みならDM不要
        linked_users = load_users()
        if discord_id in linked_users:
            return

        try:
            # DM送信
            await member.send(
                "👋 Neigeのサーバーへようこそ！\n\n"
                "Twitchサブスク連携を完了するには、サーバー内で `/link` コマンドを実行してください。\n\n"
                "🔗 連携が完了すると：\n"
                "・自動的にサブスク専用ロールが付与されます ✅\n"
                "・サブスク限定のチャンネルや特典にアクセスできます 🎁\n\n"
                "ぜひお早めに連携をお願いします！"
            )
        except discord.Forbidden:
            # ユーザーがDM拒否設定にしている場合は無視
            print(f"⚠ {member} にDMを送信できませんでした。")


def setup(bot):
    bot.add_cog(AutoLinkDM(bot))
