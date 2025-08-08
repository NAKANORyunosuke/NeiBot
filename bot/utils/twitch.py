import json
import os
import urllib.parse
import requests
import datetime
# ==================== パス設定（絶対パス） ====================

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
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


def get_user_info_and_subscription(access_token_broadcaster, client_id, viewer_access_token_for_user_lookup):
    # 視聴者の user_id を知るには視聴者トークン or そのloginが必要
    headers_viewer = {
        "Authorization": f"Bearer {viewer_access_token_for_user_lookup}",
        "Client-Id": client_id
    }
    r_user = requests.get("https://api.twitch.tv/helix/users", headers=headers_viewer, timeout=15)
    print(r_user)
    r_user.raise_for_status()
    user = r_user.json()["data"][0]
    viewer_id = user["id"]
    viewer_login = user["login"]

    # サブスク確認は配信者トークンで！
    headers_broadcaster = {
        "Authorization": f"Bearer {access_token_broadcaster}",  # 配信者の token（channel:read:subscriptions）
        "Client-Id": client_id
    }
    with open(TOKEN_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
        BROADCASTER_ID = data["twitch_id"]
        
    print(BROADCASTER_ID)
    r_sub = requests.get(
        "https://api.twitch.tv/helix/subscriptions/user",
        headers=headers_broadcaster,
        params={"broadcaster_id": BROADCASTER_ID, "user_id": viewer_id},
        timeout=20
    )
    print("SUB status:", r_sub.status_code, "body:", r_sub.text)

    if r_sub.status_code == 404:
        # 仕様どおり「未サブ」で 404
        return viewer_login, viewer_id, "not_subscribed", "unknown"

    r_sub.raise_for_status()
    data = r_sub.json().get("data", [])
    if not data:
        return viewer_login, viewer_id, "not_subscribed", "unknown"

    tier = data[0].get("tier", "unknown")
    streak = data[0].get("streak", "unknown")
    return viewer_login, viewer_id, tier, streak