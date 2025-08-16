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
    F -->|Tier情報取得| T[Twitch Helix API]
    F -->|ロール付与/更新| D
