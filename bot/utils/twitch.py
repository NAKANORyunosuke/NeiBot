from discord.ext import commands
import discord
import json
import os
import urllib.parse
import requests
from twitchAPI.twitch import Twitch

# ==================== 設定ファイル ====================

TOKEN_PATH = "./venv/token.json"
LINKED_USERS_FILE = "./venv/linked_users.json"

# ==================== Bot 本体 ====================

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ==================== ユーティリティ関数 ====================


def get_twitch_keys():
    """Twitchクライアント情報を取得"""
    with open(TOKEN_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["twitch_client_id"], data["twitch_seqret_key"], data["twitch_redirect_uri"]


def get_auth_url(discord_user_id: str):
    """Twitch OAuthのURLを生成"""
    client_id, _, redirect_uri = get_twitch_keys()
    base = "https://id.twitch.tv/oauth2/authorize"
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "user:read:subscriptions",  # 必要なスコープ
        "state": discord_user_id,  # CSRF対策 + 誰が認証したか判別用
    }
    return f"{base}?{urllib.parse.urlencode(params)}"


def load_linked_users():
    if not os.path.exists(LINKED_USERS_FILE):
        return {}
    with open(LINKED_USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_linked_users(data):
    os.makedirs(os.path.dirname(LINKED_USERS_FILE), exist_ok=True)
    with open(LINKED_USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def save_linked_user(discord_id: str, twitch_username: str):
    data = load_linked_users()
    data[discord_id] = twitch_username
    save_linked_users(data)


def get_user_info_and_subscription(access_token: str):
    client_id, client_secret, redirect_uri = get_twitch_keys()

    # Twitchオブジェクト
    twitch = Twitch(client_id, client_secret)
    twitch.set_user_authentication(access_token, ['user:read:subscriptions'], refresh_token=None)

    # ユーザー情報取得（ID + 名前）
    user_data = twitch.get_users()['data'][0]
    twitch_user_id = user_data['id']
    twitch_username = user_data['login']

    # ユーザーが配信者にサブスクしているかどうかを調べるには、
    # 「誰に対して」サブスクしているか、を指定する必要がある！
    # つまり → 対象の配信者ID（＝あなた）が必要

    # ✅ あなたの配信者IDを事前に固定（例）
    broadcaster_id = 'YOUR_TWITCH_ID_HERE'  # 自分の配信者のTwitch ID

    # URL指定で直接GET
    sub_url = f"https://api.twitch.tv/helix/subscriptions/user"
    headers = {
        "Client-ID": client_id,
        "Authorization": f"Bearer {access_token}"
    }
    params = {
        "broadcaster_id": broadcaster_id,
        "user_id": twitch_user_id
    }

    sub_res = requests.get(sub_url, headers=headers, params=params)
    if sub_res.status_code == 200:
        sub_data = sub_res.json().get("data", [])
        if sub_data:
            sub_info = sub_data[0]
            is_subscribed = True
            streak_months = sub_info.get("cumulative_months", 1)
        else:
            is_subscribed = False
            streak_months = 0
    else:
        is_subscribed = False
        streak_months = 0

    return {
        "username": twitch_username,
        "subscribed": is_subscribed,
        "streak_months": streak_months
    }
