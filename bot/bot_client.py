import asyncio
import json
import threading
from typing import Coroutine, Any
import zoneinfo
import datetime
from bot.utils.twitch import load_linked_users, save_linked_users, get_auth_url
import discord
from discord.ext import commands, tasks
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import uvicorn
import httpx
import os
from bot.utils.streak import reconcile_and_save_link
from bot.utils.twitch import (
    get_twitch_keys,
    get_user_info_and_subscription,
    save_linked_user,
    get_broadcast_id,
)


# ==================== パス設定（絶対パス） ====================

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "./"))
TOKEN_PATH = os.path.join(PROJECT_ROOT, "venv", "token.json")
LINKED_USERS_FILE = os.path.join(PROJECT_ROOT, "venv", "linked_users.json")


# ===== Discord Bot の準備 =====
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)
JST = zoneinfo.ZoneInfo("Asia/Tokyo")

# ===== FastAPI アプリ =====
app = FastAPI()


# ---- Bot ループにコルーチンを投げる小ヘルパ ----
def run_in_bot_loop(coro: Coroutine[Any, Any, Any]):
    """Discord Bot のイベントループで coro を実行して、例外をログに出す"""
    fut = asyncio.run_coroutine_threadsafe(coro, bot.loop)
    def _done(f):
        try:
            f.result()
        except Exception as e:
            print("❌ notify error:", repr(e))
    fut.add_done_callback(_done)
    return fut


# ---- Bot側で実際に送信する処理（Botのループ上で動く）----
async def notify_discord_user(discord_id: int, twitch_name: str, tier: str, streak: int | None = None):
    await bot.wait_until_ready()
    user = await bot.fetch_user(discord_id)
    if not user:
        print(f"⚠ fetch_user({discord_id}) が None")
        return
    msg = f"✅ Twitch `{twitch_name}` とリンクしました！Tier: {tier}"
    if streak is not None:
        msg += f", Streak: {streak}"
    await user.send(msg)


# ---- API: 直接Discordに通知する（外部/内部から叩ける）----
@app.post("/notify_link")
async def notify_link(discord_id: int, twitch_name: str, tier: str):
    run_in_bot_loop(notify_discord_user(discord_id, twitch_name, tier))
    return {"status": "queued"}


# ---- API: Twitch OAuth コールバック ----

# 既存ユーティリティ想定:
# - get_twitch_keys() -> (client_id, client_secret, redirect_uri)
# - get_broadcast_id() -> broadcaster_id(str or int)
# - get_user_info_and_subscription(viewer_access_token, client_id, broadcaster_id) -> dict
#   返り値例:
#   {
#     "twitch_username": str,
#     "twitch_user_id": str,
#     "tier": "1000"|"2000"|"3000"|None,
#     "streak_months": int,
#     "cumulative_months": int,
#     "bits_rank": Optional[int],
#     "bits_score": int,
#     "is_subscriber": bool,
#   }
# - save_linked_user(...) は旧版(引数: discord_id, twitch_username, tier, streak) or
#                         新版(引数: discord_id, twitch_username, tier, streak_months, cumulative_months, bits_score, bits_rank)
# - run_in_bot_loop(coro) / notify_discord_user(user_id:int, name:str, tier, streak)

@app.get("/twitch_callback")
async def twitch_callback(request: Request):
    print("✅ [twitch_callback] にアクセスがありました")
    code = request.query_params.get("code")
    state = request.query_params.get("state")  # DiscordのユーザーID（str）

    if not code or not state:
        return PlainTextResponse("Missing code or state", status_code=400)

    # 1) Twitch クレデンシャル
    try:
        client_id, client_secret, redirect_uri = get_twitch_keys()
    except Exception as e:
        return PlainTextResponse(f"Failed to read credentials: {e!r}", status_code=500)

    # 2) アクセストークン取得（非同期 httpx）
    token_url = "https://id.twitch.tv/oauth2/token"
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(token_url, data=payload, headers=headers)
    except httpx.HTTPError as e:
        return PlainTextResponse(f"Token request failed: {e!r}", status_code=502)

    if resp.status_code != 200:
        return PlainTextResponse(f"Failed to get token: {resp.text}", status_code=502)

    data = resp.json()
    access_token = data.get("access_token")
    if not access_token:
        return PlainTextResponse("Access token not found", status_code=502)

    # 3) broadcaster_id を解決
    try:
        broadcaster_id_raw = get_broadcast_id()
        BROADCASTER_ID = str(broadcaster_id_raw)
        print(f"[DEBUG] get_broadcast_id -> {BROADCASTER_ID!r}")
    except Exception as e:
        return PlainTextResponse(f"Failed to resolve broadcaster_id: {e!r}", status_code=500)

    # 4) ユーザー情報 & サブスク情報（dict 返り値）
    try:
        info = await get_user_info_and_subscription(
            viewer_access_token=access_token,
            client_id=client_id,
            broadcaster_id=BROADCASTER_ID,
        )
    except httpx.HTTPError as e:
        return PlainTextResponse(f"Helix request failed: {e!r}", status_code=502)
    except Exception as e:
        return PlainTextResponse(f"Failed to fetch user/sub info: {e!r}", status_code=500)

    twitch_user_name = info.get("twitch_username")
    if not twitch_user_name:
        return PlainTextResponse("Failed to get Twitch user info", status_code=502)

    # 5) リンク情報を保存（streak自前更新版）
    try:
        rec = reconcile_and_save_link(str(state), info)
    except Exception as e:
        print(f"❌ reconcile_and_save_link failed: {e!r}")
        rec = info  # 万一失敗したら元のinfoを使う

    # 6) Discord通知
    try:
        print("notify_discord_user の呼び出し")
        run_in_bot_loop(
            notify_discord_user(
                int(state),
                rec.get("twitch_username"),
                rec.get("tier"),
                rec.get("streak_months", 0),
            )
        )
    except Exception as e:
        print("❌ failed to schedule notify:", repr(e))

    return PlainTextResponse("Notified in background", status_code=200)



@tasks.loop(time=datetime.time(hour=0, minute=5, tzinfo=JST))
async def monthly_relink_sweeper():
    """毎日0:05(JST)に起動。月初1日のみ、再リンクフラグ付け＆DM通知を行う。"""
    await bot.wait_until_ready()
    today = datetime.datetime.now(JST).date()
    if today.day != 1:
        return  # 月初のみ

    # --- 多重実行防止（同月2回目はスキップ） ---
    meta_path = os.path.join(PROJECT_ROOT, "venv", "linked_users_meta.json")
    last_tag = f"{today.year:04d}{today.month:02d}"
    meta = {}
    if os.path.exists(meta_path):
        try:
            meta = json.load(open(meta_path, "r", encoding="utf-8"))
        except Exception:
            meta = {}
    if meta.get("last_relink_run") == last_tag:
        print("ℹ すでに今月の再リンク処理は完了しています。スキップ")
        return

    data = load_linked_users()
    if not data:
        print("ℹ linked_users.json が空/未作成: スキップ")
        # メタだけ更新
        meta["last_relink_run"] = last_tag
        json.dump(meta, open(meta_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        return

    targets = []
    for discord_id, info in data.items():
        if info.get("is_subscriber") is True:
            # すでにフラグが立っている人は二重に立てない
            if not info.get("relink_required"):
                # 前回スナップショットを保存（最小限でOK）
                info["prev_snapshot"] = {
                    "linked_date": info.get("linked_date"),
                    "streak_months": int(info.get("streak_months", 0) or 0),
                    "cumulative_months": int(info.get("cumulative_months", 0) or 0),
                    "tier": info.get("tier"),
                    "is_subscriber": bool(info.get("is_subscriber", False)),
                }
                info["relink_required"] = True
                data[discord_id] = info
                targets.append(discord_id)

    if not targets:
        print("ℹ 月初の再リンク対象なし（全員非サブ or 既にフラグ済み）")
        # メタ更新
        meta["last_relink_run"] = last_tag
        json.dump(meta, open(meta_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        return

    # 先に保存（クラッシュしてもフラグは残る）
    save_linked_users(data)
    print(f"🧹 月初再リンク: {len(targets)} 件にフラグ付与しました（prev_snapshot保持）")

    # DM送信
    for did in targets:
        try:
            user = await bot.fetch_user(int(did))
            auth_url = get_auth_url(str(did))  # state=discord_id
            msg = (
                "📅 毎月初めの再認証のお願い\n"
                "サブスク状況の確認のため、もう一度リンクをお願いします。\n"
                f"{auth_url}\n\n"
                "※ リンク後は自動でロールが同期されます。"
            )
            await user.send(msg)
            await asyncio.sleep(0.5)  # 送信間隔（必要なら増やす）
        except discord.Forbidden:
            print(f"❌ DM拒否/フレ申請必須のため送れず: {did}")
        except discord.NotFound:
            print(f"❌ ユーザーが見つからない: {did}")
        except Exception as e:
            print(f"❌ DM送信失敗 {did}: {e!r}")

    # メタ更新（“フラグ付けとDM試行”が終わったことを記録）
    meta["last_relink_run"] = last_tag
    json.dump(meta, open(meta_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


@monthly_relink_sweeper.before_loop
async def _before_monthly_relink_sweeper():
    await bot.wait_until_ready()
    print("⏰ monthly_relink_sweeper scheduled (JST 00:05)")


# ===== FastAPI を別スレッドで起動 =====
def start_api():
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")


# ===== Discord Bot を起動 =====
async def run_discord_bot():
    with open("./venv/token.json", "r", encoding="utf-8") as f:
        token = json.load(f)["discord_token"]

    bot.load_extension("bot.cogs.link")
    bot.load_extension("bot.cogs.unlink")

    await bot.start(token)


@bot.event
async def on_ready():
    if not monthly_relink_sweeper.is_running():
        monthly_relink_sweeper.start()
    # すでに daily_tier_sync を start しているならそれはそれでそのまま
    print("✅ monthly_relink_sweeper started")


if __name__ == "__main__":
    # FastAPI を別スレッドで開始（独自ループ）
    threading.Thread(target=start_api, daemon=True).start()

    # Discord Bot はメインスレッドで実行（bot.loop が基準になる）
    asyncio.run(run_discord_bot())
