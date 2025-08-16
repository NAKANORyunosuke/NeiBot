import json
import os
import urllib.parse
import datetime
import httpx
from typing import Any, Dict, Optional, Tuple
import asyncio

# ==================== パス設定（絶対パス） ====================

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
TOKEN_PATH = os.path.join(PROJECT_ROOT, "venv", "token.json")
LINKED_USERS_FILE = os.path.join(PROJECT_ROOT, "venv", "linked_users.json")

API_BASE = "https://api.twitch.tv/helix"

# リトライ設定
HTTP_TIMEOUT = 10.0            # 秒
MAX_RETRIES = 3
BACKOFF_BASE = 0.5             # 秒（指数バックオフの初期値）


async def _print_json_response(resp: httpx.Response, label: str = ""):
    """テスト用: HTTPレスポンスのJSONを整形して表示"""
    try:
        data = resp.json()
    except Exception as e:
        print(f"[{label}] JSON decode error: {e!r}")
        print(resp.text)
        return
    print(f"===== {label} JSON =====")
    print(json.dumps(data, indent=4, ensure_ascii=False))
    print("=" * 40)


# ==================== 認証情報取得 ====================
def get_twitch_keys() -> Tuple[str, str, str]:
    """
    token.json からクライアント情報を取得
    NOTE: ユーザー環境では secret キー名が "twitch_seqret_key" なので踏襲
    """
    with open(TOKEN_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["twitch_client_id"], data["twitch_seqret_key"], data["twitch_redirect_uri"]


def get_broadcast_id() -> str:
    """ブロードキャスター（配信者）の user_id を返す"""
    with open(TOKEN_PATH, "r", encoding="utf-8") as f:
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
    with open(TOKEN_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["twitch_access_token"], str(data["twitch_id"])


# ==================== OAuth URL生成 ====================
def get_auth_url(discord_user_id: str) -> str:
    client_id, _, redirect_uri = get_twitch_keys()
    base = "https://id.twitch.tv/oauth2/authorize"
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "user:read:subscriptions",  # 視聴者が自分のサブ情報を配信者に対して開示
        "state": discord_user_id,
    }
    return f"{base}?{urllib.parse.urlencode(params)}"


# ==================== JSON読み書き ====================

def load_linked_users() -> Dict[str, Any]:
    if not os.path.exists(LINKED_USERS_FILE):
        return {}
    with open(LINKED_USERS_FILE, "r", encoding="utf-8") as f:
        content = f.read().strip()
        if not content:
            return {}
        return json.loads(content)


def save_linked_users(data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(LINKED_USERS_FILE), exist_ok=True)
    with open(LINKED_USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False, default=str)


def save_linked_user(
    discord_id: str,
    twitch_username: str,
    tier: Optional[str],
    streak_months: int,
    cumulative_months: int,
    bits_score: int | None = None,
    bits_rank: int | None = None,
) -> None:
    data = load_linked_users()
    dt = datetime.date.today()

    data[discord_id] = {
        "twitch_username": twitch_username,
        "tier": tier,  # "1000"/"2000"/"3000" or None
        "is_subscriber": tier is not None,
        "streak_months": int(streak_months or 0),
        "cumulative_months": int(cumulative_months or 0),
        "bits_score": int(bits_score or 0) if bits_score is not None else 0,
        "bits_rank": bits_rank,
        "linked_date": dt.isoformat(),
    }
    save_linked_users(data)


# ==================== 内部ユーティリティ（共通クライアント / リクエスト） ====================
def _viewer_headers(viewer_access_token: str, client_id: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {viewer_access_token}",
        "Client-Id": client_id,
    }


def _broadcaster_headers() -> Dict[str, str]:
    client_id, _, _ = get_twitch_keys()
    broadcaster_token, _ = get_broadcaster_oauth()
    return {
        "Authorization": f"Bearer {broadcaster_token}",
        "Client-Id": client_id,
    }


def _new_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=HTTP_TIMEOUT)


async def _request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: Dict[str, str] | None = None,
    params: Dict[str, Any] | None = None,
    data: Dict[str, Any] | None = None,
) -> httpx.Response:
    """
    429/5xx を指数バックオフで再試行して Response を返す。
    呼び出し側で r.json() / r.raise_for_status() を行う想定。
    """
    attempt = 0
    backoff = BACKOFF_BASE
    while True:
        try:
            r = await client.request(method, url, headers=headers, params=params, data=data)
            # 429 or 5xx のときだけリトライ（それ以外は返す）
            if r.status_code in (429,) or 500 <= r.status_code < 600:
                attempt += 1
                if attempt >= MAX_RETRIES:
                    return r
                retry_after = r.headers.get("Retry-After")
                sleep_sec = float(retry_after) if retry_after else backoff
                await asyncio.sleep(sleep_sec)
                backoff *= 2
                continue
            return r
        except httpx.HTTPError as e:
            attempt += 1
            if attempt >= MAX_RETRIES:
                raise
            await asyncio.sleep(backoff)
            backoff *= 2


# ==================== API呼び出し ====================

async def _get_me_and_login(client: httpx.AsyncClient, headers: Dict[str, str]) -> Tuple[str, str]:
    """ /users で自分の id と login を取得 """
    r = await _request_json(client, "GET", f"{API_BASE}/users", headers=headers)
    print("[DEBUG] /users status:", r.status_code)
    try:
        print("[DEBUG] /users body:", r.text)
    except Exception:
        pass
    r.raise_for_status()
    data = r.json().get("data", [])
    if not data:
        raise RuntimeError("Twitch /users returned empty data")
    me = data[0]
    return me["id"], me["login"]


async def _get_user_subscription_to_broadcaster(
    client: httpx.AsyncClient,
    headers: Dict[str, str],
    broadcaster_id: str,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    """
    /subscriptions/user で、視聴者(user_id) → 配信者(broadcaster_id) のサブ情報を取得
    必要スコープ: user:read:subscriptions（viewer token）
    """
    params = {"broadcaster_id": broadcaster_id, "user_id": user_id}
    r = await _request_json(client, "GET", f"{API_BASE}/subscriptions/user", headers=headers, params=params)
    print("[DEBUG] /subscriptions/user status:", r.status_code)
    print("[DEBUG] /subscriptions/user body:", r.text)
    await _print_json_response(r, "/users")
    if r.status_code == 404:
        # 「対象なし」パターン
        return None

    r.raise_for_status()
    data = r.json().get("data", [])
    return data[0] if data else None


async def _get_bits_leaderboard_for_user(
    client: httpx.AsyncClient,
    user_id: str,
) -> Tuple[Optional[int], int]:
    """
    /bits/leaderboard で対象ユーザーの現在のスコアと順位を取得。
    必要スコープ: bits:read（broadcaster token）
    備考: user_id を指定すればトップ外でも対象ユーザーの行が返る。
    """
    headers = _broadcaster_headers()
    params = {
        "count": 100,
        "period": "all",
        "user_id": user_id,
    }
    r = await _request_json(client, "GET", f"{API_BASE}/bits/leaderboard", headers=headers, params=params)
    print("[DEBUG] /bits/leaderboard status:", r.status_code)
    print("[DEBUG] /bits/leaderboard body:", r.text)

    # 401（トークン失効/スコープ不足）などは呼び出し元で raise したいのでここで raise_for_status する
    if r.status_code == 404:
        return None, 0

    r.raise_for_status()
    data = r.json().get("data", [])
    if not data:
        return None, 0

    entry = data[0]
    return entry.get("rank"), entry.get("score", 0) or 0


# ==================== 公開関数：ユーザー情報 + サブ情報 + Bits ====================

async def get_user_info_and_subscription(
    viewer_access_token: str,
    client_id: str,
    broadcaster_id: str,
) -> dict:
    """
    視聴者のTwitchアカウントに関する情報を取得（dict返し）
    返り値例:
    {
        "twitch_username": str,
        "twitch_user_id": str,
        "tier": "1000"|"2000"|"3000"|None,
        "streak_months": int,
        "cumulative_months": int,
        "bits_rank": Optional[int],
        "bits_score": int,
        "is_subscriber": bool,
    }
    """
    async with _new_client() as client:
        # 1) 視聴者の id / login を取得
        headers_viewer = _viewer_headers(viewer_access_token, client_id)
        user_id, user_login = await _get_me_and_login(client, headers_viewer)

        # 2) 視聴者→配信者に対するサブ情報
        sub = await _get_user_subscription_to_broadcaster(
            client, headers_viewer, broadcaster_id, user_id
        )

        # デフォルト値（非サブスクでもここから初期化）
        result: Dict[str, Any] = {
            "twitch_username": user_login,
            "twitch_user_id": user_id,
            "tier": None,
            "streak_months": 0,
            "cumulative_months": 0,
            "bits_rank": None,
            "bits_score": 0,
            "is_subscriber": False,
        }

        if sub:
            # Helix の揺れに耐える
            result["tier"] = sub.get("tier")
            result["streak_months"] = int(sub.get("streak_months") or sub.get("streak") or 0)
            result["cumulative_months"] = int(sub.get("cumulative_months") or 0)
            result["is_subscriber"] = True

        # 3) Bits情報（broadcaster token）
        try:
            bits_rank, bits_score = await _get_bits_leaderboard_for_user(client, user_id)
            result["bits_rank"] = bits_rank
            result["bits_score"] = int(bits_score or 0)
        except httpx.HTTPStatusError as e:
            # スコープ不足やトークン失効などの場合はログだけ出して0扱いに
            print(f"[WARN] bits leaderboard fetch failed: {e.response.status_code} {e.response.text}")
        except httpx.HTTPError as e:
            print(f"[WARN] bits leaderboard fetch http error: {e!r}")

        return result
