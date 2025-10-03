
from django import forms
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
import requests
import re
import json


class MultiFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True

    def value_from_datadict(self, data, files, name):
        if not files:
            return []
        if hasattr(files, "getlist"):
            return files.getlist(name)
        upload = files.get(name)
        if upload is None:
            return []
        if isinstance(upload, (list, tuple)):
            return list(upload)
        return [upload]


class MultiFileField(forms.FileField):
    widget = MultiFileInput

    def clean(self, data, initial=None):
        if not data:
            data = []
        if isinstance(data, tuple):
            data = list(data)
        if not isinstance(data, list):
            data = [data]
        cleaned: list[UploadedFile] = []
        errors: list[forms.ValidationError] = []
        for item in data:
            if item in self.empty_values:
                continue
            try:
                cleaned.append(super().clean(item, initial))
            except forms.ValidationError as exc:
                errors.extend(exc.error_list)
        if errors:
            raise forms.ValidationError(errors)
        if self.required and not cleaned:
            raise forms.ValidationError(self.error_messages["required"])
        return cleaned


class RoleBroadcastForm(forms.Form):
    guild_id = forms.ChoiceField(label="サーバー", choices=())
    role_ids = forms.MultipleChoiceField(label="ロール", choices=(), required=False)
    message = forms.CharField(label="メッセージ", widget=forms.Textarea, required=False)
    attachments = MultiFileField(label="添付ファイル", required=False)

    # 8MB は Discord DM の添付制限
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
        self.fields["role_ids"].choices = roles
        if not self.is_bound and roles:
            self.initial.setdefault("role_ids", [roles[0][0]])

    def clean_attachments(self):
        files = self.cleaned_data.get("attachments") or []
        cleaned: list[UploadedFile] = []
        for f in files:
            if f is None:
                continue
            size = getattr(f, "size", None)
            if size is not None and size > self.MAX_ATTACHMENT_BYTES:
                raise forms.ValidationError("添付ファイルは8MB以下にしてください。")
            cleaned.append(f)
        return cleaned

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
                f"不明なプレースホルダーがあります: {uniq_unknown}。使用可能: {allowed}"
            )
        return msg

    def clean_role_ids(self):
        values = self.cleaned_data.get("role_ids") or []
        filtered = [v for v in values if v]
        if not filtered:
            raise forms.ValidationError("ロールを1つ以上選択してください。")
        unique: list[str] = []
        for v in filtered:
            if v not in unique:
                unique.append(v)
        return unique


class EventSubSubscriptionForm(forms.Form):
    EVENTSUB_CHOICES = [
        ("channel.subscribe", "channel.subscribe (購読開始)"),
        ("channel.subscription.message", "channel.subscription.message (継続通知)"),
        ("channel.subscription.end", "channel.subscription.end (終了通知)"),
        ("channel.cheer", "channel.cheer (ビッツ)"),
        ("stream.online", "stream.online"),
        ("stream.offline", "stream.offline"),
    ]

    subscription_type = forms.ChoiceField(
        label="EventSubタイプ", choices=EVENTSUB_CHOICES
    )
    version = forms.CharField(
        label="バージョン",
        initial="1",
        required=False,
        help_text="通常は1のままで問題ありません。",
    )
    callback_url = forms.URLField(
        label="Callback URL",
        required=False,
        help_text="空欄の場合はデフォルト設定を利用します。",
    )
    secret = forms.CharField(
        label="Secret",
        required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
        help_text="空欄の場合は既定のシークレットを使用します。",
    )
    condition_json = forms.CharField(
        label="条件 (JSON)",
        required=False,
        widget=forms.Textarea,
        help_text="必要に応じてカスタム条件をJSON形式で指定できます。未入力の場合は配信者IDを条件に利用します。",
    )

    def clean_version(self) -> str:
        value = (self.cleaned_data.get("version") or "1").strip()
        return value or "1"

    def clean_condition_json(self):
        raw = self.cleaned_data.get("condition_json")
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(f"JSONとして解釈できません: {exc}")
        if not isinstance(parsed, dict):
            raise forms.ValidationError("オブジェクト形式のJSONを指定してください。")
        return parsed

class SubscriberImportForm(forms.Form):
    file = forms.FileField(
        label="TwitchサブスクライバCSV",
        help_text="subscriber-list.csv をそのままアップロードしてください。"
    )

    def clean_file(self):
        f = self.cleaned_data.get("file")
        if not f:
            return f
        if f.size and f.size > 5 * 1024 * 1024:
            raise forms.ValidationError("ファイルサイズは5MB以下にしてください。")
        return f
