import json
import os
import urllib.parse
import requests
import datetime
# ==================== パス設定（絶対パス） ====================

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
TOKEN_PATH = os.path.join(PROJECT_ROOT, "venv", "token.json")
LINKED_USERS_FILE = os.path.join(PROJECT_ROOT, "venv", "linked_users.json")

# ==================== 認証情報取得 ====================


def get_twitch_keys():
    with open(TOKEN_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["twitch_client_id"], data["twitch_seqret_key"], data["twitch_redirect_uri"]

# ==================== OAuth URL生成 ====================


def get_auth_url(discord_user_id: str):
    client_id, _, redirect_uri = get_twitch_keys()
    base = "https://id.twitch.tv/oauth2/authorize"
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "user:read:subscriptions",
        "state": discord_user_id,
    }
    return f"{base}?{urllib.parse.urlencode(params)}"

# ==================== JSON読み書き ====================


def load_linked_users():
    if not os.path.exists(LINKED_USERS_FILE):
        return {}
    with open(LINKED_USERS_FILE, "r", encoding="utf-8") as f:
        content = f.read().strip()
        if not content:
            return {}
        return json.loads(content)


def save_linked_users(data):
    os.makedirs(os.path.dirname(LINKED_USERS_FILE), exist_ok=True)
    with open(LINKED_USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False, default=str)


def save_linked_user(discord_id: str, twitch_username: str, is_subscriber: bool, streak: int):
    data = load_linked_users()

    dt = datetime.date.today()

    data[discord_id] = {
        "twitch_username": twitch_username,
        "is_subscriber": is_subscriber,
        "streak": streak,
        "linked_date": dt.isoformat()
    }
    save_linked_users(data)

# ==================== ユーザー情報取得 ====================


def get_user_info_and_subscription(access_token: str):
    client_id, _, _ = get_twitch_keys()

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Client-Id": client_id,
    }

    # ✅ ユーザー情報取得
    user_res = requests.get("https://api.twitch.tv/helix/users", headers=headers)
    user_res.raise_for_status()
    user_data = user_res.json()["data"][0]

    twitch_user_id = user_data["id"]
    twitch_username = user_data["login"]

    # ✅ サブスク情報（配信者IDを指定）
    broadcaster_id = "YOUR_TWITCH_ID_HERE"  # ← あなたのTwitch IDをここに書く

    sub_url = "https://api.twitch.tv/helix/subscriptions/user"
    params = {
        "broadcaster_id": broadcaster_id,
        "user_id": twitch_user_id
    }

    sub_res = requests.get(sub_url, headers=headers, params=params)

    is_subscribed = False
    streak_months = 0

    if sub_res.status_code == 200:
        sub_data = sub_res.json().get("data", [])
        if sub_data:
            is_subscribed = True
            streak_months = sub_data[0].get("cumulative_months", 1)

    return {
        "username": twitch_username,
        "subscribed": is_subscribed,
        "streak_months": streak_months
    }
