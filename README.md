# Twitchサブスク連携Discord Bot - NeiBot

![Python](https://img.shields.io/badge/Python-3.12-blue)  
![py-cord](https://img.shields.io/badge/py--cord-2.6.1-green)  
![twitchAPI](https://img.shields.io/badge/twitchAPI-4.5.0-purple)  

Twitch サブスク状況を自動判定し、Discord ロールの付与 / 剥奪を行う Bot です。  
OAuth2 により Twitch アカウントと Discord アカウントをリンクし、Tier に応じてロールを割り当てます。  

---

## 主な機能

- **Twitch連携**
  - Twitch OAuth2 によるアカウントリンク  
  - Helix API を利用したサブスク Tier 情報の取得  

- **Discord連携**
  - サブスク Tier に応じたロール付与 / 剥奪  
  - サーバー参加時の DM によるリンク催促  
  - 月初に「再リンク催促」フラグを立て、未更新ユーザーに通知  

- **Web管理 (FastAPI)**
  - OAuth コールバック処理  
  - リンク状態確認用のエンドポイント  

- **運用環境**
  - Windows Server 上で常時稼働  
  - Nginx によるリバースプロキシ  
  - Let’s Encrypt (win-acme) による SSL 化  

---

## 技術スタック

- **言語:** Python 3.12  
- **主要ライブラリ:**  
  - [py-cord 2.6.1](https://github.com/Pycord-Development/pycord)  
  - [twitchAPI 4.5.0](https://github.com/Teekeks/pyTwitchAPI)  
  - [FastAPI](https://fastapi.tiangolo.com/)  
- **利用 API:** Twitch Helix API, Discord API  
- **インフラ:** Windows Server, Nginx, win-acme  

---

## 処理フロー

```mermaid
flowchart LR
    U[ユーザー] -->|Discord参加| D[Discordサーバー]
    D -->|/link 実行| O[OAuth URL発行]
    O --> B[ブラウザでTwitch認証]
    B --> F[FastAPIコールバック]
    F -->|サブスクTier取得| T[Twitch Helix API]
    F -->|ロール付与| D
```

---

## セットアップ手順（ローカル開発環境）

1. **リポジトリをクローン**
```bash
git clone https://github.com/NAKANORyunosuke/NeiBot.git
cd NeiBot
```

2. **仮想環境作成**
```bash
python -m venv venv
venv\Scripts\activate
```

3. **依存パッケージのインストール**
```bash
pip install -r requirements.txt
```

4. **認証情報を設定**  
   `venv/token.json` に以下を保存
```json
{
  "discord_token": "YOUR_DISCORD_BOT_TOKEN",
  "twitch_client_id": "YOUR_TWITCH_CLIENT_ID",
  "twitch_secret_key": "YOUR_TWITCH_SECRET_KEY",
  "twitch_redirect_uri": "https://your.domain.com/twitch/callback"
}
```

5. **Bot & APIサーバー起動**
```bash
python bot/bot_client.py
```

---

## 本番環境構築（Windows Server + Nginx + win-acme）

### 1. Python環境の準備
```powershell
winget install Python.Python.3.12
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Nginxのインストール
```powershell
choco install nginx
```
Nginx設定ファイル例（`C:\tools\nginx\conf\nginx.conf`）：
```nginx
server {
    listen 80;
    server_name your.domain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### 3. SSL証明書の取得（win-acme）
1. [win-acme公式サイト](https://www.win-acme.com/)からバイナリをダウンロード  
2. 実行して `N`（新規証明書作成）を選択  
3. ドメインを入力し、自動で証明書を取得  
4. Nginx設定に追記：
```nginx
server {
    listen 443 ssl;
    server_name your.domain.com;

    ssl_certificate     "C:/ProgramData/win-acme/httpsacme-v02.api.letsencrypt.org/acme-v02.pem";
    ssl_certificate_key "C:/ProgramData/win-acme/httpsacme-v02.api.letsencrypt.org/acme-v02-key.pem";

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### 4. Botの常時稼働
Windowsのタスクスケジューラで以下を登録：
```powershell
cd C:\path\to\NeiBot
venv\Scripts\activate
python bot/bot_client.py
```

---

## ディレクトリ構成

```
NeiBot/
├─ bot/                # Bot本体
│   ├─ bot_client.py   # Discord Botエントリーポイント
│   ├─ utils/          # 共通処理
│   └─ cogs/           # コマンド機能
├─ venv/               # 仮想環境 & 認証情報
└─ requirements.txt
```

---

## 今後の拡張予定
- サブスク期限切れチェックの自動化  
- 一斉DM送信機能  
- ログ分析による不正アクセス検知  

---

## ライセンス
Apache License 2.0