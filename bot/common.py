import json


def get_taken_json():
    with open("./venv/token.json", "r", encoding="utf-8") as f:
        return json.load(f)