import json
import os
import urllib.parse
import requests
import datetime
# ==================== パス設定（絶対パス） ====================

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
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


def get_user_info_and_subscription(access_token, client_id):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Client-Id": client_id
    }

    # ユーザー情報取得
    user_info_resp = requests.get("https://api.twitch.tv/helix/users", headers=headers)
    if user_info_resp.status_code != 200:
        return None, None, None, None
    user_data = user_info_resp.json()["data"][0]
    user_id = user_data["id"]
    user_name = user_data["login"]

    # サブスク情報取得
    # ↓ここで誰に対するサブスクかを指定する必要がある（配信者のuser_id）
    # 例: "broadcaster_id" に自分の配信者ID（固定値）を渡す必要あり
    sub_info_resp = requests.get(
        f"https://api.twitch.tv/helix/subscriptions/user?user_id={user_id}&broadcaster_id=neigechan",
        headers=headers
    )

    if sub_info_resp.status_code != 200:
        return user_name, user_id, "unknown"

    save_linked_users(sub_info_resp.json())

    sub_data = sub_info_resp.json().get("data", [])
    if not sub_data:
        return user_name, user_id, "not_subscribed"

    tier = sub_data[0].get("tier", "unknown")

    streak = sub_data[0].get("streak", "unknown")
    return user_name, user_id, tier, streak
