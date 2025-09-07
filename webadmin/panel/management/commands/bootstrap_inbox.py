from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Ensure bot DB tables (linked_users, webhook_events) exist by touching the DB."

    def handle(self, *args, **options):
        from bot.utils.save_and_load import load_users  # triggers init

        _ = load_users()
        self.stdout.write(self.style.SUCCESS("Ensured bot DB tables exist."))

