import discord
from discord.ext import commands
from bot.utils.save_and_load import load_users, delete_linked_user


class Unlink(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.slash_command(
        name="unlink",
        description="あなたのTwitchリンクを解除します"
    )
    async def unlink(self, ctx: discord.ApplicationContext):
        discord_id = str(ctx.author.id)
        linked_users = load_users()

        if discord_id in linked_users:
            delete_linked_user(discord_id)
            await ctx.respond("Twitchアカウントとのリンクを解除しました ✅", ephemeral=True)
        else:
            await ctx.respond("あなたのアカウントはTwitchとリンクされていません。", ephemeral=True)


def setup(bot):
    bot.add_cog(Unlink(bot))
