from django.apps import AppConfig
import threading
import os
import sys
import asyncio


# class BotcontrolConfig(AppConfig):
#     default_auto_field = 'django.db.models.BigAutoField'
#     name = 'botcontrol'

#     def ready(self):
#         if os.environ.get('RUN_MAIN') != 'true':
#             return  # 子プロセスじゃないなら無視

#         if 'runserver' in sys.argv:
#             from bot.bot_client import run_discord_bot

#         threading.Thread(
#             target=lambda: asyncio.run(run_discord_bot()),
#             daemon=True
#         ).start()

