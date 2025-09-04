# scripts/migrate_role_ids.py
from __future__ import annotations
import json
import sys
from pathlib import Path

# 必要に応じてパス調整
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROLE_FILE = PROJECT_ROOT / "venv" / "role_ids.json"


def is_old_format(d: dict) -> bool:
    # 旧: "ROLE_TWITCH_LINKED" などがトップレベルにある
    return any(k.startswith("ROLE_") for k in d.keys())


def is_new_format(d: dict) -> bool:
    # 新: トップレベルキーがギルドID（数字文字列）で、中身にROLE_キーがある
    return all(
        isinstance(v, dict) and any(k.startswith("ROLE_") for k in v.keys())
        for v in d.values()
    )


def main(guild_id: str):
    if not ROLE_FILE.exists():
        print(f"[skip] {ROLE_FILE} が見つかりません。")
        return 0

    data = json.loads(ROLE_FILE.read_text(encoding="utf-8"))
    if is_new_format(data):
        print("[ok] すでに新形式です。何もしません。")
        return 0

    if not is_old_format(data):
        print("[warn] 旧形式でも新形式でもありません。中断します。")
        return 1

    # 旧 → 新へ包む
    wrapped = {guild_id: data}
    ROLE_FILE.write_text(
        json.dumps(wrapped, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[done] 旧形式を新形式に変換しました。guild_id={guild_id}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        print("Usage: python scripts/migrate_role_ids.py <GUILD_ID>")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
