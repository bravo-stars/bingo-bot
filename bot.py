import os
import asyncio
import random

from telegram import (
    Update, ReplyKeyboardMarkup,
    InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
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

game = {
    "players": {},
    "called": set(),
    "started": False,
    "finished": False,
    "countdown_task": None
}


def make_card():
    columns = [
        random.sample(range(1, 16), 5),
        random.sample(range(16, 31), 5),
        random.sample(range(31, 46), 5),
        random.sample(range(46, 61), 5),
        random.sample(range(61, 76), 5)
    ]
    columns[2][2] = 0
    return [
        [columns[c][r] for c in range(5)]
        for r in range(5)
    ]


def card_text(card):
    text = " B   I   N   G   O\n"
    for row in card:
        text += " ".join(
            "⭐" if n == 0 else f"{n:2}"
            for n in row
        ) + "\n"
    return text


def picker_keyboard(player):
    buttons = []
    for i in range(6):
        mark = "✅" if i in player["selected"] else "⬜"
        buttons.append([
            InlineKeyboardButton(
                f"{mark} Cartela {i + 1}",
                callback_data=f"pick:{i}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "✅ Confirm selection",
            callback_data="confirm"
        )
    ])
    return InlineKeyboardMarkup(buttons)


async def show_picker(update, context):
    user_id = update.effective_user.id

    if game["started"] or game["finished"]:
        await update.message.reply_text(
            "⏳ This demo game has already started or finished."
        )
        return

    if user_id not in game["players"]:
        game["players"][user_id] = {
            "chat_id": update.effective_chat.id,
            "options": [make_card() for _ in range(6)],
            "selected": set(),
            "cards": [],
            "confirmed": False
        }

    player = game["players"][user_id]

    await update.message.reply_text(
        "🎮 Choose up to 2 Cartelas from these 6:\n\n"
        + "\n".join(
            f"Cartela {i + 1}\n{card_text(card)}"
            for i, card in enumerate(player["options"])
        ),
        reply_markup=picker_keyboard(player)
    )


async def start_countdown(app):
    for seconds in (30, 20, 10, 5):
        if game["finished"]:
            return
        for player in game["players"].values():
            if player["confirmed"]:
                await app.bot.send_message(
                    player["chat_id"],
                    f"⏳ Demo game starts in {seconds} seconds!"
                )
        await asyncio.sleep(10 if seconds != 5 else 5)

    game["started"] = True

    for player in game["players"].values():
        if player["confirmed"]:
            await app.bot.send_message(
                player["chat_id"],
                "🎮 GAME STARTED!\nNumbers will be called every 3 seconds."
            )

    numbers = list(range(1, 76))
    random.shuffle(numbers)

    for number in numbers:
        if game["finished"]:
            return

        game["called"].add(number)

        winners = []
        for user_id, player in game["players"].items():
            if not player["confirmed"]:
                continue

            for card_number, card in enumerate(player["cards"], 1):
                patterns = []

                for row in card:
                    if all(n == 0 or n in game["called"] for n in row):
                        patterns.append("LINE")

                corners = [
                    card[0][0], card[0][4],
                    card[4][0], card[4][4]
                ]
                if all(n in game["called"] for n in corners):
                    patterns.append("4 CORNERS")

                if patterns:
                    winners.append(
                        (player["chat_id"], card_number, patterns)
                    )

        message = f"🔢 Number called: {number}"
        for player in game["players"].values():
            if player["confirmed"]:
                await app.bot.send_message(player["chat_id"], message)

        if winners:
            game["finished"] = True
            result = "🏆 GAME OVER! WINNER(S):\n"
            for chat_id, card_number, patterns in winners:
                result += (
                    f"Cartela {card_number}: "
                    f"{', '.join(patterns)}\n"
                )

            for player in game["players"].values():
                if player["confirmed"]:
                    await app.bot.send_message(
                        player["chat_id"], result
                    )
            return

        await asyncio.sleep(3)

    game["finished"] = True
    for player in game["players"].values():
        if player["confirmed"]:
            await app.bot.send_message(
                player["chat_id"],
                "Game finished. No winner this time."
            )


async def button_handler(update, context):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    player = game["players"].get(user_id)

    if not player:
        await query.answer("Please press Play Demo first.", show_alert=True)
        return

    if game["started"] or game["finished"]:
        await query.edit_message_text("This game is already closed.")
        return

    if query.data.startswith("pick:"):
        if player["confirmed"]:
            return

        index = int(query.data.split(":")[1])

        if index in player["selected"]:
            player["selected"].remove(index)
        elif len(player["selected"]) < 2:
            player["selected"].add(index)
        else:
            await query.answer(
                "You can select only 2 Cartelas.",
                show_alert=True
            )
            return

        await query.edit_message_reply_markup(
            reply_markup=picker_keyboard(player)
        )
        return

    if query.data == "confirm":
        if not player["selected"]:
            await query.answer(
                "Choose at least 1 Cartela.",
                show_alert=True
            )
            return

        player["cards"] = [
            player["options"][i]
            for i in sorted(player["selected"])
        ]
        player["confirmed"] = True

        await query.edit_message_text(
            "✅ Cartela selection confirmed!\n"
            f"You selected {len(player['cards'])} Cartela(s).\n"
            "Wait for the game to start."
        )

        if game["countdown_task"] is None:
            game["countdown_task"] = asyncio.create_task(
                start_countdown(context.application)
            )


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
        await show_picker(update, context)
        return

    if choice == "🌟 Super Bingo":
        await update.message.reply_text(
            "🌟 SUPER BINGO\n"
            "Demo only. The player wins when every number "
            "on their Cartela has been called.",
            reply_markup=ReplyKeyboardMarkup(back, resize_keyboard=True)
        )
        return

    if choice == "🎁 BravoBingo Bonus":
        await update.message.reply_text(
            "🎁 BRAVOBINGO BONUS\n"
            "⚽ Football Match Prediction\n"
            "1️⃣ Home team wins\n"
            "2️⃣ Draw\n"
            "3️⃣ Away team wins\n\n"
            "Demo prize pool: 10,000 ETB\n"
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
        CallbackQueryHandler(button_handler)
    )
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
