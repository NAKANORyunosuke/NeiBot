from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from bot.bot_client import bot, send_message_to_channel
from django.shortcuts import redirect
from bot.common import get_twitch_keys, get_auth_url
from twitchAPI.twitch import Twitch
import requests
from django.http import HttpResponse
from twitchAPI.twitch import Twitch
from bot.utils.twitch import get_user_info_and_subscription, save_linked_user


@csrf_exempt
def home(request):
    return render(request, 'main/home.html')


@csrf_exempt
def twitch_callback(request):
    print("✅ [twitch_callback] にアクセスがありました")
    code = request.GET.get("code")
    state = request.GET.get("state")  # DiscordのユーザーID（str）

    if not code or not state:
        return HttpResponse("Missing code or state", status=400)

    # 1. Twitchクレデンシャルを取得
    client_id, client_secret, redirect_uri = get_twitch_keys()

    # 2. アクセストークン取得
    token_url = "https://id.twitch.tv/oauth2/token"
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }

    try:
        token_res = requests.post(token_url, data=data)
        token_res.raise_for_status()
    except Exception as e:
        return HttpResponse(f"Token取得エラー: {str(e)}", status=500)

    token_data = token_res.json()
    access_token = token_data["access_token"]

    # 3. ユーザー情報・サブスク情報取得
    try:
        result = get_user_info_and_subscription(access_token)
        print("✅ Twitch情報取得成功")  # ← 追加
    except Exception as e:
        print(f"❌ Twitch情報取得エラー: {str(e)}")
        return HttpResponse(f"Twitchユーザー取得エラー: {str(e)}", status=500)

    # 4. 保存
    print("✅ 保存開始")
    save_linked_user(
        discord_id=state,
        twitch_username=result["username"],
        is_subscriber=result["subscribed"],
        streak=result["streak_months"]
    )
    print("✅ 保存完了")

    return HttpResponse("✅ Twitchとの連携が完了しました。Discordに戻ってください。")


def notify_discord_bot(discord_id, twitch_name, tier):
    try:
        res = requests.post("http://localhost:8000/notify_link", json={
            "discord_id": discord_id,
            "twitch_name": twitch_name,
            "tier": tier
        })
        res.raise_for_status()
    except Exception as e:
        print(f"❌ Discord Bot 通知エラー: {e}")


def redirect_to_login(request):
    return redirect('login')
