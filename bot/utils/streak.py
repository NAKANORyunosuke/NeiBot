import datetime
from typing import Dict, Any, Optional
from bot.utils.save_and_load import *

JST = datetime.timezone(datetime.timedelta(hours=9))


def _yyyymm(d: datetime.date) -> int:
    return d.year * 100 + d.month


def _month_diff(a: datetime.date, b: datetime.date) -> int:
    """a→b の“月差”（a<b でも正）"""
    return (b.year - a.year) * 12 + (b.month - a.month)


def reconcile_and_save_link(discord_id: str, info: Dict[str, Any], today: Optional[datetime.date] = None) -> Dict[str, Any]:
    """
    毎月の再リンク時に streak を更新して保存する。
    - 今月すでに処理済みなら“上書きはするがstreakは据え置き”
    - サブ切れ（tier=None）なら streak をリセット
    - 先月→今月の月差=1 かつ サブ継続中なら streak + 1
    - 月差>1（空白がある）や、先月非サブ→今月サブ なら streak = 1（再開）
    - cumulative_months が増えていれば +1 の整合も取る
    """
    """
    info =
    {
        "twitch_username": str,
        "twitch_user_id": str,
        "tier": "1000"|"2000"|"3000"|None,
        "streak_months": int,
        "cumulative_months": int,
        "bits_rank": Optional[int],
        "bits_score": int,
        "is_subscriber": bool,
    }
    """
    if today is None:
        today = datetime.datetime.now(JST).date()

    linked = load_users()
    prev = linked.get(discord_id, {})

    prev_linked_iso = prev.get("linked_date")  # "YYYY-MM-DD"
    prev_date = None
    if prev_linked_iso:
        try:
            prev_date = datetime.date.fromisoformat(prev_linked_iso)
        except Exception:
            pass

    prev_streak = int(prev.get("streak_months", 0) or 0)
    prev_cum = int(prev.get("cumulative_months", 0) or 0)
    prev_is_sub = bool(prev.get("is_subscriber", False))

    # 新しい情報（APIからのdict）
    tier = info.get("tier")
    is_sub = tier is not None
    cum = int(info.get("cumulative_months", 0) or 0)

    # デフォルトは「今月1から」（非サブは0）
    new_streak = 0
    if is_sub:
        if prev_date is None:
            # 初回リンク or 日付壊れ → 1から
            new_streak = max(1, int(info.get("streak_months", 0) or 1))
        else:
            mdiff = _month_diff(prev_date, today)
            if mdiff <= 0:
                # 同月 or 逆行（同月再リンクなど）→前回値を優先
                new_streak = max(prev_streak, int(info.get("streak_months", 0) or prev_streak or 1))
            elif mdiff == 1:
                # 先月→今月
                if prev_is_sub:
                    new_streak = max(prev_streak + 1, int(info.get("streak_months", 0) or (prev_streak + 1)))
                else:
                    # 先月は非サブ→今月再開
                    new_streak = max(1, int(info.get("streak_months", 0) or 1))
            else:
                # 月差>1（空白あり）→再開扱いで1から
                new_streak = max(1, int(info.get("streak_months", 0) or 1))
        # cumulative が素直に増えているなら new_streak を整合
        if cum > prev_cum and new_streak < prev_streak + (cum - prev_cum):
            new_streak = prev_streak + (cum - prev_cum)
    else:
        # 非サブならリセット
        new_streak = 0
    for key in list(info.keys()):
        linked[str(discord_id)][key] = info[key]
    
    linked[str(discord_id)]["resolved"] = True
    linked[str(discord_id)]["first_notice_at"] = None
    linked[str(discord_id)]["last_verified_at"] = today
    
    save_linked_users(linked)
    return linked[str(discord_id)]
