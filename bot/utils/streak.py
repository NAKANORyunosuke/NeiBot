import datetime
from typing import Dict, Any, Optional
from bot.utils.save_and_load import load_users, save_linked_users

JST = datetime.timezone(datetime.timedelta(hours=9))


def _yyyymm(d: datetime.date) -> int:
    return d.year * 100 + d.month


def _month_diff(a: datetime.date, b: datetime.date) -> int:
    """a→b の“月差”（a<b でも正）"""
    return (b.year - a.year) * 12 + (b.month - a.month)


def _first_day(dt: datetime.date) -> datetime.date:
    return datetime.date(dt.year, dt.month, 1)


def _add_months(d: datetime.date, months: int) -> datetime.date:
    """d に months を加減算（負数可）して同日のまま返す。日超過は月末で丸める。"""
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    day = d.day
    # 月末調整
    last_day = (
        (datetime.date(y + (m // 12), (m % 12) + 1, 1) - datetime.timedelta(days=1)).day
        if m != 12
        else 31
    )
    if day > last_day:
        day = last_day
    return datetime.date(y, m, day)


def reconcile_and_save_link(
    discord_id: str, info: Dict[str, Any], today: Optional[datetime.date] = None
) -> Dict[str, Any]:
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
    did = str(discord_id)
    prev = linked.get(did, {}) if isinstance(linked.get(did, {}), dict) else {}

    # 直近の検証日を使用（過去実装の linked_date ではなく last_verified_at を基準に）
    prev_linked_iso = prev.get("last_verified_at")  # "YYYY-MM-DD" or date
    prev_date = None
    if prev_linked_iso:
        try:
            if isinstance(prev_linked_iso, datetime.date):
                prev_date = prev_linked_iso
            else:
                prev_date = datetime.date.fromisoformat(str(prev_linked_iso))
        except Exception:
            pass

    prev_streak = int(prev.get("streak_months", 0) or 0)
    prev_cum = int(prev.get("cumulative_months", 0) or 0)
    prev_is_sub = bool(prev.get("is_subscriber", False))

    # 新しい情報（APIからのdict）
    tier = info.get("tier")
    is_sub = tier is not None
    # Helixは視聴者の cumulative/streak/開始日を返さないため、
    # cumulative は自前でカウント（“サブだった月の累積”）に切り替える。
    sub_started_at = info.get("sub_started_at")  # 文字列 or None

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
                new_streak = max(
                    prev_streak, int(info.get("streak_months", 0) or prev_streak or 1)
                )
            elif mdiff == 1:
                # 先月→今月
                if prev_is_sub:
                    new_streak = max(
                        prev_streak + 1,
                        int(info.get("streak_months", 0) or (prev_streak + 1)),
                    )
                else:
                    # 先月は非サブ→今月再開
                    new_streak = max(1, int(info.get("streak_months", 0) or 1))
            else:
                # 月差>1（空白あり）→再開扱いで1から
                new_streak = max(1, int(info.get("streak_months", 0) or 1))
        # new_streak は自前ロジックで完結
    else:
        # 非サブならリセット
        new_streak = 0
    # 保存用にエントリを用意
    if did not in linked or not isinstance(linked.get(did), dict):
        linked[did] = {}

    # APIの最新値をコピー
    for key in list(info.keys()):
        linked[did][key] = info[key]

    # 計算済み streak を反映
    linked[did]["streak_months"] = int(new_streak)

    # 累計月数は“当月サブ確認が取れた回数”として自前更新
    new_cum = prev_cum
    if is_sub:
        if prev_date is None:
            new_cum = max(prev_cum, 1)
        else:
            mdiff = _month_diff(prev_date, today)
            if mdiff > 0:
                new_cum = prev_cum + 1
    linked[did]["cumulative_months"] = int(new_cum)

    # サブスク登録日（できる限り確実に）：
    # 1) APIの sub_started_at があればそれを採用
    # 2) なければ cumulative_months から推定（今月の1日から cum-1 ヶ月引く）
    # 3) 既存の subscribed_since があれば最小（日付が古い方）を維持
    prev_since_iso = linked[did].get("subscribed_since") or prev.get("subscribed_since")
    prev_since: Optional[datetime.date] = None
    if prev_since_iso:
        try:
            if isinstance(prev_since_iso, datetime.date):
                prev_since = prev_since_iso
            else:
                prev_since = datetime.date.fromisoformat(str(prev_since_iso))
        except Exception:
            prev_since = None

    new_since_candidates: list[datetime.date] = []
    if isinstance(sub_started_at, datetime.date):
        new_since_candidates.append(sub_started_at)
    elif isinstance(sub_started_at, str):
        try:
            # HelixはISO8601（日時）想定 → date に丸め
            new_since_candidates.append(
                datetime.date.fromisoformat(sub_started_at[:10])
            )
        except Exception:
            pass
    if is_sub:
        today = today or datetime.datetime.now(JST).date()
        # 厳密な開始日は取得不可のため、その月の1日を初期値とする
        new_since_candidates.append(_first_day(today))

    chosen_since = None
    for cand in new_since_candidates:
        if cand is None:
            continue
        if chosen_since is None or cand < chosen_since:
            chosen_since = cand
    if prev_since and (chosen_since is None or prev_since < chosen_since):
        chosen_since = prev_since
    if chosen_since is None and is_sub:
        chosen_since = today or datetime.datetime.now(JST).date()

    if chosen_since is not None:
        linked[did]["subscribed_since"] = chosen_since.isoformat()

    # 付帯メタ情報
    linked[did]["resolved"] = True
    linked[did]["first_notice_at"] = None
    linked[did]["last_verified_at"] = today or datetime.datetime.now(JST).date()
    # 直近のリンク完了日（OAuth完了のタイミング）として更新
    # 既存運用では linked_date を参照しているケースがあるため、毎回上書きする
    t = today or datetime.datetime.now(JST).date()
    linked[did]["linked_date"] = t.isoformat()

    save_linked_users(linked)
    return linked[did]
