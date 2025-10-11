from typing import Dict, Any, Optional, Tuple
from collections.abc import Mapping
import os
import json
import datetime as dt
import inspect as _inspect
import sqlite3

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
DATA_DIR = os.path.join(PROJECT_ROOT, "venv")
LEGACY_USERS_FILE = os.path.join(DATA_DIR, "all_users.json")
TOKEN_FILE = os.path.join(DATA_DIR, "token.json")
ROLE_FILE = os.path.join(DATA_DIR, "role_id.json")
CHANNEL_FILE = os.path.join(DATA_DIR, "channel_id.json")
CATEGORY_FILE = os.path.join(DATA_DIR, "category_id.json")
ROLE_CONFIG_FILE = os.path.join(DATA_DIR, "subscription_config.json")
GUILD_STATE_FILE = os.path.join(DATA_DIR, "guild_state.json")
LEGACY_GUILD_STATE_FILES = {
    "roles": ROLE_FILE,
    "channels": CHANNEL_FILE,
    "categories": CATEGORY_FILE,
}
DB_PATH = os.path.join(PROJECT_ROOT, "db.sqlite3")
JST = dt.timezone(dt.timedelta(hours=9))

# ---- SQLite tables ----
LINKED_USERS_TABLE = "linked_users"
INBOX_TABLE = "webhook_events"
CHEER_TABLE = "cheer_events"


def _db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
    except Exception:
        pass
    return conn


def _db_init(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {LINKED_USERS_TABLE} (
            discord_id TEXT PRIMARY KEY,
            data       TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {INBOX_TABLE} (
            source       TEXT NOT NULL,
            delivery_id  TEXT NOT NULL,
            event_type   TEXT NOT NULL,
            twitch_user_id TEXT,
            payload      TEXT NOT NULL,
            headers      TEXT,
            status       TEXT NOT NULL DEFAULT 'pending',
            retries      INTEGER NOT NULL DEFAULT 0,
            error        TEXT,
            received_at  TEXT NOT NULL,
            processed_at TEXT,
            PRIMARY KEY (source, delivery_id)
        );
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {CHEER_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            twitch_user_id TEXT,
            bits INTEGER NOT NULL,
            is_anonymous INTEGER NOT NULL,
            message TEXT,
            payload TEXT NOT NULL,
            cheer_at TEXT NOT NULL
        );
        """
    )
    conn.commit()


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _db_rowcount(conn: sqlite3.Connection) -> int:
    cur = conn.execute(f"SELECT COUNT(1) FROM {LINKED_USERS_TABLE}")
    (cnt,) = cur.fetchone()
    return int(cnt or 0)


def _db_upsert_user(discord_id: str, payload: Dict[str, Any]) -> None:
    conn = _db_connect()
    try:
        _db_init(conn)
        now = _now_iso()
        try:
            payload_json = json.dumps(payload, ensure_ascii=False, default=str)
        except Exception:
            payload_json = json.dumps(str(payload), ensure_ascii=False)
        with conn:
            conn.execute(
                f"""
                INSERT INTO {LINKED_USERS_TABLE} (discord_id, data, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(discord_id) DO UPDATE SET
                    data=excluded.data,
                    updated_at=excluded.updated_at
                """,
                (str(discord_id), payload_json, now, now),
            )
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _db_upsert_users(data: Dict[str, Any]) -> None:
    if not isinstance(data, dict):
        return
    conn = _db_connect()
    try:
        _db_init(conn)
        now = _now_iso()
        keys = list(map(str, data.keys()))
        with conn:
            for did in keys:
                payload = data.get(did)
                try:
                    payload_json = json.dumps(payload, ensure_ascii=False, default=str)
                except Exception:
                    payload_json = json.dumps(str(payload), ensure_ascii=False)
                conn.execute(
                    f"""
                    INSERT INTO {LINKED_USERS_TABLE} (discord_id, data, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(discord_id) DO UPDATE SET
                        data=excluded.data,
                        updated_at=excluded.updated_at
                    """,
                    (str(did), payload_json, now, now),
                )
            # delete rows missing from dict (match legacy overwrite semantics)
            cur = conn.execute(f"SELECT discord_id FROM {LINKED_USERS_TABLE}")
            existing = {r[0] for r in cur.fetchall()}
            to_del = [x for x in existing if x not in keys]
            if to_del:
                conn.executemany(
                    f"DELETE FROM {LINKED_USERS_TABLE} WHERE discord_id = ?",
                    [(x,) for x in to_del],
                )
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _db_get_user(discord_id: str) -> Optional[Dict[str, Any]]:
    conn = _db_connect()
    try:
        _db_init(conn)
        cur = conn.execute(
            f"SELECT data FROM {LINKED_USERS_TABLE} WHERE discord_id = ?",
            (str(discord_id),),
        )
        row = cur.fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0] or "{}")
        except Exception:
            return {}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _db_delete_user(discord_id: str) -> None:
    conn = _db_connect()
    try:
        _db_init(conn)
        with conn:
            conn.execute(
                f"DELETE FROM {LINKED_USERS_TABLE} WHERE discord_id = ?",
                (str(discord_id),),
            )
    finally:
        try:
            conn.close()
        except Exception:
            pass


def load_file(FILE_NAME):
    if not os.path.exists(FILE_NAME):
        return {}
    with open(FILE_NAME, "r", encoding="utf-8") as f:
        content = f.read().strip()
        if not content:
            return {}
        return json.loads(content)


def save_file(data, FILE_NAME) -> None:
    os.makedirs(os.path.dirname(FILE_NAME), exist_ok=True)
    with open(FILE_NAME, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False, default=str)


def _coerce_mapping(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _load_guild_state() -> Dict[str, Any]:
    data = load_file(GUILD_STATE_FILE)
    if not isinstance(data, dict):
        data = {}
    migrated = False
    for section, legacy_path in LEGACY_GUILD_STATE_FILES.items():
        section_data = data.get(section)
        if not isinstance(section_data, dict):
            section_data = {}
        if not section_data and os.path.exists(legacy_path):
            legacy_data = load_file(legacy_path)
            if isinstance(legacy_data, dict) and legacy_data:
                section_data = legacy_data
                migrated = True
        data[section] = section_data
    if migrated:
        save_file(data, GUILD_STATE_FILE)
    return data


def _save_guild_state(guild_state: Dict[str, Any]) -> None:
    payload: Dict[str, Any] = {}
    for key, value in guild_state.items():
        payload[key] = _coerce_mapping(value)
    save_file(payload, GUILD_STATE_FILE)


def load_role_ids() -> Dict[str, Any]:
    state = _load_guild_state()
    roles = state.get("roles")
    return _coerce_mapping(roles)


def save_role_ids(data: Dict[str, Any]) -> None:
    state = _load_guild_state()
    state["roles"] = _coerce_mapping(data)
    _save_guild_state(state)


def load_channel_ids() -> Dict[str, Any]:
    state = _load_guild_state()
    channels = state.get("channels")
    return _coerce_mapping(channels)


def save_channel_ids(data: Dict[str, Any]) -> None:
    state = _load_guild_state()
    state["channels"] = _coerce_mapping(data)
    _save_guild_state(state)


def load_subscription_config() -> Dict[str, Any]:
    try:
        data = load_file(ROLE_CONFIG_FILE)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_subscription_config(data: Dict[str, Any]) -> None:
    save_file(data or {}, ROLE_CONFIG_FILE)


def record_cheer_event(
    *,
    twitch_user_id: str | None,
    bits: int,
    is_anonymous: bool,
    message: str | None,
    payload: dict,
    cheer_at: str | None,
) -> None:
    if not isinstance(bits, int) or bits <= 0:
        return
    conn = _db_connect()
    try:
        _db_init(conn)
        p_json = json.dumps(payload or {}, ensure_ascii=False, default=str)
        ts = cheer_at or _now_iso()
        with conn:
            conn.execute(
                f"""
                INSERT INTO {CHEER_TABLE}
                    (twitch_user_id, bits, is_anonymous, message, payload, cheer_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(twitch_user_id) if twitch_user_id is not None else None,
                    int(bits),
                    1 if is_anonymous else 0,
                    message,
                    p_json,
                    ts,
                ),
            )
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _db_load_all_users() -> Dict[str, Any]:
    conn = _db_connect()
    try:
        _db_init(conn)
        # migrate legacy file once
        try:
            if _db_rowcount(conn) == 0 and os.path.exists(LEGACY_USERS_FILE):
                legacy = load_file(LEGACY_USERS_FILE)
                if isinstance(legacy, dict) and legacy:
                    _db_upsert_users(legacy)
        except Exception:
            pass
        cur = conn.execute(f"SELECT discord_id, data FROM {LINKED_USERS_TABLE}")
        res: Dict[str, Any] = {}
        for did, data_json in cur.fetchall():
            try:
                res[str(did)] = json.loads(data_json or "{}")
            except Exception:
                res[str(did)] = {}
        return res
    finally:
        try:
            conn.close()
        except Exception:
            pass


def load_users() -> Dict[str, Any]:
    """Return all linked users from DB (migrates from legacy JSON once if needed)."""
    try:
        return _db_load_all_users()
    except Exception:
        return {}


def save_linked_users(data: Dict[str, Any]) -> None:
    """Persist entire users map to DB."""
    try:
        _db_upsert_users(data)
    except Exception:
        pass


def load_subscription_categories() -> Dict[str, Any]:
    state = _load_guild_state()
    categories = state.get("categories")
    return _coerce_mapping(categories)


def save_subscription_categories(data: Dict[str, Any]) -> None:
    state = _load_guild_state()
    state["categories"] = _coerce_mapping(data)
    _save_guild_state(state)


def get_guild_id():
    with open(TOKEN_FILE, "r", encoding="utf-8") as f:
        GUILD_ID = json.load(f)["guild_id"]
    return GUILD_ID


# linked_users table helper
# initialize missing entries when needed
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
    if not isinstance(data, dict):
        data = {}

    guild_id = get_guild_id()
    try:
        guild_ref = int(guild_id)
    except (TypeError, ValueError):
        guild_ref = guild_id
    guild = bot.get_guild(guild_ref)
    if guild is None:
        return

    def _clean(value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    dirty = False

    for member in guild.members:
        if getattr(member, "bot", False):
            continue

        discord_id = str(member.id)
        existing = data.get(discord_id)
        is_new_entry = not isinstance(existing, dict)
        if is_new_entry:
            existing = {}
        data[discord_id] = existing

        changed = is_new_entry

        def _set_field(key: str, value: Optional[str]) -> None:
            nonlocal changed
            if value is None:
                return
            if existing.get(key) != value:
                existing[key] = value
                changed = True

        username = _clean(getattr(member, "name", None))
        global_name = _clean(getattr(member, "global_name", None))
        nickname = _clean(getattr(member, "nick", None))
        display_name = _clean(getattr(member, "display_name", None))
        if not display_name:
            display_name = nickname or global_name or username
        discriminator = _clean(getattr(member, "discriminator", None))

        _set_field("discord_display_name", display_name)
        _set_field("discord_username", username)
        _set_field("discord_global_name", global_name)
        _set_field("discord_discriminator", discriminator)
        _set_field("discord_nickname", nickname)

        profile_existing = existing.get("discord_profile")
        if not isinstance(profile_existing, dict):
            profile_existing = {}
        profile_candidate = dict(profile_existing)
        profile_changed = False

        def _set_profile(key: str, value: Optional[str]) -> None:
            nonlocal profile_changed
            if value is None:
                return
            if profile_candidate.get(key) != value:
                profile_candidate[key] = value
                profile_changed = True

        _set_profile("id", discord_id)
        _set_profile("username", username)
        _set_profile("display_name", display_name)
        _set_profile("global_name", global_name)
        _set_profile("discriminator", discriminator)
        _set_profile("nickname", nickname)

        avatar_url = None
        try:
            avatar_url = getattr(getattr(member, "display_avatar", None), "url", None)
        except Exception:
            avatar_url = None
        _set_profile("avatar_url", _clean(avatar_url))
        _set_profile("mention", _clean(getattr(member, "mention", None)))

        if profile_changed:
            existing["discord_profile"] = profile_candidate
            changed = True

        # 連携済みユーザーは resolved を自動的に維持
        if existing.get("twitch_user_id"):
            if not existing.get("resolved"):
                existing["resolved"] = True
                changed = True
            if existing.get("roles_revoked"):
                existing["roles_revoked"] = False
                existing["roles_revoked_at"] = None
                changed = True

        if changed:
            dirty = True
            try:
                _db_upsert_user(discord_id, existing)
            except Exception:
                pass

def get_taken_json():
    try:
        return _db_load_all_users()
    except Exception:
        return {}


def get_linked_user(discord_id: str) -> Dict[str, Any]:
    try:
        obj = _db_get_user(discord_id)
        return obj or {}
    except Exception:
        return {}


def delete_linked_user(discord_id: str) -> None:
    try:
        _db_delete_user(discord_id)
    except Exception:
        pass


def ensure_user_entry(discord_id: str) -> None:
    try:
        if _db_get_user(discord_id) is None:
            _db_upsert_user(discord_id, {})
    except Exception:
        pass


def patch_linked_user(
    discord_id: str, updates: Dict[str, Any], *, include_none: bool = False
) -> Dict[str, Any]:
    did = str(discord_id)
    try:
        current = _db_get_user(did) or {}
    except Exception:
        current = {}
    if not isinstance(current, dict):
        current = {}
    for k, v in (updates or {}).items():
        if v is None and not include_none:
            continue
        current[k] = v
    try:
        _db_upsert_user(did, current)
    except Exception:
        pass
    return current


# ---- Inbox helpers ----
def inbox_enqueue_event(
    *,
    source: str,
    delivery_id: str,
    event_type: str,
    twitch_user_id: str | None,
    payload: dict,
    headers: dict | None,
    status: str = "pending",
) -> None:
    conn = _db_connect()
    try:
        _db_init(conn)
        now = _now_iso()
        p_json = json.dumps(payload, ensure_ascii=False, default=str)
        h_json = json.dumps(headers or {}, ensure_ascii=False, default=str)
        with conn:
            conn.execute(
                f"""
                INSERT INTO {INBOX_TABLE}
                (source, delivery_id, event_type, twitch_user_id, payload, headers, status, received_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, delivery_id) DO UPDATE SET
                    event_type=excluded.event_type,
                    twitch_user_id=excluded.twitch_user_id,
                    payload=excluded.payload,
                    headers=excluded.headers,
                    status=excluded.status
                """,
                (
                    source,
                    delivery_id,
                    event_type,
                    str(twitch_user_id) if twitch_user_id is not None else None,
                    p_json,
                    h_json,
                    status,
                    now,
                ),
            )
    finally:
        try:
            conn.close()
        except Exception:
            pass


def inbox_mark_processed(
    source: str, delivery_id: str, *, ok: bool, error: str | None = None
) -> None:
    conn = _db_connect()
    try:
        _db_init(conn)
        now = _now_iso()
        with conn:
            conn.execute(
                f"UPDATE {INBOX_TABLE} SET status=?, processed_at=?, error=?, retries=(CASE WHEN ? THEN retries+1 ELSE retries END) WHERE source=? AND delivery_id=?",
                (
                    "done" if ok else "failed",
                    now,
                    None if ok else (error or "unknown error"),
                    0 if ok else 1,
                    source,
                    delivery_id,
                ),
            )
    finally:
        try:
            conn.close()
        except Exception:
            pass


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


def get_eventsub_config() -> Tuple[str, str]:
    """
    EventSub 用の (callback_url, secret) を返す。
    既存キーを流用:
      - callback_url: token.json の "twitch_redirect_uri" のベースURLを使い、パスを "/twitch_eventsub" に置換
      - secret: token.json の "twitch_secret_key" をそのまま使用

    環境変数での上書きも可:
      - TWITCH_EVENTSUB_CALLBACK, TWITCH_EVENTSUB_SECRET
    """
    # env override（任意）
    env_cb = os.getenv("TWITCH_EVENTSUB_CALLBACK")
    env_secret = os.getenv("TWITCH_EVENTSUB_SECRET")

    with open(TOKEN_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    # secret は client secret を流用（ユーザーの要望に従う）
    secret = env_secret or data.get("twitch_secret_key")

    # redirect_uri からホストを流用し、パスだけ /twitch_eventsub にする
    from urllib.parse import urlparse, urlunparse

    redirect_uri = data.get("twitch_redirect_uri")
    if env_cb:
        callback = env_cb
    else:
        if not redirect_uri:
            raise RuntimeError("twitch_redirect_uri missing in token.json")
        parsed = urlparse(redirect_uri)
        # 絶対URLであることを期待
        callback = urlunparse(
            (parsed.scheme, parsed.netloc, "/twitch_eventsub", "", "", "")
        )

    if not (callback and secret):
        raise RuntimeError("EventSub config missing: callback or secret not set")

    return callback, secret


def get_admin_api_token() -> Optional[str]:
    """Read admin API token from token.json (key: "admin_api_token")."""
    if not os.path.exists(TOKEN_FILE):
        return None
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        token = data.get("admin_api_token")
        if token is None:
            return None
        token_str = str(token).strip()
        return token_str or None
    except Exception:
        return None


__all__ = [
    name
    for name, obj in globals().items()
    if not name.startswith("_")
    and getattr(obj, "__module__", None) == __name__
    and (_inspect.isfunction(obj))
]
