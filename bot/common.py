import json
import os


def get_taken_json():
    with open("./venv/token.json", "r", encoding="utf-8") as f:
        return json.load(f)


def get_twitch_keys():
    with open("./venv/token.json", "r", encoding="utf-8") as f:
        TWITCH_CLIENT_ID = json.load(f)["twitch_clinet_id"]
        TWITCH_SECRET_KEY = json.load(f)["twitch_seqret_key"]
        TWITCH_ACCESS_TOKEN = json.load(f)["twitch_access_token"]
    return {"twitch_clinet_id": TWITCH_CLIENT_ID,
            "twitch_seqret_key": TWITCH_SECRET_KEY,
            "twitch_access_token": TWITCH_ACCESS_TOKEN}


def get_auth_url(state: str):
    client_id, client_secret, redirect_uri = get_twitch_keys()
    return (
        f"https://id.twitch.tv/oauth2/authorize"
        f"?client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&scope=user:read:subscriptions"
        f"&state={state}"
    )


def get_guild_id():
    with open("./venv/token.json", "r", encoding="utf-8") as f:
        GUILD_ID = json.load(f)['guild_id']
    return str(GUILD_ID)


def save_all_guild_members(bot):
    path = "./venv/guild_members.json"

    # 既存データを読む
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}

    guild_id = get_guild_id()
    guild = bot.get_guild(guild_id)
    if guild is None:
        print("Guildが見つかりません")
        return

    members_dic = {
        str(m.id): {
            "id": m.id,
            "name": getattr(m, "name", None),
            "display_name": getattr(m, "display_name", None),
            "bot": getattr(m, "bot", None),
        }
        for m in guild.members
    }

    data[str(guild_id)] = members_dic

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

    os.replace(tmp, path)


def load_guild_members():
    path = "./venv/guild_members.json"
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
        if not content:
            return {}
        return json.loads(content)
