from django.conf import settings
from django.dispatch import receiver
from allauth.account.signals import user_logged_in
from allauth.socialaccount.models import SocialAccount
from django.db.models.signals import post_migrate
from django.contrib.sites.models import Site
from django.conf import settings as dj_settings


def _ensure_twitch_socialapp():
    try:
        from allauth.socialaccount.models import SocialApp
    except Exception:
        return
    try:
        import json, os
        token_path = os.path.join(dj_settings.BASE_DIR.parent, "venv", "token.json")
        client_id = None
        secret = None
        if os.path.exists(token_path):
            data = json.loads(open(token_path, "r", encoding="utf-8").read())
            client_id = data.get("twitch_client_id")
            secret = data.get("twitch_secret_key")
        # Env overrides
        client_id = os.environ.get("TWITCH_CLIENT_ID") or client_id
        secret = os.environ.get("TWITCH_CLIENT_SECRET") or secret
        if not (client_id and secret):
            return
        app, _ = SocialApp.objects.get_or_create(provider="twitch", name="Twitch")
        app.client_id = client_id
        app.secret = secret
        app.save()
        # Ensure site mapping
        site = Site.objects.get_or_create(id=1, defaults={"domain": "localhost:8001", "name": "Local"})[0]
        app.sites.add(site)
    except Exception:
        pass


@receiver(user_logged_in)
def mark_staff_on_twitch_login(request, user, **kwargs):
    try:
        sa = SocialAccount.objects.filter(user=user, provider="twitch").first()
        if not sa:
            return
        twitch_login = (sa.extra_data or {}).get("login")
        if twitch_login and str(twitch_login).lower() in getattr(settings, "ALLOWED_TWITCH_LOGINS", set()):
            if not user.is_staff:
                user.is_staff = True
                user.save(update_fields=["is_staff"])
    except Exception:
        pass


@receiver(post_migrate)
def _on_post_migrate(sender, **kwargs):
    _ensure_twitch_socialapp()
