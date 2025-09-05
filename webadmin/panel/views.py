from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect, render
from allauth.socialaccount.models import SocialAccount
from .forms import RoleBroadcastForm
import requests


def index(request: HttpRequest) -> HttpResponse:
    return render(request, "panel/index.html")


@login_required
def broadcast(request: HttpRequest) -> HttpResponse:
    # Staff only
    if not request.user.is_staff:
        return HttpResponseForbidden("このページへアクセスする権限がありません。")

    if request.method == "POST":
        form = RoleBroadcastForm(request.POST, request.FILES)
        # If refresh button pressed, just re-render with updated role choices
        if request.POST.get("refresh"):
            return render(request, "panel/broadcast.html", {"form": form})
        if form.is_valid():
            role_id = int(form.cleaned_data["role_id"])
            guild_id = int(form.cleaned_data["guild_id"]) if form.cleaned_data.get("guild_id") else None
            message = form.cleaned_data["message"] or ""
            file_url = None

            # Save attachment (if any) and build absolute URL
            f = form.cleaned_data.get("attachment")
            if f:
                from django.core.files.storage import default_storage
                from django.core.files.base import ContentFile
                path = default_storage.save(f"uploads/{f.name}", ContentFile(f.read()))
                file_url = request.build_absolute_uri(settings.MEDIA_URL + path.split("/media/")[-1])

            # Call bot admin API
            headers = {"Authorization": f"Bearer {settings.ADMIN_API_TOKEN}"} if settings.ADMIN_API_TOKEN else {}
            payload = {"role_id": role_id, "message": message}
            if guild_id:
                payload["guild_id"] = guild_id
            if file_url:
                payload["file_url"] = file_url
            try:
                r = requests.post(f"{settings.BOT_ADMIN_API_BASE}/send_role_dm", json=payload, headers=headers, timeout=10)
                if r.status_code == 200:
                    messages.success(request, "送信をキューに投入しました。")
                    return redirect("broadcast")
                else:
                    messages.error(request, f"送信に失敗しました: {r.status_code} {r.text}")
            except Exception as e:
                messages.error(request, f"APIエラー: {e}")
    else:
        form = RoleBroadcastForm()

    # Show current Twitch login (if any)
    twitch_account = None
    try:
        twitch_account = SocialAccount.objects.filter(user=request.user, provider="twitch").first()
    except Exception:
        twitch_account = None

    return render(request, "panel/broadcast.html", {"form": form, "twitch_account": twitch_account})
