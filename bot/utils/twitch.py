from discord.ext import commands
import discord
import json

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)


def get_twitch_keys():
    with open("./venv/token.json", "r", encoding="utf-8") as f:
        TWITCH_CLIENT_ID = json.load(f)["twitch_clinet_id"]
        TWITCH_SECRET_KEY = json.load(f)["twitch_seqret_key"]
        TWITCH_ACCESS_TOKEN = json.load(f)["twitch_access_token"]
    return {"twitch_clinet_id": TWITCH_CLIENT_ID,
            "twitch_seqret_key": TWITCH_SECRET_KEY,
            "twitch_access_token": TWITCH_ACCESS_TOKEN}

