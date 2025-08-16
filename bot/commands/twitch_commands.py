import discord
from discord import app_commands
from discord.ext import commands
import json
import os

LINKED_USERS_FILE = "venv/linked_users.json"


# JSONを読み込む
def load_linked_users():
    if not os.path.exists(LINKED_USERS_FILE):
        return {}
    with open(LINKED_USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# JSONに保存する
def save_linked_users(data):
    os.makedirs(os.path.dirname(LINKED_USERS_FILE), exist_ok=True)
    with open(LINKED_USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
