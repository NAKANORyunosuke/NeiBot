# NeiBot – Twitch サブスク連携 Discord Bot

![Python](https://img.shields.io/badge/Python-3.12-blue)
![py-cord](https://img.shields.io/badge/py--cord-2.6.1-green)

Twitch サブスク状況に応じて Discord 側のロール・チャンネル権限を自動管理する Bot です。/link による OAuth 連携、月次の再リンク DM、Twitch EventSub(Webhook) による再サブ情報の自動反映に対応しています。

---

## 主な機能

- Twitch 連携: `/link` で視聴者 OAuth。配信者トークンで補助情報を取得。
- サブ情報の保存: `streak_months`(連続) / `cumulative_months`(累計) / `subscribed_since`(開始日) / `tier` を保存。
- EventSub(Webhook): `channel.subscribe`/`channel.subscription.message`/`channel.subscription.end` を受信し、自動更新。
- 月次リマインド: 月初に全員へ再リンク DM、7日経過で未解決へ再送。
- ロール管理: 連携ロールと Tier ロールを付与/整理。サブ専用カテゴリー/チャンネルも自動整備。

---

## 構成

- Discord Bot: `bot/bot_client.py`（py-cord）
  - ロードする拡張: `bot.cogs.link`, `bot.cogs.unlink`(任意), `bot.monthly_relink_bot`, `bot.cogs.auto_link_dm`
- FastAPI: `GET /twitch_callback`, `POST /twitch_eventsub`
  - 起動時に EventSub を App Access Token で登録
- スケジューラ: APScheduler（JST）
  - 月初 09:05 初回通知／毎日 09:10 未解決再送

---

## セットアップ

1) 依存インストール

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirement.txt
```

2) `venv/token.json` を作成

```json
{
  "discord_token": "<Discord Bot Token>",
  "guild_id": 123456789012345678,

  "twitch_client_id": "<Twitch Client ID>",
  "twitch_secret_key": "<Twitch Client Secret>",
  "twitch_redirect_uri": "https://your.domain/twitch_callback",

  "twitch_access_token": "<Broadcaster User Access Token>",
  "twitch_id": "<Broadcaster User ID>"
}
```

必須スコープ
- 視聴者 OAuth(リンク用): `user:read:subscriptions`
- 配信者ユーザートークン(補助取得/任意): `channel:read:subscriptions`（Bits を使う場合は `bits:read`）

3) 起動

```bash
python bot/bot_client.py
```

環境変数 `DEBUG=1` で詳細ログを出力します。

---

## EventSub(Webhook) 設定

- コールバック URL: `twitch_redirect_uri` のホストを使い、`/twitch_eventsub` に自動変換（例: `https://your.domain/twitch_eventsub`）
- 署名 secret: `twitch_secret_key` を使用（環境変数で上書き可）
- 要件: HTTPS/標準ポート(443) 必須。ローカル開発では ngrok 等で公開してください。

起動時ログ
- 202: 登録受理
- 409: 既に登録済み（問題なし）
- `webhook_callback_verification` に対して challenge を 200 で返答します。

ngrok 例

```bash
ngrok http 8000
# token.json の twitch_redirect_uri を https://<ngrok>.ngrok-free.app/twitch_callback に変更
# Twitch 開発コンソールの Redirect URL にも同値を登録
```

---

## スラッシュコマンド

- `/link`: Twitch 連携を開始。完了後、Twitch名/サブ状態/Tier/連続・累計・開始日を DM で通知。
- `/force_relink`: 全員へ再リンク DM（テスト）
- `/force_resend`: 「7日経過・未解決」へ再送（テスト）
- `/relink_status`: 未解決ユーザー数の要約（テスト）

---

## データ保存（`venv/all_users.json`）

主なキー
- `twitch_username`, `twitch_user_id`
- `tier`("1000"|"2000"|"3000"|null), `is_subscriber`
- `streak_months`, `cumulative_months`, `subscribed_since`
- `linked_date`, `last_verified_at`
- `first_notice_at`, `last_notice_at`, `resolved`

備考
- `streak_months` は毎月の検証日と EventSub をもとに更新。
- `cumulative_months` は EventSub(subscription.message) を最優先。未着の場合は自前ロジックで増分。
- `linked_date` は /link 完了時に上書き更新。

---

## よくある質問 / トラブルシュート

- EventSub 登録が 400: callback は HTTPS/443 のみ許可。ngrok の https を使用。
- EventSub 登録が 400(認可エラー): 作成は App Access Token 必須（実装済み）。
- Bits が 401/403: トークン/スコープ不足。以後は自動スキップ（ログ1回）。
- Webhook が来ない: ngrok URL 変更時は `twitch_redirect_uri` と Twitch 側設定を更新。ユーザーは /link 済みかを確認（未リンクは無視）。

---

## 本番環境（Nginx）

Windows Server + Nginx + win-acme での構成例です。既存の運用はこの形を想定しています。

1) Python/依存パッケージ

```powershell
winget install Python.Python.3.12
python -m venv venv
venv\Scripts\activate
pip install -r requirement.txt
```

2) Nginx のセットアップ（Chocolatey 例）

```powershell
choco install nginx
```

3) HTTP→HTTPS リダイレクト（任意）

```nginx
server {
    listen 80;
    server_name your.domain.com;
    return 301 https://$host$request_uri;
}
```

4) HTTPS リバースプロキシ（FastAPI→127.0.0.1:8000）

```nginx
server {
    listen 443 ssl http2;
    server_name your.domain.com;

    # win-acme で取得した証明書パスに置き換えてください
    ssl_certificate     "C:/ProgramData/win-acme/httpsacme-v02.api.letsencrypt.org/acme-v02.pem";
    ssl_certificate_key "C:/ProgramData/win-acme/httpsacme-v02.api.letsencrypt.org/acme-v02-key.pem";

    # すべて FastAPI(Uvicorn) にプロキシ
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 600s;
    }
}
```

5) 設定上の注意

- `twitch_redirect_uri` は本番の HTTPS ドメインで `/twitch_callback` を指す必要があります（例: `https://your.domain.com/twitch_callback`）。
- EventSub のコールバックは自動で `https://your.domain.com/twitch_eventsub` になります（内部実装で `twitch_redirect_uri` から生成）。
- Twitch の開発者コンソールに「OAuth Redirect URLs」として `twitch_redirect_uri` を登録してください。
- Uvicorn は 8000 番で動作（`python bot/bot_client.py`）。Nginx が 443/80 を受け、Uvicorn にプロキシします。

6) 常時稼働（例: タスクスケジューラ）

```powershell
cd C:\path\to\NeiBot
venv\Scripts\activate
python bot/bot_client.py
```

---

## ライセンス

このプロジェクトは **商用利用禁止ライセンス（Non-Commercial License）** の下で公開されています。詳細は [LICENSE](./LICENSE) を参照してください。
