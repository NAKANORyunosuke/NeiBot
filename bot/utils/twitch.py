import json
import os
import urllib.parse
import requests
import datetime
from typing import Optional, Union

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

def load_linked_users() -> dict:
    if not os.path.exists(LINKED_USERS_FILE):
        return {}
    with open(LINKED_USERS_FILE, "r", encoding="utf-8") as f:
        content = f.read().strip()
        if not content:
            return {}
        return json.loads(content)

def save_linked_users(data: dict):
    os.makedirs(os.path.dirname(LINKED_USERS_FILE), exist_ok=True)
    with open(LINKED_USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False, default=str)

# ==================== ユーザー保存（辞書対応＋後方互換） ====================

def save_linked_user(
    user: Union[dict, str],
    twitch_username: Optional[str] = None,
    is_subscriber: Optional[bool] = None,
    streak: Optional[int] = None,
    tier: Optional[str] = None
):
    """
    推奨: dict 形式
        save_linked_user({
            "discord_id": "1234567890",
            "twitch_username": "neigechan",
            "tier": "1000",       # 2000, 3000 など
            "streak": 5,          # 取れない場合は None
            "linked_date": "YYYY-MM-DD"  # 省略可: 自動付与
        })

    後方互換(非推奨):
        save_linked_user(discord_id, twitch_username, is_subscriber, streak)
    """
    data = load_linked_users()
    today = datetime.date.today().isoformat()

    if isinstance(user, str):
        # 旧形式
        discord_id = user
        entry = {
            "discord_id": discord_id,
            "twitch_username": twitch_username,
            "is_subscriber": is_subscriber,
            "tier": tier,
            "streak": streak,
            "linked_date": today,
        }
    elif isinstance(user, dict):
        # 新形式
        discord_id = user["discord_id"]
        entry = {
            "discord_id": discord_id,
            "twitch_username": user.get("twitch_username"),
            "is_subscriber": user.get("is_subscriber"),
            "tier": user.get("tier"),
            "streak": user.get("streak"),
            "linked_date": user.get("linked_date") or today,
        }
    else:
        raise TypeError("user must be dict or str")

    entry = {k: v for k, v in entry.items() if v is not None}  # None を落とす
    data[discord_id] = entry
    save_linked_users(data)

# ==================== ユーティリティ ====================

def get_linked_user(discord_id: str) -> Optional[dict]:
    return load_linked_users().get(discord_id)

def unlink_user(discord_id: str) -> bool:
    data = load_linked_users()
    if discord_id in data:
        del data[discord_id]
        save_linked_users(data)
        return True
    return False

def upsert_fields(discord_id: str, **fields):
    data = load_linked_users()
    current = data.get(
        discord_id,
        {"discord_id": discord_id, "linked_date": datetime.date.today().isoformat()}
    )
    current.update({k: v for k, v in fields.items() if v is not None})
    data[discord_id] = current
    save_linked_users(data)

# ==================== ユーザー情報取得（Helix判定） ====================

def get_user_info_and_subscription(access_token_broadcaster: str, client_id: str, viewer_access_token_for_user_lookup: str):
    # 視聴者の user_id
    headers_viewer = {
        "Authorization": f"Bearer {viewer_access_token_for_user_lookup}",
        "Client-Id": client_id
    }
    r_user = requests.get("https://api.twitch.tv/helix/users", headers=headers_viewer, timeout=15)
    r_user.raise_for_status()
    user = r_user.json()["data"][0]
    viewer_id = user["id"]
    viewer_login = user["login"]

    # サブスク確認（配信者トークン: channel:read:subscriptions）
    headers_broadcaster = {
        "Authorization": f"Bearer {access_token_broadcaster}",
        "Client-Id": client_id
    }
    with open(TOKEN_PATH, "r", encoding="utf-8") as f:
        conf = json.load(f)
        BROADCASTER_ID = conf["twitch_id"]

    r_sub = requests.get(
        "https://api.twitch.tv/helix/subscriptions/user",
        headers=headers_broadcaster,
        params={"broadcaster_id": BROADCASTER_ID, "user_id": viewer_id},
        timeout=20
    )
    print("SUB status:", r_sub.status_code, "body:", r_sub.text)

    if r_sub.status_code == 404:
        return viewer_login, viewer_id, "not_subscribed", None

    r_sub.raise_for_status()
    sub_data = r_sub.json().get("data", [])
    if not sub_data:
        return viewer_login, viewer_id, "not_subscribed", None

    tier = sub_data[0].get("tier", "unknown")
    streak = sub_data[0].get("streak")  # Helixでは通常 None
    return viewer_login, viewer_id, tier, streak
