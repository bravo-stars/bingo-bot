
import os
import asyncio
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)

TOKEN = os.environ["BOT_TOKEN"]

keyboard = [
    ["🎮 Play"],
    ["💰 Check your balance"],
    ["💳 Deposit"],
    ["🏧 Withdrawal"],
    ["📜 Transaction History"],
    ["📖 Instruction"],
    ["👤 Register"]
]

rooms = [
    ["🎮 Play Demo"],
    ["🌟 Super Bingo"],
    ["🎁 BravoBingo Bonus"],
    ["⬅️ Back"]
]

back = [["⬅️ Back"]]

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
            "🕹 PLAY IN:\nChoose a room:",
            reply_markup=ReplyKeyboardMarkup(
                rooms, resize_keyboard=True
            )
        )
        return

    if choice == "🎮 Play Demo":
        await update.message.reply_text(
            "🎮 PLAY DEMO\n"
            "LINE: Complete one horizontal line.\n"
            "4 CORNERS: Complete all four corners.\n"
            "This is a free demo game.",
            reply_markup=ReplyKeyboardMarkup(back, resize_keyboard=True)
        )
        return

    if choice == "🌟 Super Bingo":
        await update.message.reply_text(
            "🌟 SUPER BINGO\n"
            "Entry price: 50 ETB (demo only)\n"
            "The player wins when every number on "
            "their Cartela has been called.\n"
            "This is not a real-money game yet.",
            reply_markup=ReplyKeyboardMarkup(back, resize_keyboard=True)
        )
        return

    if choice == "🎁 BravoBingo Bonus":
        await update.message.reply_text(
            "🎁 BRAVOBINGO BONUS\n"
            "⚽ Football Match Prediction\n"
            "Predict the match result:\n"
            "1️⃣ Home team wins\n"
            "2️⃣ Draw\n"
            "3️⃣ Away team wins\n\n"
            "Demo prize pool: 10,000 ETB\n"
            "Correct predictions share the prize pool.\n"
            "Demo only: no real payments or prizes.",
            reply_markup=ReplyKeyboardMarkup(back, resize_keyboard=True)
        )
        return

    if choice == "⬅️ Back":
        await start(update, context)
        return

    responses = {
        "💰 Check your balance": "💰 Demo balance: 0 ETB",
        "💳 Deposit": "💳 Deposits are not available in demo.",
        "🏧 Withdrawal": "🏧 Withdrawals are not available in demo.",
        "📜 Transaction History": "📜 No transactions yet.",
        "📖 Instruction": "📖 Choose a game room, then follow its instructions.",
        "👤 Register": "👤 Registration is coming soon."
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
