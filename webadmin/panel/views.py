
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect, render
from allauth.socialaccount.models import SocialAccount
from .forms import RoleBroadcastForm
import requests
from pathlib import Path


def index(request: HttpRequest) -> HttpResponse:
    return render(request, "panel/index.html")


@login_required
def broadcast(request: HttpRequest) -> HttpResponse:
    # Staff only
    if not request.user.is_staff:
        return HttpResponseForbidden("このページへアクセスする権限がありません。")

    if request.method == "POST":
        form = RoleBroadcastForm(request.POST, request.FILES)
        refresh_requested = bool(request.POST.get("refresh"))
        if not refresh_requested and form.is_valid():
            role_ids = [int(r) for r in form.cleaned_data["role_ids"]]
            guild_id_value = form.cleaned_data.get("guild_id")
            guild_id = int(guild_id_value) if guild_id_value else None
            message = form.cleaned_data["message"] or ""
            file_url = None
            file_path = None

            # Save attachment (if any) and build absolute URL
            f = form.cleaned_data.get("attachment")
            if f:
                from django.core.files.storage import default_storage
                from django.core.files.base import ContentFile

                # Save into MEDIA_ROOT/uploads and build both absolute URL and local path
                rel_path = default_storage.save(f"uploads/{f.name}", ContentFile(f.read()))
                # Normalize URL path
                url_path = str(rel_path).replace("\\", "/").lstrip("/")
                file_url = request.build_absolute_uri(settings.MEDIA_URL + url_path)
                # Absolute filesystem path for the bot (runs on same host)
                file_path = str((Path(settings.MEDIA_ROOT) / rel_path).resolve())

            headers = {"Authorization": f"Bearer {settings.ADMIN_API_TOKEN}"} if settings.ADMIN_API_TOKEN else {}
            role_labels = {str(value): label for value, label in form.fields["role_ids"].choices}
            success_roles: list[str] = []
            failed_roles: list[tuple[str, str]] = []

            for rid in role_ids:
                payload = {"role_id": rid, "message": message}
                if guild_id:
                    payload["guild_id"] = guild_id
                if file_url:
                    payload["file_url"] = file_url
                if file_path:
                    payload["file_path"] = file_path
                try:
                    r = requests.post(
                        f"{settings.BOT_ADMIN_API_BASE}/send_role_dm",
                        json=payload,
                        headers=headers,
                        timeout=10,
                    )
                except Exception as e:
                    failed_roles.append((role_labels.get(str(rid), str(rid)), str(e)))
                    continue

                if r.status_code == 200:
                    success_roles.append(role_labels.get(str(rid), str(rid)))
                else:
                    reason = f"{r.status_code} {r.text}".strip()
                    failed_roles.append((role_labels.get(str(rid), str(rid)), reason))

            if success_roles:
                if len(success_roles) == 1:
                    messages.success(request, f"「{success_roles[0]}」への送信をキューに投入しました。")
                else:
                    joined = "、".join(success_roles)
                    messages.success(
                        request,
                        f"{len(success_roles)}件のロール（{joined}）への送信をキューに投入しました。",
                    )
            for label, reason in failed_roles:
                messages.error(request, f"ロール「{label}」への送信に失敗しました: {reason}")

            if not failed_roles:
                return redirect("broadcast")
    else:
        form = RoleBroadcastForm()

    # Show current Twitch login (if any)
    twitch_account = None
    try:
        twitch_account = SocialAccount.objects.filter(user=request.user, provider="twitch").first()
    except Exception:
        twitch_account = None

    return render(
        request,
        "panel/broadcast.html",
        {"form": form, "twitch_account": twitch_account},
    )
