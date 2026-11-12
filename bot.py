
import os
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

TOKEN = TOKEN = os.environ["9200651366:AAGOzolrB8Y09uqHGmz5t19Lmxegnd7XKHc"]

keyboard = [
    ["🎮 Play", "💰 Check your balance"],
    ["💳 Deposit", "🏧 Withdrawal"],
    ["📜 Transaction History"],
    ["📖 Instruction", "📝 Register"]
]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎉 Welcome to Bingo Bot!\nChoose an option:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard, resize_keyboard=True
        )
    )

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text

    responses = {
        "🎮 Play": "🎮 Welcome! Bingo game is coming soon.",
        "💰 Check your balance": "💰 Your balance: 0 ETB",
        "💳 Deposit": "💳 Deposit feature is coming soon.",
        "🏧 Withdrawal": "🏧 Withdrawal feature is coming soon.",
        "📜 Transaction History": "📜 No transactions yet.",
        "📖 Instruction": "📖 Register first, then play Bingo.",
        "📝 Register": "📝 Registration successful!"
    }

    await update.message.reply_text(
        responses.get(choice, "Please choose a menu button.")
    )

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, menu)
    )

    external_url = os.environ["RENDER_EXTERNAL_URL"]

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        url_path="telegram",
        webhook_url=f"{external_url}/telegram",
    )

if __name__ == "__main__":
    main()
