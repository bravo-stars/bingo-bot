
import os
import asyncio
import random

from telegram import (
    Update, ReplyKeyboardMarkup,
    InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

TOKEN = os.environ["BOT_TOKEN"]

MAIN_MENU = [
    ["🎮 Play"],
    ["💰 Check your balance"],
    ["💳 Deposit"],
    ["🏧 Withdrawal"],
    ["📜 Transaction History"],
    ["📖 Instruction"],
    ["👤 Register"]
]

ROOMS = [
    ["🎮 Play Demo"],
    ["🌟 Super Bingo"],
    ["🎁 BravoBingo Bonus"],
    ["⬅️ Back"]
]

BACK = [["⬅️ Back"]]

players = {}
called = set()
game_started = False
game_finished = False
game_task = None


def number_keyboard(player):
    used = set()
    for card in player["cards"]:
        used.update(card)

    used.update(player["draft"])

    buttons = []
    row = []

    for n in range(1, 76):
        if n in used:
            continue

        mark = "✅" if n in player["draft"] else str(n)
        row.append(
            InlineKeyboardButton(
                mark, callback_data=f"num:{n}"
            )
        )

        if len(row) == 5:
            buttons.append(row)
            row = []

    if row:
        buttons.append(row)

    buttons.append([
        InlineKeyboardButton(
            f"Confirm ({len(player['draft'])}/24)",
            callback_data="done"
        )
    ])

    return InlineKeyboardMarkup(buttons)


def show_card(numbers):
    values = sorted(numbers)
    values.insert(12, 0)

    text = " B    I    N    G    O\n"
    for r in range(5):
        line = []
        for c in range(5):
            n = values[r * 5 + c]
            line.append("FREE" if n == 0 else str(n))
        text += " | ".join(line) + "\n"

    return text


async def choose_cards(update, context):
    if game_started or game_finished:
        await update.message.reply_text(
            "The demo game is already closed."
        )
        return

    user_id = update.effective_user.id

    players[user_id] = {
        "chat_id": update.effective_chat.id,
        "target": 0,
        "cards": [],
        "draft": set(),
        "ready": False
    }

    buttons = [
        [
            InlineKeyboardButton(
                "1 Cartela", callback_data="count:1"
            ),
            InlineKeyboardButton(
                "2 Cartela", callback_data="count:2"
            )
        ]
    ]

    await update.message.reply_text(
        "🎮 PLAY DEMO\nHow many Cartelas do you want?",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def begin_card(query, player):
    player["draft"] = set()
    card_no = len(player["cards"]) + 1

    await query.edit_message_text(
        f"🎟 CARTELA {card_no}\n"
        "Choose exactly 24 numbers from 1 to 75.\n"
        "The center will be FREE.\n"
        "Numbers already used on your first Cartela "
        "cannot be selected again.",
        reply_markup=number_keyboard(player)
    )


def has_won(card):
    values = list(card)
    values.insert(12, 0)

    for r in range(5):
        line = values[r * 5:r * 5 + 5]
        if all(n == 0 or n in called for n in line):
            return "LINE"

    corners = [values[0], values[4], values[20], values[24]]
    if all(n in called for n in corners):
        return "4 CORNERS"

    return None


async def run_game(app):
    global game_started, game_finished, called

    for seconds in (30, 20, 10):
        for player in list(players.values()):
            if player["ready"]:
                await app.bot.send_message(
                    player["chat_id"],
                    f"⏳ Game starts in {seconds} seconds!"
                )
        await asyncio.sleep(10)

    game_started = True

    for player in list(players.values()):
        if player["ready"]:
            await app.bot.send_message(
                player["chat_id"], "🎮 GAME STARTED!"
            )

    numbers = list(range(1, 76))
    random.shuffle(numbers)

    for number in numbers:
        if game_finished:
            break

        called.add(number)
        winners = {}

        for user_id, player in players.items():
            if not player["ready"]:
                continue

            patterns = []
            for index, card in enumerate(player["cards"], 1):
                result = has_won(card)
                if result:
                    patterns.append(f"Cartela {index}: {result}")

            if patterns:
                winners[user_id] = patterns

        for player in list(players.values()):
            if player["ready"]:
                await app.bot.send_message(
                    player["chat_id"],
                    f"🔢 CALLED NUMBER: {number}\n"
                    f"Numbers called: {len(called)}"
                )

        if winners:
            game_finished = True

            result = "🏆 BINGO! WINNER(S):\n"
            for user_id, patterns in winners.items():
                result += (
                    f"Player {user_id}: "
                    + ", ".join(patterns) + "\n"
                )

            for player in list(players.values()):
                if player["ready"]:
                    await app.bot.send_message(
                        player["chat_id"], result
                    )
            return

        await asyncio.sleep(3)

    game_finished = True

    for player in list(players.values()):
        if player["ready"]:
            await app.bot.send_message(
                player["chat_id"],
                "Game finished. No winner."
            )


async def buttons(update, context):
    global game_task

    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    player = players.get(user_id)

    if not player:
        await query.answer(
            "Press Play Demo first.", show_alert=True
        )
        return

    if game_started or game_finished:
        await query.answer(
            "Game already started.", show_alert=True
        )
        return

    data = query.data

    if data.startswith("count:"):
        player["target"] = int(data.split(":")[1])
        await begin_card(query, player)
        return

    if data.startswith("num:"):
        n = int(data.split(":")[1])

        if n in player["draft"]:
            player["draft"].remove(n)
        elif len(player["draft"]) < 24:
            player["draft"].add(n)
        else:
            await query.answer(
                "You already selected 24 numbers.",
                show_alert=True
            )
            return

        await query.edit_message_reply_markup(
            reply_markup=number_keyboard(player)
        )
        return

    if data == "done":
        if len(player["draft"]) != 24:
            await query.answer(
                "Please select exactly 24 numbers.",
                show_alert=True
            )
            return

        player["cards"].append(sorted(player["draft"]))

        if len(player["cards"]) < player["target"]:
            await begin_card(query, player)
            return

        player["ready"] = True

        card_messages = ""
        for i, card in enumerate(player["cards"], 1):
            card_messages += (
                f"\n🎟 CARTELA {i}\n{show_card(card)}"
            )

        await query.edit_message_text(
            "✅ Your Cartela selection is complete!"
            + card_messages
        )

        await context.bot.send_message(
            player["chat_id"],
            "You joined the demo game!\n"
            "The game starts 30 seconds after the first player joins."
        )

        if game_task is None:
            game_task = asyncio.create_task(
                run_game(context.application)
            )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎉 Welcome to Bingo Bot!\nChoose an option:",
        reply_markup=ReplyKeyboardMarkup(
            MAIN_MENU, resize_keyboard=True
        )
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text

    if choice == "🎮 Play":
        await update.message.reply_text(
            "🕹 Choose a room:",
            reply_markup=ReplyKeyboardMarkup(
                ROOMS, resize_keyboard=True
            )
        )
        return

    if choice == "🎮 Play Demo":
        await choose_cards(update, context)
        return

    if choice == "🌟 Super Bingo":
        await update.message.reply_text(
            "🌟 SUPER BINGO\nDemo information only.\n"
            "Win when all numbers on your Cartela are called.",
            reply_markup=ReplyKeyboardMarkup(BACK, resize_keyboard=True)
        )
        return

    if choice == "🎁 BravoBingo Bonus":
        await update.message.reply_text(
            "🎁 BRAVOBINGO BONUS\n"
            "⚽ Football Match Prediction\n"
            "1️⃣ Home win\n2️⃣ Draw\n3️⃣ Away win\n"
            "Demo only. No real payments or prizes.",
            reply_markup=ReplyKeyboardMarkup(BACK, resize_keyboard=True)
        )
        return

    if choice == "⬅️ Back":
        await start(update, context)
        return

    replies = {
        "💰 Check your balance": "Demo balance: 0 ETB",
        "💳 Deposit": "Deposits are not available in demo.",
        "🏧 Withdrawal": "Withdrawals are not available in demo.",
        "📜 Transaction History": "No transactions yet.",
        "📖 Instruction": "Choose a room and follow the instructions.",
        "👤 Register": "Registration is coming soon."
    }

    await update.message.reply_text(
        replies.get(choice, "Please choose a menu button.")
    )


async def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))
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
        webhook_url=f"{external_url}/telegram"
    )

    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
