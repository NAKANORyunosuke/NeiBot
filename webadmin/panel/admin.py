from django.contrib import admin
from django.utils import timezone
from .models import LinkedUser, WebhookEvent
from bot.utils.eventsub_apply import apply_event_to_linked_users


@admin.register(LinkedUser)
class LinkedUserAdmin(admin.ModelAdmin):
    list_display = (
        "discord_id",
        "twitch_username",
        "tier",
        "is_subscriber",
        "updated_at",
    )
    search_fields = ("discord_id",)
    ordering = ("-updated_at",)

    def twitch_username(self, obj):
        try:
            return (obj.data or {}).get("twitch_username")
        except Exception:
            return None

    def tier(self, obj):
        try:
            return (obj.data or {}).get("tier")
        except Exception:
            return None

    def is_subscriber(self, obj):
        try:
            return bool((obj.data or {}).get("is_subscriber"))
        except Exception:
            return False


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = (
        "delivery_id",
        "source",
        "event_type",
        "twitch_user_id",
        "status",
        "retries",
        "received_at",
        "processed_at",
    )
    list_filter = ("source", "status", "event_type")
    search_fields = ("delivery_id", "twitch_user_id")
    ordering = ("-received_at",)
    actions = ("reprocess_events", "mark_pending",)

    def reprocess_events(self, request, queryset):
        ok = 0
        failed = 0
        for ev in queryset:
            try:
                sub_type = None
                try:
                    sub_type = (ev.payload.get("subscription") or {}).get("type")
                except Exception:
                    sub_type = None
                event = (ev.payload or {}).get("event") or {}
                ts = None
                try:
                    ts = (ev.headers or {}).get("Twitch-Eventsub-Message-Timestamp")
                except Exception:
                    ts = None
                _ = apply_event_to_linked_users(sub_type, event, ts)
                ev.status = "done"
                ev.processed_at = timezone.now().isoformat()
                ev.error = None
                ev.save(update_fields=["status", "processed_at", "error"])
                ok += 1
            except Exception as e:
                ev.retries = (ev.retries or 0) + 1
                ev.status = "failed"
                ev.error = str(e)
                ev.save(update_fields=["status", "retries", "error"])
                failed += 1
        self.message_user(request, f"Reprocessed: {ok} ok, {failed} failed")

    reprocess_events.short_description = "Reprocess selected events"

    def mark_pending(self, request, queryset):
        updated = queryset.update(status="pending", error=None)
        self.message_user(request, f"Marked {updated} event(s) as pending")

    mark_pending.short_description = "Mark selected as pending"

