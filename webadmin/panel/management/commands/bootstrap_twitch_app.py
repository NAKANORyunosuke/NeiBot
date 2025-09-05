from django.core.management.base import BaseCommand
from django.contrib.sites.models import Site
from allauth.socialaccount.models import SocialApp
from django.conf import settings
import json
import os


class Command(BaseCommand):
    help = "Create/Update django-allauth SocialApp for Twitch from token.json/env"

    def handle(self, *args, **options):
        # Load credentials
        client_id = os.environ.get("TWITCH_CLIENT_ID")
        secret = os.environ.get("TWITCH_CLIENT_SECRET")
        if not (client_id and secret):
            # Try token.json
            try:
                token_path = os.path.join(settings.BASE_DIR.parent, "venv", "token.json")
                data = json.loads(open(token_path, "r", encoding="utf-8").read())
                client_id = client_id or data.get("twitch_client_id")
                secret = secret or data.get("twitch_secret_key")
            except Exception:
                pass

        if not (client_id and secret):
            self.stderr.write(self.style.ERROR("Missing TWITCH_CLIENT_ID/SECRET and token.json values"))
            return

        app, _ = SocialApp.objects.get_or_create(provider="twitch", name="Twitch")
        app.client_id = client_id
        app.secret = secret
        app.save()

        site, _ = Site.objects.get_or_create(id=1, defaults={"domain": "127.0.0.1:8001", "name": "Local"})
        app.sites.add(site)
        self.stdout.write(self.style.SUCCESS("Twitch SocialApp configured."))
