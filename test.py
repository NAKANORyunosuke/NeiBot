import asyncio
from twitchAPI.twitch import Twitch
from twitchAPI.oauth import UserAuthenticator
from twitchAPI.type import AuthScope

CLIENT_ID = 'fv8nqktj88anstz5avbfgpbe44v9sz'
CLIENT_SECRET = '5y4wrsvw0eju9bygxv2obcdmhba7e8'

async def main():
    twitch = await Twitch(CLIENT_ID, CLIENT_SECRET)
    
    # OAuth スコープを指定（ユーザーのサブスク情報を読むため）
    auth = UserAuthenticator(twitch, [AuthScope.USER_READ_SUBSCRIPTIONS])
    
    token, refresh_token = await auth.authenticate()

    # 認証トークンを設定
    twitch.set_user_authentication(token, [AuthScope.USER_READ_SUBSCRIPTIONS], refresh_token)

    # チャンネルID（例: 自分のTwitchチャンネルID）に対するサブスク情報を取得
    channel_id = '172270081'  # ←調べて適切に置き換える
    sub_info = await twitch.get_user_subscription(channel_id)

    print(f'Tier: {sub_info.tier}')  # 例: '1000' = Tier 1

# 非同期関数を実行
asyncio.run(main())