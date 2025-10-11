import json
import os
import urllib.parse
import httpx
from typing import Any, Dict, Optional, Tuple
import asyncio
from bot.utils.save_and_load import (
    get_twitch_keys,
    get_broadcaster_oauth,
    get_eventsub_config,
)
from bot.common import debug_print

# ==================== パス設定（絶対パス） ====================

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
TOKEN_PATH = os.path.join(PROJECT_ROOT, "venv", "token.json")

API_BASE = "https://api.twitch.tv/helix"

# Bits取得の一時無効化フラグ（401/403検出後は以後スキップ）
_BITS_DISABLED = False

# リトライ設定
HTTP_TIMEOUT = 10.0  # 秒
MAX_RETRIES = 3
BACKOFF_BASE = 0.5  # 秒（指数バックオフの初期値）


async def _print_json_response(resp: httpx.Response, label: str = ""):
    """テスト用: HTTPレスポンスのJSONを整形して表示"""
    try:
        data = resp.json()
    except Exception as e:
        debug_print(f"[{label}] JSON decode error: {e!r}")
        debug_print(resp.text)
        return
    debug_print(f"===== {label} JSON =====")
    debug_print(json.dumps(data, indent=4, ensure_ascii=False))
    debug_print("=" * 40)


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
            r = await client.request(
                method, url, headers=headers, params=params, data=data
            )
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
        except httpx.HTTPError:
            attempt += 1
            if attempt >= MAX_RETRIES:
                raise
            await asyncio.sleep(backoff)
            backoff *= 2


# ==================== API呼び出し ====================


async def _get_me_and_login(
    client: httpx.AsyncClient, headers: Dict[str, str]
) -> Tuple[str, str]:
    """/users で自分の id と login を取得"""
    r = await _request_json(client, "GET", f"{API_BASE}/users", headers=headers)
    debug_print("[DEBUG] /users status:", r.status_code)
    try:
        debug_print("[DEBUG] /users body:", r.text)
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
    r = await _request_json(
        client, "GET", f"{API_BASE}/subscriptions/user", headers=headers, params=params
    )
    debug_print("[DEBUG] /subscriptions/user status:", r.status_code)
    debug_print("[DEBUG] /subscriptions/user body:", r.text)
    await _print_json_response(r, "/users")
    if r.status_code == 404:
        # 「対象なし」パターン
        return None

    r.raise_for_status()
    data = r.json().get("data", [])
    return data[0] if data else None


async def _get_broadcaster_subscription_by_user(
    client: httpx.AsyncClient,
    broadcaster_id: str,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    """
    配信者トークンで /subscriptions を参照し、特定ユーザーの streak/cumulative/開始日を取得
    必要スコープ: channel:read:subscriptions（broadcaster token）
    """
    headers = _broadcaster_headers()
    params = {"broadcaster_id": broadcaster_id, "user_id": user_id}
    r = await _request_json(
        client, "GET", f"{API_BASE}/subscriptions", headers=headers, params=params
    )
    debug_print("[DEBUG] /subscriptions (broadcaster) status:", r.status_code)
    debug_print("[DEBUG] /subscriptions (broadcaster) body:", r.text)
    if r.status_code == 404:
        return None
    # 401/403 はスコープ不足や無効トークンの可能性 → 呼び出し元で握りつぶす
    r.raise_for_status()
    data = r.json().get("data", [])
    if not data:
        return None
    sub = data[0]
    # フィールド名の揺れに耐性
    started_at = (
        sub.get("started_at") or sub.get("start_date") or sub.get("created_at") or None
    )
    return {
        "tier": sub.get("tier"),
        "cumulative_months": sub.get("cumulative_months"),
        "streak_months": sub.get("streak_months") or sub.get("streak"),
        "sub_started_at": started_at,
        "is_gift": sub.get("is_gift"),
    }


async def _get_app_access_token(client: httpx.AsyncClient) -> tuple[str, str]:
    """App Access Token と Client ID を返す。"""
    client_id, client_secret, _ = get_twitch_keys()
    token_url = "https://id.twitch.tv/oauth2/token"
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }
    response = await client.post(
        token_url,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    response.raise_for_status()
    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("Failed to acquire Twitch app access token")
    return token, client_id


async def register_eventsub_subscriptions(
    callback_url: str | None = None, *, client: httpx.AsyncClient | None = None
) -> None:
    """EventSubの購読を作成（subscribe/resub/end）。すでに存在する場合はAPI側で重複を許容。"""
    if callback_url is None:
        cb, _ = get_eventsub_config()
        callback_url = cb

    _, broadcaster_id = get_broadcaster_oauth()
    _, secret = get_eventsub_config()

    payloads = [
        {
            "type": "channel.subscribe",
            "version": "1",
            "condition": {"broadcaster_user_id": broadcaster_id},
            "transport": {
                "method": "webhook",
                "callback": callback_url,
                "secret": secret,
            },
        },
        {
            "type": "channel.subscription.message",
            "version": "1",
            "condition": {"broadcaster_user_id": broadcaster_id},
            "transport": {
                "method": "webhook",
                "callback": callback_url,
                "secret": secret,
            },
        },
        {
            "type": "channel.subscription.end",
            "version": "1",
            "condition": {"broadcaster_user_id": broadcaster_id},
            "transport": {
                "method": "webhook",
                "callback": callback_url,
                "secret": secret,
            },
        },
        {
            "type": "channel.cheer",
            "version": "1",
            "condition": {"broadcaster_user_id": broadcaster_id},
            "transport": {
                "method": "webhook",
                "callback": callback_url,
                "secret": secret,
            },
        },
    ]

    close_client = False
    if client is None:
        client = _new_client()
        close_client = True

    async def _register(c: httpx.AsyncClient) -> None:
        app_token, client_id = await _get_app_access_token(c)
        headers = {
            "Authorization": f"Bearer {app_token}",
            "Client-Id": client_id,
            "Content-Type": "application/json",
        }

        for body in payloads:
            r = await c.post(
                f"{API_BASE}/eventsub/subscriptions",
                headers=headers,
                content=json.dumps(body),
            )
            debug_print("[EventSub] create", body["type"], "status:", r.status_code)
            try:
                debug_print("[EventSub] body:", r.text)
            except Exception:
                pass

    if close_client:
        async with client:
            await _register(client)
    else:
        await _register(client)


async def list_eventsub_subscriptions(
    status: str | None = None, *, client: httpx.AsyncClient | None = None
) -> list[dict[str, Any]]:
    """既存のEventSub購読一覧を取得する。"""
    close_client = False
    if client is None:
        client = _new_client()
        close_client = True

    results: list[dict[str, Any]] = []

    async def _fetch(c: httpx.AsyncClient) -> None:
        app_token, client_id = await _get_app_access_token(c)
        headers = {
            "Authorization": f"Bearer {app_token}",
            "Client-Id": client_id,
        }
        params: dict[str, Any] = {}
        if status:
            params["status"] = status
        cursor: str | None = None
        while True:
            if cursor:
                params["after"] = cursor
            response = await c.get(
                f"{API_BASE}/eventsub/subscriptions",
                headers=headers,
                params=params,
            )
            response.raise_for_status()
            data = response.json()
            items = data.get("data", []) or []
            results.extend(items)
            pagination = data.get("pagination") or {}
            cursor = pagination.get("cursor")
            if not cursor:
                break

    if close_client:
        async with client:
            await _fetch(client)
    else:
        await _fetch(client)
    return results


async def delete_eventsub_subscription(
    subscription_id: str, *, client: httpx.AsyncClient | None = None
) -> int:
    """指定IDのEventSub購読を削除し、ステータスコードを返す。"""
    close_client = False
    if client is None:
        client = _new_client()
        close_client = True

    async def _delete(c: httpx.AsyncClient) -> int:
        app_token, client_id = await _get_app_access_token(c)
        headers = {
            "Authorization": f"Bearer {app_token}",
            "Client-Id": client_id,
        }
        response = await c.delete(
            f"{API_BASE}/eventsub/subscriptions",
            headers=headers,
            params={"id": subscription_id},
        )
        return response.status_code

    if close_client:
        async with client:
            return await _delete(client)
    return await _delete(client)


async def create_eventsub_subscription(
    sub_type: str,
    *,
    version: str = "1",
    condition: dict[str, Any] | None = None,
    callback_url: str | None = None,
    secret: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> tuple[int, dict[str, Any] | str]:
    """任意のEventSub購読を作成するヘルパー。"""
    if callback_url is None or secret is None:
        cb, sec = get_eventsub_config()
        callback_url = callback_url or cb
        secret = secret or sec
    if condition is None:
        _, broadcaster_id = get_broadcaster_oauth()
        condition = {"broadcaster_user_id": broadcaster_id}

    body = {
        "type": sub_type,
        "version": version,
        "condition": condition,
        "transport": {
            "method": "webhook",
            "callback": callback_url,
            "secret": secret,
        },
    }

    close_client = False
    if client is None:
        client = _new_client()
        close_client = True

    async def _create(c: httpx.AsyncClient) -> tuple[int, dict[str, Any] | str]:
        app_token, client_id = await _get_app_access_token(c)
        headers = {
            "Authorization": f"Bearer {app_token}",
            "Client-Id": client_id,
            "Content-Type": "application/json",
        }
        response = await c.post(
            f"{API_BASE}/eventsub/subscriptions",
            headers=headers,
            content=json.dumps(body),
        )
        try:
            payload = response.json()
        except Exception:
            payload = response.text
        return response.status_code, payload

    if close_client:
        async with client:
            return await _create(client)
    return await _create(client)


async def _get_bits_leaderboard_for_user(
    client: httpx.AsyncClient,
    user_id: str,
) -> Tuple[Optional[int], int]:
    """
    /bits/leaderboard で対象ユーザーの現在のスコアと順位を取得。
    必要スコープ: bits:read（broadcaster token）
    備考: user_id を指定すればトップ外でも対象ユーザーの行が返る。
    """
    global _BITS_DISABLED
    if _BITS_DISABLED:
        return None, 0

    headers = _broadcaster_headers()
    params = {
        "count": 100,
        "period": "all",
        "user_id": user_id,
    }
    r = await _request_json(
        client, "GET", f"{API_BASE}/bits/leaderboard", headers=headers, params=params
    )
    debug_print("[DEBUG] /bits/leaderboard status:", r.status_code)
    debug_print("[DEBUG] /bits/leaderboard body:", r.text)

    # 401/403 はトークン失効やスコープ不足の可能性が高い → 以後スキップ
    if r.status_code in (401, 403):
        _BITS_DISABLED = True
        debug_print("[INFO] bits leaderboard disabled due to auth error (401/403).")
        return None, 0
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
            result["streak_months"] = int(
                sub.get("streak_months") or sub.get("streak") or 0
            )
            result["cumulative_months"] = int(sub.get("cumulative_months") or 0)
            result["is_subscriber"] = True

        # 2.5) 配信者視点のサブ情報（streak/cumulative/開始日）で上書き強化
        if result["is_subscriber"]:
            try:
                bsub = await _get_broadcaster_subscription_by_user(
                    client, broadcaster_id, user_id
                )
                if bsub:
                    if bsub.get("tier") is not None:
                        result["tier"] = bsub.get("tier")
                    if bsub.get("cumulative_months") is not None:
                        result["cumulative_months"] = int(
                            bsub.get("cumulative_months") or 0
                        )
                    if bsub.get("streak_months") is not None:
                        result["streak_months"] = int(bsub.get("streak_months") or 0)
                    if bsub.get("sub_started_at"):
                        result["sub_started_at"] = bsub.get("sub_started_at")
            except httpx.HTTPStatusError:
                # スコープ不足などは無視して続行
                pass

        # 3) Bits情報（broadcaster token）
        try:
            bits_rank, bits_score = await _get_bits_leaderboard_for_user(
                client, user_id
            )
            result["bits_rank"] = bits_rank
            result["bits_score"] = int(bits_score or 0)
        except httpx.HTTPStatusError as e:
            # スコープ不足やトークン失効などの場合はログだけ出して0扱いに
            debug_print(
                f"[WARN] bits leaderboard fetch failed: {e.response.status_code} {e.response.text}"
            )
        except httpx.HTTPError as e:
            debug_print(f"[WARN] bits leaderboard fetch http error: {e!r}")

        return result
