import os
from dotenv import load_dotenv
import telegram
import logging

load_dotenv()

bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
if not bot_token:
	raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

logging.basicConfig(level=logging.INFO)

bot = telegram.Bot(token=bot_token)

