import json
import os
import urllib.parse
import requests
import datetime
import httpx

# ==================== パス設定（絶対パス） ====================

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
TOKEN_PATH = os.path.join(PROJECT_ROOT, "venv", "token.json")
LINKED_USERS_FILE = os.path.join(PROJECT_ROOT, "venv", "linked_users.json")

# ==================== 認証情報取得 ====================


def get_twitch_keys():
    with open(TOKEN_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["twitch_client_id"], data["twitch_seqret_key"], data["twitch_redirect_uri"]


def get_broadcast_id():
    with open(TOKEN_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["twitch_id"]
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


async def get_user_info_and_subscription(viewer_access_token: str, client_id: str, broadcaster_id: str):
    headers = {
        "Authorization": f"Bearer {viewer_access_token}",
        "Client-Id": client_id,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        # 1) /users
        r = await client.get("https://api.twitch.tv/helix/users", headers=headers)
        print("[DEBUG] /users status:", r.status_code)
        try:
            print("[DEBUG] /users body:", r.text)
        except Exception:
            pass
        r.raise_for_status()
        me = r.json()["data"][0]
        user_id = me["id"]
        user_login = me["login"]

        # 2) /subscriptions/user
        params = {"broadcaster_id": broadcaster_id, "user_id": user_id}
        r2 = await client.get("https://api.twitch.tv/helix/subscriptions/user", headers=headers, params=params)
        print("[DEBUG] /subscriptions/user status:", r2.status_code)
        print("[DEBUG] /subscriptions/user body:", r2.text)

        if r2.status_code == 404:
            return user_login, user_id, None, None

        r2.raise_for_status()
        data = r2.json().get("data", [])
        if not data:
            return user_login, user_id, None, None

        sub = data[0]
        tier = sub.get("tier")
        streak = sub.get("cumulative_months", sub.get("streak"))
        return user_login, user_id, tier, streak

