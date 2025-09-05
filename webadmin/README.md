Admin panel (Django) for Twitch login and role DM broadcast

Overview
- Logs in with Twitch via django-allauth.
- Presents a form for selecting a Discord role, composing a message, and optionally attaching a file.
- On submit, calls the bot’s FastAPI admin endpoints to DM all members with that role.

Prereqs
- Bot running with FastAPI on `http://127.0.0.1:8000` (default from `bot/bot_client.py`).
- Set an admin API token in the bot process environment: `ADMIN_API_TOKEN=<random-long-token>`.
- Twitch Developer Console: add a Redirect URL for Django: `http://127.0.0.1:8001/accounts/twitch/login/callback/` (and your production URL as needed).

Install
```bash
cd webadmin
pip install -r requirements.txt  # only adds django-allauth
cd ..
pip install -r requirement.txt    # base project deps (if not installed already)
```

Run
```bash
set BOT_ADMIN_API_BASE=http://127.0.0.1:8000
set ADMIN_API_TOKEN=<same_token_as_bot>
set ALLOWED_TWITCH_LOGINS=<twitch_login1>,<twitch_login2>

cd webadmin
python manage.py migrate
python manage.py runserver 127.0.0.1:8001
```

Usage
- Open http://127.0.0.1:8001
- Login with Twitch (Allauth). Allowed logins are promoted to staff automatically.
- Open “ロールDM送信”, select a role, enter message, optionally add a file, and submit.

Notes
- The file is uploaded to Django and the bot downloads it via the generated public URL, then re-attaches it in the DM.
- If Discord DMs are closed for a user, those sends will fail silently and be logged by the bot.
- For production, set proper `ALLOWED_HOSTS`, `SECRET_KEY`, and HTTPS.

