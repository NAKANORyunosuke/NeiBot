from typing import Dict, Any, Optional, Tuple
import os
import json
import datetime as dt
import inspect as _inspect

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
DATA_DIR = os.path.join(PROJECT_ROOT, "venv")
USERS_FILE = os.path.join(DATA_DIR, "all_users.json")
TOKEN_FILE = os.path.join(DATA_DIR, "token.json")
ROLE_FILE = os.path.join(DATA_DIR, "role_id.json")
JST = dt.timezone(dt.timedelta(hours=9))


def load_role_ids() -> Dict[str, Any]:
    if not os.path.exists(ROLE_FILE):
        return {}
    with open(ROLE_FILE, "r", encoding="utf-8") as f:
        content = f.read().strip()
        if not content:
            return {}
        return json.loads(content)


def save_role_ids(data: Dict[str, Any]):
    os.makedirs(os.path.dirname(ROLE_FILE), exist_ok=True)
    with open(ROLE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False, default=str)


def load_users() -> Dict[str, Any]:
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        content = f.read().strip()
        if not content:
            return {}
        return json.loads(content)


def save_linked_users(data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(USERS_FILE), exist_ok=True)
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False, default=str)


def get_guild_id():
    with open(TOKEN_FILE, "r", encoding="utf-8") as f:
        GUILD_ID = json.load(f)["guild_id"]
    return GUILD_ID


# all_users.jsonの定義 最初だけにする
# 後々追加されるキーのためにDictとして保存しない
def save_linked_user(
    discord_id: str,
    twitch_username: str,
    tier: Optional[str],
    streak_months: int,
    cumulative_months: int,
    bits_score: int | None = None,
    bits_rank: int | None = None,
    is_linked: bool | None = None,
) -> None:
    data = load_users()
    discord_id_str = str(discord_id)
    if discord_id_str not in list(data.keys()):
        data[discord_id_str] = {}

    data[discord_id_str]["twitch_username"] = twitch_username
    data[discord_id_str]["tier"] = tier
    data[discord_id_str]["is_subscriber"] = tier is not None
    data[discord_id_str]["streak_months"] = int(streak_months or 0)
    data[discord_id_str]["cumulative_months"] = int(cumulative_months or 0)
    data[discord_id_str]["bits_score"] = (
        int(bits_score or 0) if bits_score is not None else 0
    )
    data[discord_id_str]["bits_rank"] = bits_rank
    data[discord_id_str]["linked_date"] = (
        dt.date.today().isoformat() if is_linked is not None else None
    )

    save_linked_users(data)


def save_all_guild_members(bot):
    data = load_users()

    guild_id = get_guild_id()
    guild = bot.get_guild(guild_id)
    if guild is None:
        return
    keys = list(data.keys())
    for m in guild.members:
        if str(m.id) not in keys and (not m.bot):
            save_linked_user(m.id, None, None, None, None, None, None, None)


def get_taken_json():
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def get_twitch_keys() -> Tuple[str, str, str]:
    """
    token.json からクライアント情報を取得
    NOTE: ユーザー環境では secret キー名が "twitch_secret_key" なので踏襲
    """
    with open(TOKEN_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return (
        data["twitch_client_id"],
        data["twitch_secret_key"],
        data["twitch_redirect_uri"],
    )


def get_broadcast_id() -> str:
    """ブロードキャスター（配信者）の user_id を返す"""
    with open(TOKEN_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return str(data["twitch_id"])  # 既存キーを踏襲


def get_broadcaster_oauth() -> Tuple[str, str]:
    """
    ブロードキャスター用のアクセストークンと user_id を返す
    例:
    {
        "twitch_access_token": "...",        # broadcaster token
        "twitch_id": "12345678"              # broadcaster user_id
    }
    """
    with open(TOKEN_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["twitch_access_token"], str(data["twitch_id"])


__all__ = [
    name
    for name, obj in globals().items()
    if not name.startswith("_")
    and getattr(obj, "__module__", None) == __name__
    and (_inspect.isfunction(obj))
]
