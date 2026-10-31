import os
from dotenv import load_dotenv
import telebot
from recipe_picker import random_dinners, bot_options_message
#import logging

load_dotenv()

bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
if not bot_token:
	raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

bot = telebot.TeleBot(token=bot_token, parse_mode="HTML")  # Set parse_mode to HTML for formatting

offered_options_index = []  # Global variable to store the offered options db index

@bot.message_handler(commands=["greet"])
def start(message):
	welcome_message = f"Hello, <i>{message.from_user.first_name}</i>! I am <i>MealMind</i>.\nUse /help to see what I can do."
	bot.send_message(chat_id=message.chat.id, text=welcome_message)

@bot.message_handler(commands=["help"])
def help(message):
	help_message = (
		"Here are the commands you can use:\n"
		"/greet - Receive a greeting message.\n"
		"/help - Show this help message.\n"
		"/dinner - Get a dinner suggestion.\n"
		"/pick - Pick one of the dinner options.\n"
	)
	bot.send_message(chat_id=message.chat.id, text=help_message)

@bot.message_handler(commands=["dinner"])
def dinner(message):
	dinner_suggestion = random_dinners()
	db_recipe_index, suggestion_message = bot_options_message(dinner_suggestion)
	offered_options_index = db_recipe_index  # Storing offered options globally so they can be called in /pick
	bot.send_message(chat_id=message.chat.id, text=suggestion_message)

@bot.message_handler(commands=["pick"])
def pick(message):
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(
        telebot.types.InlineKeyboardButton("Option 1", callback_data=offered_options_index[0]),
        telebot.types.InlineKeyboardButton("Option 2", callback_data=offered_options_index[1]),
        telebot.types.InlineKeyboardButton("Option 3", callback_data=offered_options_index[2])
    )
    bot.send_message(message.chat.id, "Pick one:", reply_markup=kb)
	

bot.polling()