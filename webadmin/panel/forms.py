from django import forms
from django.conf import settings
import requests
import re


class RoleBroadcastForm(forms.Form):
    guild_id = forms.ChoiceField(label="サーバー", choices=())
    role_id = forms.ChoiceField(label="ロール", choices=())
    message = forms.CharField(label="メッセージ", widget=forms.Textarea, required=False)
    attachment = forms.FileField(label="添付ファイル", required=False)

    # 8MB 上限（Discord DM の添付上限）
    MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
    ALLOWED_PLACEHOLDERS = {"user"}
    PLACEHOLDER_RE = re.compile(r"(?<!\{)\{([^\{\}]+)\}(?!\})")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        headers = {"Authorization": f"Bearer {settings.ADMIN_API_TOKEN}"} if getattr(settings, "ADMIN_API_TOKEN", None) else {}

        # Fetch guilds
        guilds = []
        try:
            r_g = requests.get(f"{settings.BOT_ADMIN_API_BASE}/guilds", headers=headers, timeout=5)
            if r_g.status_code == 200:
                data = r_g.json().get("guilds", [])
                guilds = [(str(g["id"]), g["name"]) for g in data]
        except Exception:
            guilds = []
        self.fields["guild_id"].choices = guilds

        # Determine selected guild (for POST or initial)
        selected_gid = None
        if hasattr(self, "data") and self.data and self.data.get("guild_id"):
            selected_gid = self.data.get("guild_id")
        elif guilds:
            selected_gid = guilds[0][0]

        # Fetch roles for selected guild
        roles = []
        if selected_gid:
            try:
                r_r = requests.get(
                    f"{settings.BOT_ADMIN_API_BASE}/roles",
                    headers=headers,
                    params={"guild_id": selected_gid},
                    timeout=5,
                )
                if r_r.status_code == 200:
                    data = r_r.json().get("roles", [])
                    roles = [(str(r["id"]), r["name"]) for r in data]
            except Exception:
                roles = []
        self.fields["role_id"].choices = roles

    def clean_attachment(self):
        f = self.cleaned_data.get("attachment")
        if not f:
            return f
        size = getattr(f, "size", None)
        if size is not None and size > self.MAX_ATTACHMENT_BYTES:
            raise forms.ValidationError("添付ファイルは8MB以下にしてください。")
        return f

    def clean_message(self):
        msg = self.cleaned_data.get("message") or ""
        # Validate placeholders like {user}; reject unknown ones
        unknown: list[str] = []
        for m in self.PLACEHOLDER_RE.finditer(msg):
            key = (m.group(1) or "").strip().lower()
            if key not in self.ALLOWED_PLACEHOLDERS:
                unknown.append(m.group(1).strip())
        if unknown:
            allowed = ", ".join(sorted(self.ALLOWED_PLACEHOLDERS))
            uniq_unknown = ", ".join(sorted({u for u in unknown}))
            raise forms.ValidationError(
                f"不明なプレースホルダがあります: {uniq_unknown}（使用可能: {allowed}）"
            )
        return msg
