import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
# Ensure repo root is importable (to import `bot.*` helpers from admin actions)
if str(BASE_DIR.parent) not in sys.path:
    sys.path.insert(0, str(BASE_DIR.parent))

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insecure-key")
# Toggle DEBUG via env (default: True for local dev)
DEBUG = (os.environ.get("DJANGO_DEBUG") or "true").lower() in ("1", "true", "yes", "on")

# Align with nginx.conf (neige-subscription.com) in production
if DEBUG:
    ALLOWED_HOSTS = ["*"]
else:
    ALLOWED_HOSTS = [
        "neige-subscription.com",
        "www.neige-subscription.com",
    ]
    # Trust HTTPS proxy headers from Nginx and set CSRF trusted origins
    CSRF_TRUSTED_ORIGINS = [
        "https://neige-subscription.com",
        "https://www.neige-subscription.com",
    ]
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True
    # Secure cookies for HTTPS and predictable samesite behavior
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    CSRF_COOKIE_SAMESITE = "Lax"
    # Build absolute URLs as https in contexts where scheme is not inferred
    ACCOUNT_DEFAULT_HTTP_PROTOCOL = "https"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",

    # allauth
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.twitch",

    # local
    "panel",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # allauth middleware
    "allauth.account.middleware.AccountMiddleware",
]

ROOT_URLCONF = "webadmin.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

WSGI_APPLICATION = "webadmin.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        # Use the same DB file as the bot (repo root)
        "NAME": (BASE_DIR.parent / "db.sqlite3"),
    }
}

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "ja"
TIME_ZONE = "Asia/Tokyo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "static"
STATICFILES_DIRS = []

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# allauth
SITE_ID = 1
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/broadcast/"
# Ensure allauth also uses the same redirect
ACCOUNT_LOGIN_REDIRECT_URL = LOGIN_REDIRECT_URL
LOGOUT_REDIRECT_URL = "/broadcast/"
ACCOUNT_LOGOUT_REDIRECT_URL = LOGOUT_REDIRECT_URL

AUTHENTICATION_BACKENDS = (
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
)

# Email/Allauth settings for development (no SMTP)
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_EMAIL_REQUIRED = False
ACCOUNT_AUTHENTICATION_METHOD = "username"
ACCOUNT_USERNAME_REQUIRED = True
SOCIALACCOUNT_STORE_TOKENS = True
SOCIALACCOUNT_PROVIDERS = {
    "twitch": {
        "APP": {},  # Will be populated below if credentials are available
        "SCOPE": ["user:read:email"],
    }
}

# Use GET to initiate social login to avoid CSRF POST on provider start
SOCIALACCOUNT_LOGIN_ON_GET = True

# Admin API endpoint for the bot (FastAPI) and token for auth
# token.json を単一のソースとして使用する（環境変数は無視）
BOT_ADMIN_API_BASE = None
ADMIN_API_TOKEN = None

# Allow Twitch logins for staff promotion (comma separated Twitch login names)
ALLOWED_TWITCH_LOGINS = {
    s.strip().lower()
    for s in os.environ.get("ALLOWED_TWITCH_LOGINS", "").split(",")
    if s.strip()
}

# Load token.json if present to auto-allow broadcaster login
try:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    token_path = PROJECT_ROOT / "venv" / "token.json"
    if token_path.exists():
        import json

        data = json.loads(token_path.read_text("utf-8"))
        # Optionally record broadcaster login name if available (not always present)
        broadcaster_login = data.get("twitch_login") or data.get("twitch_name")
        if broadcaster_login:
            ALLOWED_TWITCH_LOGINS.add(str(broadcaster_login).lower())

        # If Twitch client credentials exist, wire them directly to allauth to avoid DB SocialApp
        tw_client_id = os.environ.get("TWITCH_CLIENT_ID") or data.get("twitch_client_id")
        tw_secret = os.environ.get("TWITCH_CLIENT_SECRET") or data.get("twitch_secret_key")
        if tw_client_id and tw_secret:
            SOCIALACCOUNT_PROVIDERS.setdefault("twitch", {})
            SOCIALACCOUNT_PROVIDERS["twitch"]["APP"] = {
                "client_id": tw_client_id,
                "secret": tw_secret,
                "key": "",
            }

        # Read admin API configurations for Django from token.json only
        ADMIN_API_TOKEN = str(data.get("admin_api_token") or "")
        BOT_ADMIN_API_BASE = str(data.get("bot_admin_api_base") or "")
except Exception:
    pass

if not BOT_ADMIN_API_BASE:
    BOT_ADMIN_API_BASE = "http://127.0.0.1:8000"
