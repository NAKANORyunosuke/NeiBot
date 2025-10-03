from django.db import models


class LinkedUser(models.Model):
    discord_id = models.CharField(max_length=64, primary_key=True)
    data = models.JSONField()
    created_at = models.CharField(max_length=40)
    updated_at = models.CharField(max_length=40)

    class Meta:
        managed = False
        db_table = "linked_users"
        verbose_name = "Linked User"
        verbose_name_plural = "Linked Users"

    def __str__(self) -> str:
        try:
            twitch_name = (self.data or {}).get("twitch_username")
        except Exception:
            twitch_name = None
        label = f"{self.discord_id}"
        if twitch_name:
            label += f" ({twitch_name})"
        return label


class WebhookEvent(models.Model):
    delivery_id = models.CharField(max_length=128, primary_key=True)
    source = models.CharField(max_length=32)
    event_type = models.CharField(max_length=128)
    twitch_user_id = models.CharField(max_length=64, null=True, blank=True)
    payload = models.JSONField()
    headers = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=16, default="pending")
    retries = models.IntegerField(default=0)
    error = models.TextField(null=True, blank=True)
    received_at = models.CharField(max_length=40)
    processed_at = models.CharField(max_length=40, null=True, blank=True)

    class Meta:
        managed = False
        db_table = "webhook_events"
        verbose_name = "Webhook Event"
        verbose_name_plural = "Webhook Events"

    def __str__(self) -> str:
        return f"{self.source}:{self.delivery_id} [{self.status}]"



