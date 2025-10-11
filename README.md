# NeiBot – Twitch サブスク連携 Discord Bot

![Python](https://img.shields.io/badge/Python-3.12-blue)
![py-cord](https://img.shields.io/badge/py--cord-2.6.1-green)

NeiBot は Twitch のサブスクライバー状態を Discord サーバーへ反映し、専用ロールやチャンネル制御、月次リマインド DM を自動化する統合ボットです。Discord ボットと FastAPI Webhook を 1 プロセスで動かし、Django ベースの管理コンソールから運用を行います。

---

## 主な機能
- `/link` による Twitch OAuth 連携と Tier ロール自動付与
- Twitch EventSub (`channel.subscribe`, `channel.subscription.message`, `channel.subscription.end`, `channel.cheer`, `stream.online`) の受信と連動処理
- APScheduler を用いた毎月の再リンクリマインド DM と未解決者のロール剥奪
- 新規参加者への自動 DM 案内、`/unlink` による連携解除
- 管理者向け Django パネルでのダッシュボード、ロール単位の DM 一斉送信、Twitch CSV 取り込み、EventSub 管理
- `scripts/eventsub_local_test.py`・Jupyter Notebook によるローカル検証

---

## アーキテクチャ概要
- **Discord Bot** (`bot/bot_client.py`, `bot/cogs/`)
  - py-cord 2.6.1 で実装。Intents は `Intents.all()` を使用。
  - Slash Command 拡張 (`link`, `unlink`, `monthly_relink_bot`, `auto_link_dm`) と DM 送信／ロール制御を担当。
- **FastAPI** (同 `bot/bot_client.py`)
  - `/twitch_callback` で OAuth コールバックを受け、Helix API からサブスク情報を取得。
  - `/twitch_eventsub` で EventSub 通知を HMAC 検証のうえ反映。管理 API は Bearer 認証。
- **Django 管理コンソール** (`webadmin/`)
  - `RUN_DJANGO=1` でボット起動時に子プロセスとして `webadmin/manage.py runserver 127.0.0.1:8001` を起動。
  - `panel` アプリが `db.sqlite3` の `linked_users` / `webhook_events` を参照し、Web UI で運用操作を提供。
- **ストレージ**
  - `db.sqlite3`: ボットと Django が共有。`linked_users`, `webhook_events`, `cheer_events`。
  - `venv/all_users.json`: 旧形式のバックアップ。初回起動時に `linked_users` テーブルへ自動移行され、その後は更新されない。
  - 補助設定: `venv/token.json`, `role_id.json`, `channel_id.json`, `category_id.json`, `subscription_config.json`。

---

## ディレクトリ
```
bot/                  Discord Bot と FastAPI の実装
  cogs/               Slash Command / イベント拡張
  utils/              Twitch API, データ永続化, EventSub 適用ロジック
webadmin/             Django 管理サイト
scripts/              EventSub ローカルテスト CLI
notebooks/            Webhook 動作確認 Notebook
nginx.conf            本番用リバースプロキシの参考設定 (Let’s Encrypt + nginx)
```

---

## セットアップ
```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### `venv/token.json` を作成
```json
{
  "discord_token": "<Discord Bot Token>",
  "guild_id": 123456789012345678,
  "twitch_client_id": "<Twitch Client ID>",
  "twitch_secret_key": "<Twitch Client Secret>",
  "twitch_redirect_uri": "https://your.domain/twitch_callback",
  "twitch_access_token": "<Broadcaster OAuth Token>",
  "twitch_id": "<Broadcaster User ID>",
  "admin_api_token": "<任意の管理トークン>"
}
```
- Viewer OAuth スコープ: `user:read:subscriptions`
- Broadcaster トークン: `channel:read:subscriptions` (Bits 集計まで行う場合 `bits:read` も付与)

### 任意設定ファイル
- `subscription_config.json`: Tier ごとのロール／カテゴリ／チャンネル名、通知チャンネルをカスタマイズ。
- `role_id.json` / `channel_id.json` / `category_id.json`: 初回起動時に自動生成されるギルド ID マップ。

---

## 起動方法
```powershell
# PowerShell 例
venv\Scripts\activate
$env:DEBUG = "1"             # 詳細ログ (任意)
$env:RUN_DJANGO = "1"        # 管理画面を同時起動 (任意)
python bot/bot_client.py
```
- 環境変数
  - `DEBUG=1` : 詳細ログを標準出力へ
  - `APP_ENV=prod` : FastAPI の `/docs` 等を無効化
  - `TWITCH_EVENTSUB_CALLBACK`, `TWITCH_EVENTSUB_SECRET` : `token.json` を上書き
  - `RUN_DJANGO=1` : Django 管理画面を別スレッドで起動
  - `BOT_ADMIN_API_BASE` : Django から利用する API ベース URL (既定 `http://127.0.0.1:8000`)
  - `ADMIN_API_TOKEN` : Django から送る Bearer トークン (`token.json` と揃える)

---

## 本番運用 (Let’s Encrypt + nginx)
1. Windows Server に Python 3.12 をインストールし、仮想環境へ依存パッケージを導入。
2. Let’s Encrypt (win-acme 等) でドメイン証明書を取得。
3. `nginx.conf` を参考に設定。
   - `listen 80` で HTTPS へリダイレクト。
   - `listen 443 ssl` で `ssl_certificate`, `ssl_certificate_key`, `ssl_trusted_certificate` を Let’s Encrypt の pem へ変更。
   - `/twitch_callback` と `/twitch_eventsub` は 127.0.0.1:8000 (Uvicorn/FastAPI) へプロキシ。
   - `/`, `/panel/`, `/accounts/` は 127.0.0.1:8001 (Django) へプロキシ。
   - `/static/`, `/media/` はローカルディレクトリを直接配信。
   - Rate Limit や悪質リクエストのフィルタを有効化している点に留意。
4. タスクスケジューラ等で `venv\Scripts\activate && python bot/bot_client.py` を常駐実行。

---

## 管理コンソール
- URL: `https://<domain>/panel/`
- 主要機能
  - ダッシュボードで連携済み人数・DM 失敗・Tier 内訳を可視化
  - 未解決ユーザー一覧の CSV エクスポート
  - Twitch の `subscriber-list.csv` をインポートし、`linked_users` を一括更新
  - ロール選択 + テンプレート ( `{user}` ) 付き DM 一斉送信。添付ファイルは最大 8MB / メッセージ 10 個
  - EventSub 購読の確認／追加／削除
- `settings.ADMIN_API_TOKEN` と `BOT_ADMIN_API_BASE` は `.env` などに設定し、FastAPI 側のトークンと一致させる。

---

## テスト / 検証
- **EventSub ローカルテスト**: `python scripts/eventsub_local_test.py --start-server`
  - `--discord-id`, `--twitch-user-id` でテスト対象を指定。
  - HMAC 署名済みの `channel.subscribe` → `message` → `end` を送信。
- **Jupyter Notebook**: `notebooks/NeiBot_EventSub_LocalTests.ipynb`
  - 順番にセルを実行し、`db.sqlite3` の更新を確認。
- **Twitch CLI (任意)**:
  ```powershell
  twitch event configure -F https://<domain>/twitch_eventsub -s "<secret>"
  twitch event trigger channel.subscribe -b <broadcaster_id> -u <user_id> --tier 1000
  ```

---

## トラブルシューティング
- EventSub 登録が 400: HTTPS/443 で公開されているか、ngrok 等を利用している場合 URL を更新済みか確認。
- EventSub 401/403: Broadcaster トークンのスコープ不足。`twitch_access_token` を再発行。
- `/link` 完了後にロールが付かない: Bot のロール位置／権限を確認し、`Twitch-linked` ロールより上位に配置。
- DM が届かない: ユーザーの DM 設定、もしくは `dm_failed` フラグを Django ダッシュボードで確認。
- Bits 情報が常に 0: Broadcaster トークンに `bits:read` を付与して再設定。

---

## ライセンス
本プロジェクトは **非営利利用限定ライセンス (Non-Commercial License)** です。詳細は [`LICENSE`](./LICENSE) を参照してください。
