import os
import json
import hmac
import hashlib
import sqlite3
import random
import logging
from datetime import datetime, timezone
from urllib.parse import parse_qsl

from aiohttp import web
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# ---------- LOGGING ----------
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("bingo-bot")

# ---------- SETTINGS ----------
TOKEN = os.environ["BOT_TOKEN"]
BASE_URL = os.environ["RENDER_EXTERNAL_URL"].rstrip("/")
PORT = int(os.environ.get("PORT", "10000"))
DB_PATH = os.environ.get("DATABASE_PATH", "bingo.sqlite3")
WEBHOOK_SECRET = hashlib.sha256(TOKEN.encode()).hexdigest()[:40]

MAIN_MENU = [
    ["🎮 Play"],
    ["💰 Check your balance"],
    ["💳 Deposit"],
    ["🏧 Withdrawal"],
    ["📜 Transaction History"],
    ["📖 Instruction"],
    ["👤 Register"]
]
ROOMS = [["🎮 Play Demo"], ["🌟 Super Bingo"], ["🎁 BravoBingo Bonus"], ["⬅️ Back"]]


# ---------- DATABASE ----------
def db():
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS tickets (
            ticket_no INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            card_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")
    logger.info("Database initialized")


# ---------- BINGO CARDS ----------
def make_card():
    cols = [
        random.sample(range(1, 16), 5),
        random.sample(range(16, 31), 5),
        random.sample(range(31, 46), 5),
        random.sample(range(46, 61), 5),
        random.sample(range(61, 76), 5)
    ]
    grid = [[cols[c][r] for c in range(5)] for r in range(5)]
    grid[2][2] = "FREE"
    return grid


# ---------- TELEGRAM WEB APP SECURITY ----------
def verify_init_data(init_data):
    if not init_data:
        raise web.HTTPUnauthorized(text="Open this page from Telegram.")
    values = dict(parse_qsl(init_data, keep_blank_values=True))
    received = values.pop("hash", None)
    if not received:
        raise web.HTTPUnauthorized(text="Telegram verification missing.")
    check = "\n".join(f"{k}={v}" for k, v in sorted(values.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise web.HTTPUnauthorized(text="Telegram verification failed.")
    try:
        auth_date = int(values.get("auth_date", "0"))
        if abs(datetime.now(timezone.utc).timestamp() - auth_date) > 86400:
            raise ValueError()
        user_id = int(json.loads(values.get("user", "{}"))["id"])
    except (ValueError, KeyError, TypeError):
        raise web.HTTPUnauthorized(text="Please reopen the game from Telegram.")
    return user_id


def request_user(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("tma "):
        raise web.HTTPUnauthorized(text="Open this page from Telegram.")
    return verify_init_data(auth[4:])


# ---------- WEB APP ROUTES ----------
async def home(request):
    return web.FileResponse("webapp/index.html")


async def health(request):
    return web.json_response({"ok": True, "service": "bingo-bot"})


async def ticket_status(request):
    uid = request_user(request)
    with db() as con:
        claimed = [r["ticket_no"] for r in con.execute("SELECT ticket_no FROM tickets")]
        mine = [r["ticket_no"] for r in con.execute(
            "SELECT ticket_no FROM tickets WHERE user_id=? ORDER BY ticket_no", (uid,)
        )]
    return web.json_response({"claimed": claimed, "mine": mine})


async def select_tickets(request):
    uid = request_user(request)
    body = await request.json()
    nums = body.get("tickets")
    if not isinstance(nums, list) or not 1 <= len(nums) <= 2:
        raise web.HTTPBadRequest(text="Choose one or two Cartelas.")
    if any(type(n) is not int or not 1 <= n <= 700 for n in nums) or len(set(nums)) != len(nums):
        raise web.HTTPBadRequest(text="Choose distinct Cartela numbers from 1 to 700.")

    con = db()
    try:
        con.execute("BEGIN IMMEDIATE")
        existing = {r["ticket_no"] for r in con.execute(
            "SELECT ticket_no FROM tickets WHERE user_id=?", (uid,)
        )}
        add = [n for n in nums if n not in existing]
        if len(existing) + len(add) > 2:
            raise web.HTTPBadRequest(text="You can own at most two Cartelas.")
        for n in add:
            try:
                con.execute(
                    "INSERT INTO tickets(ticket_no,user_id,card_json,created_at) VALUES(?,?,?,?)",
                    (n, uid, json.dumps(make_card()), datetime.now(timezone.utc).isoformat())
                )
            except sqlite3.IntegrityError:
                raise web.HTTPConflict(text=f"Cartela {n} is already taken. Refresh and choose another.")
        rows = con.execute(
            "SELECT ticket_no,card_json FROM tickets WHERE user_id=? ORDER BY ticket_no", (uid,)
        ).fetchall()
        result = {str(r["ticket_no"]): json.loads(r["card_json"]) for r in rows}
        con.commit()
        return web.json_response({"ok": True, "tickets": result})
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ---------- TELEGRAM BOT HANDLERS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("/start received from Telegram user %s", update.effective_user.id if update.effective_user else "unknown")
    await update.effective_message.reply_text(
        "🎉 Welcome to Bingo Bot!\nChoose an option:",
        reply_markup=ReplyKeyboardMarkup(MAIN_MENU, resize_keyboard=True)
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    choice = message.text
    logger.info("Menu message received: %s", choice)

    if choice == "🎮 Play":
        keys = [
            [KeyboardButton("🎟 Open Cartela 1–700", web_app=WebAppInfo(url=BASE_URL + "/"))],
            ["🏠 Other Rooms"],
            ["⬅️ Back"]
        ]
        await message.reply_text(
            "🎮 PLAY\n1–700 ያሉ Cartela ቁጥሮችን ለመምረጥ ከታች ያለውን ቁልፍ ተጫን።",
            reply_markup=ReplyKeyboardMarkup(keys, resize_keyboard=True)
        )
    elif choice == "🏠 Other Rooms":
        await message.reply_text(
            "🕹 Choose a room:",
            reply_markup=ReplyKeyboardMarkup(ROOMS, resize_keyboard=True)
        )
    elif choice == "🎮 Play Demo":
        keys = [
            [KeyboardButton("🎟 Open Cartela 1–700", web_app=WebAppInfo(url=BASE_URL + "/"))],
            ["⬅️ Back"]
        ]
        await message.reply_text(
            "🎮 PLAY DEMO\n1–700 ውስጥ እስከ 2 Cartela ምረጥ።",
            reply_markup=ReplyKeyboardMarkup(keys, resize_keyboard=True)
        )
    elif choice == "🌟 Super Bingo":
        await message.reply_text(
            "🌟 SUPER BINGO\nDemo information only.\nWin when all numbers on your Cartela are called.",
            reply_markup=ReplyKeyboardMarkup([["⬅️ Back"]], resize_keyboard=True)
        )
    elif choice == "🎁 BravoBingo Bonus":
        await message.reply_text(
            "🎁 BRAVOBINGO BONUS\n⚽ Football Match Prediction\n1️⃣ Home win\n2️⃣ Draw\n3️⃣ Away win\nDemo only. No real payments or prizes.",
            reply_markup=ReplyKeyboardMarkup([["⬅️ Back"]], resize_keyboard=True)
        )
    elif choice == "⬅️ Back":
        await start(update, context)
    else:
        replies = {
            "💰 Check your balance": "Demo balance: 0 ETB",
            "💳 Deposit": "Deposits are not available in demo.",
            "🏧 Withdrawal": "Withdrawals are not available in demo.",
            "📜 Transaction History": "No transactions yet.",
            "📖 Instruction": "Choose a room and follow the instructions.",
            "👤 Register": "Registration is coming soon."
        }
        await message.reply_text(replies.get(choice, "Please choose a menu button."))


async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Telegram handler failed", exc_info=context.error)


# ---------- TELEGRAM WEBHOOK ----------
async def telegram_webhook(request):
    received_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(received_secret, WEBHOOK_SECRET):
        logger.warning("Rejected webhook request: invalid secret header")
        raise web.HTTPUnauthorized()

    try:
        data = await request.json()
        update = Update.de_json(data, request.app["telegram"].bot)
        logger.info("Webhook update received: update_id=%s", update.update_id)
        await request.app["telegram"].update_queue.put(update)
        return web.Response(text="ok")
    except Exception:
        logger.exception("Could not process incoming Telegram webhook request")
        raise web.HTTPInternalServerError(text="Webhook processing failed")


async def on_startup(webapp):
    application = webapp["telegram"]
    await application.initialize()

    bot_info = await application.bot.get_me()
    logger.info("Connected to Telegram bot: @%s (id=%s)", bot_info.username, bot_info.id)

    webhook_url = BASE_URL + "/telegram"
    await application.bot.set_webhook(
        url=webhook_url,
        secret_token=WEBHOOK_SECRET,
        allowed_updates=Update.ALL_TYPES
    )
    webhook_info = await application.bot.get_webhook_info()
    logger.info(
        "Webhook configured: url=%s pending_updates=%s last_error=%s",
        webhook_info.url,
        webhook_info.pending_update_count,
        webhook_info.last_error_message
    )

    await application.start()
    logger.info("Telegram application started")


async def on_cleanup(webapp):
    application = webapp["telegram"]
    try:
        await application.bot.delete_webhook()
        await application.stop()
        await application.shutdown()
        logger.info("Telegram application stopped cleanly")
    except Exception:
        logger.exception("Error during Telegram application shutdown")


def main():
    init_db()

    telegram_app = Application.builder().token(TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu))
    telegram_app.add_error_handler(handle_error)

    webapp = web.Application(client_max_size=1024 * 1024)
    webapp["telegram"] = telegram_app
    webapp.router.add_get("/", home)
    webapp.router.add_get("/health", health)
    webapp.router.add_get("/api/tickets", ticket_status)
    webapp.router.add_post("/api/select", select_tickets)
    webapp.router.add_post("/telegram", telegram_webhook)
    webapp.on_startup.append(on_startup)
    webapp.on_cleanup.append(on_cleanup)

    logger.info("Starting Bingo Bot web server on port %s", PORT)
    web.run_app(webapp, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
