from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from bot.bot_client import bot, send_message_to_channel
from django.shortcuts import redirect
from bot.common import get_twitch_keys, get_auth_url
from twitchAPI.twitch import Twitch
import requests
from django.http import HttpResponse
from twitchAPI.twitch import Twitch
from bot.utils.twitch import get_twitch_keys, save_linked_user
from bot.utils.twitch import get_user_info_and_subscription, save_linked_user

test_channel_id = 1401953150558277795


@csrf_exempt
def home(request):
    if request.method == 'POST':
        if "send_message" in request.POST:
            print("送信ボタンが押されました")

            loop = getattr(bot, 'loop', None)
            if loop and loop.is_running():
                loop.create_task(send_message_to_channel(test_channel_id, "Webから送信されました"))
            else:
                print("Botが未起動または準備中")
            return render(request, 'main/home.html', {'message': '送信処理しました'})
    return render(request, 'main/home.html')


@csrf_exempt
def twitch_callback(request):
    code = request.GET.get("code")
    state = request.GET.get("state")  # DiscordのユーザーIDを受け取る

    if not code or not state:
        return HttpResponse("Missing code or state", status=400)

    # 1. クレデンシャルを取得
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

    # 3. ユーザー情報・サブスク情報を取得
    try:
        result = get_user_info_and_subscription(access_token)
    except Exception as e:
        return HttpResponse(f"Twitchユーザー取得エラー: {str(e)}", status=500)

    # 4. 保存（linked_users.json に書き込む）
    save_linked_user(
        discord_id=state,
        twitch_username=result["username"],
        is_subscriber=result["subscribed"],
        streak=result["streak_months"]
    )

    return HttpResponse("✅ Twitchとの連携が完了しました。Discordに戻ってください。")


def redirect_to_login(request):
    return redirect('login')
