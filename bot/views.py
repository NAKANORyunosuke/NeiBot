from discord.ui import View, Button, Modal, InputText
import discord


class TwitchUsernameModal(Modal):
    def __init__(self):
        super().__init__(title="Twitchユーザー名を入力")

        self.add_item(InputText(label="Twitch Username", placeholder="例: example_user"))

    async def callback(self, interaction: discord.Interaction):
        twitch_username = self.children[0].value

        await interaction.response.send_message(
            f"Twitchユーザー名 `{twitch_username}` を受け取りました。処理を開始します。",
            ephemeral=True
        )

        # ここでTwitch APIを使ってtier取得 & ロール付与
        # await assign_role_based_on_tier(interaction.user, twitch_username)


class TwitchLinkButton(View):
    def __init__(self):
        super().__init__(timeout=None)

        self.add_item(Button(label="Twitch連携", style=discord.ButtonStyle.primary, custom_id="twitch_link"))

    @discord.ui.button(label="Twitch連携", style=discord.ButtonStyle.primary, custom_id="twitch_link")
    async def button_callback(self, button, interaction: discord.Interaction):
        await interaction.response.send_modal(TwitchUsernameModal())
