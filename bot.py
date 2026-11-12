
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

rooms = [
    ["🎮 Play Demo"],
    ["🌟 Super Bingo Demo"],
    ["🎁 BravoBingo Bonus"],
    ["⬅️ Back"]
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

    if choice == "🎮 Play":
        await update.message.reply_text(
            "🕹 PLAY IN:\nChoose a room to join the demo game.",
            reply_markup=ReplyKeyboardMarkup(
                rooms, resize_keyboard=True
            )
        )
        return

    if choice in rooms[0] + rooms[1] + rooms[2]:
        await update.message.reply_text(
            f"🎮 {choice}\n\nDemo room selected!\nGame setup is coming soon."
        )
        return

    if choice == "⬅️ Back":
        await start(update, context)
        return

    responses = {
        "📝 Register": "📝 Registration feature is coming soon.",
        "💰 Check your balance": "💰 Demo balance: 0 ETB",
        "💳 Deposit": "💳 Demo deposit is not available.",
        "🏧 Withdrawal": "🏧 Demo withdrawal is not available.",
        "📜 Transaction History": "📜 No transactions yet.",
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
