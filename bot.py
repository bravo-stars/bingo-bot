
import os
import asyncio
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

TOKEN = os.environ["BOT_TOKEN"]

keyboard = [
    ["📝 Register"],
    ["💰 Check your balance"],
    ["💳 Deposit"],
    ["🏧 Withdrawal"],
    ["📜 Transaction History"],
    ["🎮 Play"],
    ["📖 Instruction"]
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
        "📝 Register": "📝 Registration feature is coming soon.",
        "💰 Check your balance": "💰 Your balance: 0 ETB",
        "💳 Deposit": "💳 Deposit feature is coming soon.",
        "🏧 Withdrawal": "🏧 Withdrawal feature is coming soon.",
        "📜 Transaction History": "📜 No transactions yet.",
        "🎮 Play": "🎮 Welcome! Bingo game is coming soon.",
        "📖 Instruction": "📖 Register first, then play Bingo."
    }

    await update.message.reply_text(
        responses.get(choice, "Please choose a menu button.")
    )

async def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, menu)
    )

    external_url = os.environ["RENDER_EXTERNAL_URL"]
    port = int(os.environ.get("PORT", 10000))

    await app.initialize()
    await app.start()

    await app.updater.start_webhook(
        listen="0.0.0.0",
        port=port,
        url_path="telegram",
        webhook_url=f"{external_url}/telegram",
    )

    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
