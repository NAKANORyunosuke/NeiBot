import json


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
