import os
from bot.utils.save_and_load import *

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "venv")
USERS_FILE = os.path.join(DATA_DIR, "all_users.json")
DEBUG = os.getenv("DEBUG", "false").lower() in ("1", "true", "yes")


# ========= 共通ユーティリティ =========
def debug_print(*args, **kwargs):
    if DEBUG:
        print(*args, **kwargs)