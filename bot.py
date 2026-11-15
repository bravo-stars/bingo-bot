import os
import asyncio
import time
import json
import hmac
import hashlib
import sqlite3
import random
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl

from aiohttp import web
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

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
ADMIN_ID = int(os.environ.get("ADMIN_ID", "7504691975"))

MAIN_MENU = [
    ["🎮 Play"],
    ["💰 Check your balance"],
    ["💳 Deposit"],
    ["🏧 Withdrawal"],
    ["📜 Transaction History"],
    ["📖 Instruction"],
    ["👤 Register"]
]
ROOMS = [[KeyboardButton("🎮 Play | VIP", web_app=WebAppInfo(url=BASE_URL + "/"))], ["🌟 Super Bingo | Free"], ["🎁 BravoBingo Bonus"], ["⬅️ Back"]]
DEPOSIT_METHODS = [["📱 TELE-Birr"], ["🏦 CBE-Birr"], ["⬅️ Back"]]


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
        con.execute("""CREATE TABLE IF NOT EXISTS deposit_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            method TEXT NOT NULL,
            sms_text TEXT NOT NULL,
            sms_hash TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending',
            amount_cents INTEGER,
            created_at TEXT NOT NULL,
            reviewed_at TEXT
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS wallets (
            user_id INTEGER PRIMARY KEY,
            balance_cents INTEGER NOT NULL DEFAULT 0
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS bot_users (
            user_id INTEGER PRIMARY KEY, full_name TEXT NOT NULL, username TEXT,
            registered_at TEXT NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS withdrawal_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            method TEXT NOT NULL, amount_cents INTEGER NOT NULL,
            account_details TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL, reviewed_at TEXT
        )""")

        con.execute("""CREATE TABLE IF NOT EXISTS game_rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT NOT NULL,
            opens_at REAL NOT NULL,
            closes_at REAL NOT NULL,
            numbers_json TEXT NOT NULL DEFAULT '[]',
            drawn_json TEXT NOT NULL DEFAULT '[]',
            next_draw_at REAL,
            winner_ticket INTEGER,
            winner_user_id INTEGER,
            finished_at REAL,
            created_at TEXT NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS round_tickets (
            round_id INTEGER NOT NULL,
            ticket_no INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            card_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(round_id, ticket_no)
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_round_tickets_user ON round_tickets(round_id,user_id)")
        # Safe migration for storing every winner in the same draw.
        columns = {r["name"] for r in con.execute("PRAGMA table_info(game_rounds)")}
        if "winners_json" not in columns:
            con.execute("ALTER TABLE game_rounds ADD COLUMN winners_json TEXT NOT NULL DEFAULT '[]'")
        round_columns = {r["name"] for r in con.execute("PRAGMA table_info(round_tickets)")}
        if "user_name" not in round_columns:
            con.execute("ALTER TABLE round_tickets ADD COLUMN user_name TEXT NOT NULL DEFAULT ''")
        if "prize_cents" not in columns:
            con.execute("ALTER TABLE game_rounds ADD COLUMN prize_cents INTEGER NOT NULL DEFAULT 0")
        if "house_cents" not in columns:
            con.execute("ALTER TABLE game_rounds ADD COLUMN house_cents INTEGER NOT NULL DEFAULT 0")
        con.execute("""CREATE TABLE IF NOT EXISTS wallet_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            amount_cents INTEGER NOT NULL,
            description TEXT NOT NULL,
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


def request_user_profile(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("tma "):
        raise web.HTTPUnauthorized(text="Open this page from Telegram.")
    init_data = auth[4:]
    uid = verify_init_data(init_data)
    values = dict(parse_qsl(init_data, keep_blank_values=True))
    try:
        user = json.loads(values.get("user", "{}"))
    except (ValueError, TypeError):
        user = {}
    name = " ".join(x for x in [user.get("first_name", ""), user.get("last_name", "")] if x).strip()
    return uid, (name[:120] or f"Player {uid}")




# ---------- FREE-PLAY ROUND ENGINE ----------
def ensure_round():
    now = time.time()
    with db() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM game_rounds ORDER BY id DESC LIMIT 1").fetchone()
        if row is None or (row["status"] == "finished" and row["finished_at"] is not None and now >= row["finished_at"] + 10):
            cur = con.execute(
                """INSERT INTO game_rounds(status,opens_at,closes_at,created_at)
                   VALUES('open',?,?,?)""",
                (now, now + 30, datetime.now(timezone.utc).isoformat())
            )
            row = con.execute("SELECT * FROM game_rounds WHERE id=?", (cur.lastrowid,)).fetchone()
        con.commit()
        return dict(row)


def has_winning_pattern(card, drawn):
    """Win on any complete row, column, diagonal, or all four corners."""
    called = set(drawn)
    marked = [[value == "FREE" or value in called for value in row] for row in card]
    # Five horizontal and five vertical lines.
    if any(all(row) for row in marked):
        return True
    if any(all(marked[r][c] for r in range(5)) for c in range(5)):
        return True
    # Both diagonals.
    if all(marked[i][i] for i in range(5)) or all(marked[i][4-i] for i in range(5)):
        return True
    # Four corner cells.
    return marked[0][0] and marked[0][4] and marked[4][0] and marked[4][4]


async def game_loop():
    while True:
        try:
            now = time.time()
            con = db()
            try:
                con.execute("BEGIN IMMEDIATE")
                row = con.execute("SELECT * FROM game_rounds ORDER BY id DESC LIMIT 1").fetchone()
                if row is None:
                    con.execute(
                        "INSERT INTO game_rounds(status,opens_at,closes_at,created_at) VALUES('open',?,?,?)",
                        (now, now + 30, datetime.now(timezone.utc).isoformat())
                    )
                elif row["status"] == "finished":
                    if row["finished_at"] is not None and now >= row["finished_at"] + 10:
                        con.execute(
                            "INSERT INTO game_rounds(status,opens_at,closes_at,created_at) VALUES('open',?,?,?)",
                            (now, now + 30, datetime.now(timezone.utc).isoformat())
                        )
                elif row["status"] == "open" and now >= row["closes_at"]:
                    count = con.execute("SELECT COUNT(*) FROM round_tickets WHERE round_id=?", (row["id"],)).fetchone()[0]
                    if count == 0:
                        con.execute("UPDATE game_rounds SET status='finished',finished_at=? WHERE id=?", (now, row["id"]))
                    else:
                        order = random.sample(range(1, 76), 75)
                        con.execute(
                            "UPDATE game_rounds SET status='drawing',numbers_json=?,next_draw_at=? WHERE id=?",
                            (json.dumps(order), now + 3, row["id"])
                        )
                elif row["status"] == "drawing" and row["next_draw_at"] is not None and now >= row["next_draw_at"]:
                    order = json.loads(row["numbers_json"])
                    drawn = json.loads(row["drawn_json"])
                    if len(drawn) < 75:
                        drawn.append(order[len(drawn)])
                        tickets = con.execute(
                            "SELECT ticket_no,user_id,user_name,card_json FROM round_tickets WHERE round_id=? ORDER BY ticket_no",
                            (row["id"],)
                        ).fetchall()
                        winners = []
                        for ticket in tickets:
                            if has_winning_pattern(json.loads(ticket["card_json"]), drawn):
                                winners.append({"ticket_no": ticket["ticket_no"], "user_id": ticket["user_id"],
                                                "user_name": ticket["user_name"]})
                        if winners:
                            # Pool is funded by paid Cartelas; reserve 20% for the house,
                            # and divide the remaining 80% by winning Cartela.
                            ticket_count = len(tickets)
                            pool_cents = ticket_count * 1000
                            house_cents = pool_cents // 5
                            winner_pool = pool_cents - house_cents
                            base_share, remainder = divmod(winner_pool, len(winners))
                            for index, winner in enumerate(winners):
                                share = base_share + (1 if index < remainder else 0)
                                winner["prize_cents"] = share
                                con.execute(
                                    "INSERT INTO wallets(user_id,balance_cents) VALUES(?,?) "
                                    "ON CONFLICT(user_id) DO UPDATE SET balance_cents=balance_cents+excluded.balance_cents",
                                    (winner["user_id"], share)
                                )
                                if share:
                                    con.execute(
                                        "INSERT INTO wallet_transactions(user_id,kind,amount_cents,description,created_at) VALUES(?,?,?,?,?)",
                                        (winner["user_id"], "WIN", share,
                                         f"Round {row['id']} Cartela {winner['ticket_no']} prize",
                                         datetime.now(timezone.utc).isoformat())
                                    )
                            con.execute(
                                "INSERT INTO wallets(user_id,balance_cents) VALUES(?,?) "
                                "ON CONFLICT(user_id) DO UPDATE SET balance_cents=balance_cents+excluded.balance_cents",
                                (ADMIN_ID, house_cents)
                            )
                            if house_cents:
                                con.execute(
                                    "INSERT INTO wallet_transactions(user_id,kind,amount_cents,description,created_at) VALUES(?,?,?,?,?)",
                                    (ADMIN_ID, "HOUSE", house_cents, f"Round {row['id']} 20% house share",
                                     datetime.now(timezone.utc).isoformat())
                                )
                            first = winners[0]
                            con.execute(
                                """UPDATE game_rounds SET drawn_json=?,status='finished',
                                   winner_ticket=?,winner_user_id=?,winners_json=?,prize_cents=?,
                                   house_cents=?,finished_at=?,next_draw_at=NULL WHERE id=?""",
                                (json.dumps(drawn), first["ticket_no"], first["user_id"],
                                 json.dumps(winners), winner_pool, house_cents, now, row["id"])
                            )
                        elif len(drawn) >= 75:
                            con.execute(
                                "UPDATE game_rounds SET drawn_json=?,status='finished',finished_at=?,next_draw_at=NULL WHERE id=?",
                                (json.dumps(drawn), now, row["id"])
                            )
                        else:
                            con.execute(
                                "UPDATE game_rounds SET drawn_json=?,next_draw_at=? WHERE id=?",
                                (json.dumps(drawn), now + 3, row["id"])
                            )
                con.commit()
            except Exception:
                con.rollback()
                logger.exception("Free-play game loop database error")
            finally:
                con.close()
        except Exception:
            logger.exception("Free-play game loop failed")
        await asyncio.sleep(0.5)


# ---------- WEB APP ROUTES ----------
async def home(request):
    return web.FileResponse("webapp/index.html")


async def health(request):
    return web.json_response({"ok": True, "service": "bingo-bot"})


async def ticket_status(request):
    uid = request_user(request)
    round_info = ensure_round()
    with db() as con:
        full_round = con.execute("SELECT * FROM game_rounds WHERE id=?", (round_info["id"],)).fetchone()
        claimed = [r["ticket_no"] for r in con.execute(
            "SELECT ticket_no FROM round_tickets WHERE round_id=? ORDER BY ticket_no", (round_info["id"],)
        )]
        mine_rows = con.execute(
            "SELECT ticket_no,card_json FROM round_tickets WHERE round_id=? AND user_id=? ORDER BY ticket_no",
            (round_info["id"], uid)
        ).fetchall()
    mine = [r["ticket_no"] for r in mine_rows]
    cards = {str(r["ticket_no"]): json.loads(r["card_json"]) for r in mine_rows}
    with db() as con:
        wallet = con.execute("SELECT balance_cents FROM wallets WHERE user_id=?", (uid,)).fetchone()
    balance_etb = (wallet["balance_cents"] if wallet else 0) / 100
    return web.json_response({
        "round_id": round_info["id"],
        "status": round_info["status"],
        "closes_at": round_info["closes_at"],
        "next_draw_at": round_info["next_draw_at"],
        "drawn": json.loads(round_info["drawn_json"]),
        "winner_ticket": round_info["winner_ticket"],
        "winner_user_id": round_info["winner_user_id"],
        "winners": json.loads(round_info["winners_json"] or "[]"),
        "prize_etb": (full_round["prize_cents"] or 0) / 100,
        "house_etb": (full_round["house_cents"] or 0) / 100,
        "claimed": claimed,
        "mine": mine,
        "balance_etb": balance_etb,
        "cards": cards
    })


async def select_tickets(request):
    uid, user_name = request_user_profile(request)
    body = await request.json()
    nums = body.get("tickets")
    if not isinstance(nums, list) or not 1 <= len(nums) <= 2:
        raise web.HTTPBadRequest(text="Choose one or two Cartelas.")
    if any(type(n) is not int or not 1 <= n <= 600 for n in nums) or len(set(nums)) != len(nums):
        raise web.HTTPBadRequest(text="Choose distinct Cartela numbers from 1 to 600.")

    con = db()
    try:
        con.execute("BEGIN IMMEDIATE")
        round_row = con.execute("SELECT * FROM game_rounds ORDER BY id DESC LIMIT 1").fetchone()
        now = time.time()
        if round_row is None or round_row["status"] != "open" or now >= round_row["closes_at"]:
            raise web.HTTPConflict(text="Ticket selection is closed. Wait for the next round.")
        round_id = round_row["id"]
        existing = {r["ticket_no"] for r in con.execute(
            "SELECT ticket_no FROM round_tickets WHERE round_id=? AND user_id=?", (round_id, uid)
        )}
        add = [n for n in nums if n not in existing]
        if len(existing) + len(add) > 2:
            raise web.HTTPBadRequest(text="You can own at most two Cartelas in one round.")
        cost_cents = len(add) * 1000
        wallet = con.execute("SELECT balance_cents FROM wallets WHERE user_id=?", (uid,)).fetchone()
        balance_cents = wallet["balance_cents"] if wallet else 0
        if balance_cents < cost_cents:
            raise web.HTTPPaymentRequired(text=f"Insufficient balance. Each Cartela costs 10 ETB; need {cost_cents/100:.2f} ETB.")
        if cost_cents:
            con.execute("UPDATE wallets SET balance_cents=balance_cents-? WHERE user_id=?", (cost_cents, uid))
            con.execute(
                "INSERT INTO wallet_transactions(user_id,kind,amount_cents,description,created_at) VALUES(?,?,?,?,?)",
                (uid, "TICKET", -cost_cents, f"Round {round_id}: {len(add)} Cartela(s)",
                 datetime.now(timezone.utc).isoformat())
            )
        for n in add:
            try:
                con.execute(
                    "INSERT INTO round_tickets(round_id,ticket_no,user_id,card_json,created_at,user_name) VALUES(?,?,?,?,?,?)",
                    (round_id, n, uid, json.dumps(make_card()), datetime.now(timezone.utc).isoformat(), user_name)
                )
            except sqlite3.IntegrityError:
                raise web.HTTPConflict(text=f"Cartela {n} is already taken. Refresh and choose another.")
        rows = con.execute(
            "SELECT ticket_no,card_json FROM round_tickets WHERE round_id=? AND user_id=? ORDER BY ticket_no",
            (round_id, uid)
        ).fetchall()
        result = {str(r["ticket_no"]): json.loads(r["card_json"]) for r in rows}
        con.commit()
        return web.json_response({"ok": True, "round_id": round_id, "tickets": result})
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

# ---------- TELEGRAM BOT HANDLERS ----------

async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    with db() as con:
        row = con.execute("SELECT balance_cents FROM wallets WHERE user_id=?", (uid,)).fetchone()
    balance = (row["balance_cents"] if row else 0) / 100
    await update.effective_message.reply_text(f"💰 Your BravoBingo balance: {balance:.2f} ETB")


async def play(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("🎮 PLAY — Choose a room:", reply_markup=ReplyKeyboardMarkup(ROOMS, resize_keyboard=True))


async def deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("awaiting_deposit_sms", None)
    await update.effective_message.reply_text("💳 BravoBingo Deposit\nየማስገቢያ መንገድ ይምረጡ:", reply_markup=ReplyKeyboardMarkup(DEPOSIT_METHODS, resize_keyboard=True))


async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as con:
        rows = con.execute("SELECT kind,amount_cents,description,created_at FROM wallet_transactions WHERE user_id=? ORDER BY id DESC LIMIT 10", (update.effective_user.id,)).fetchall()
    if not rows:
        await update.effective_message.reply_text("📜 ገና የግብይት ታሪክ የለም።")
        return
    lines = ["📜 Recent transactions:"]
    for row in rows:
        lines.append(f"{row['created_at'][:16]} | {row['kind']} | {row['amount_cents']/100:.2f} ETB | {row['description']}")
    await update.effective_message.reply_text("\n".join(lines))


async def instructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("📖 BravoBingo Instructions\n1. Deposit money and wait for Admin approval.\n2. Open 🎮 Play | VIP.\n3. Choose 1 or 2 Cartelas (10 ETB each).\n4. Wait for the 30-second selection timer to finish.\n5. Match called numbers to your Cartela.\n6. Winners receive their share in Wallet.")


async def register_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    with db() as con:
        con.execute("INSERT INTO bot_users(user_id,full_name,username,registered_at) VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET full_name=excluded.full_name, username=excluded.username",
                    (user.id, user.full_name[:200], user.username, datetime.now(timezone.utc).isoformat()))
    await update.effective_message.reply_text("✅ BravoBingo account registered. Your Telegram account is linked.")


async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("🏧 Withdrawal method ይምረጡ:", reply_markup=ReplyKeyboardMarkup([["📱 TELE-Birr"], ["🏦 CBE-Birr"], ["⬅️ Back"]], resize_keyboard=True))
    context.user_data["withdrawal_method_pending"] = True


async def approve_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.effective_message.reply_text("Not authorized."); return
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /approve_withdrawal REQUEST_ID"); return
    try: rid = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("Invalid request ID."); return
    with db() as con:
        cur = con.execute("UPDATE withdrawal_requests SET status='paid',reviewed_at=? WHERE id=? AND status='pending'", (datetime.now(timezone.utc).isoformat(), rid))
        row = con.execute("SELECT user_id,amount_cents FROM withdrawal_requests WHERE id=?", (rid,)).fetchone()
    if cur.rowcount != 1:
        await update.effective_message.reply_text("Request not found or already reviewed."); return
    await update.effective_message.reply_text(f"✅ Withdrawal #{rid} marked paid.")
    try: await context.bot.send_message(chat_id=row["user_id"], text=f"✅ Withdrawal #{rid}: {row['amount_cents']/100:.2f} ETB paid.")
    except Exception: logger.exception("Withdrawal notification failed")


async def reject_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.effective_message.reply_text("Not authorized."); return
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /reject_withdrawal REQUEST_ID"); return
    try: rid = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("Invalid request ID."); return
    con = db()
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT user_id,amount_cents,status FROM withdrawal_requests WHERE id=?", (rid,)).fetchone()
        if not row or row["status"] != "pending":
            con.rollback(); await update.effective_message.reply_text("Request not found or already reviewed."); return
        con.execute("UPDATE wallets SET balance_cents=balance_cents+? WHERE user_id=?", (row["amount_cents"], row["user_id"]))
        con.execute("INSERT INTO wallet_transactions(user_id,kind,amount_cents,description,created_at) VALUES(?,?,?,?,?)", (row["user_id"], "WITHDRAWAL_REFUND", row["amount_cents"], f"Withdrawal #{rid} refund", datetime.now(timezone.utc).isoformat()))
        con.execute("UPDATE withdrawal_requests SET status='rejected',reviewed_at=? WHERE id=?", (datetime.now(timezone.utc).isoformat(), rid))
        con.commit()
    except Exception:
        con.rollback(); logger.exception("Withdrawal refund failed"); await update.effective_message.reply_text("Refund failed."); return
    finally: con.close()
    await update.effective_message.reply_text(f"Request #{rid} rejected; {row['amount_cents']/100:.2f} ETB refunded.")
    try: await context.bot.send_message(chat_id=row["user_id"], text=f"❌ Withdrawal #{rid} rejected. {row['amount_cents']/100:.2f} ETB returned to your Wallet.")
    except Exception: logger.exception("Refund notification failed")



async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("awaiting_deposit_sms", None)
    logger.info("/start received from Telegram user %s", update.effective_user.id if update.effective_user else "unknown")
    await update.effective_message.reply_text(
        "🎉 Welcome to BravoBingo!\nChoose an option:",
        reply_markup=ReplyKeyboardMarkup(MAIN_MENU, resize_keyboard=True)
    )


async def deposit_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    withdrawal_step = context.user_data.get("withdrawal_step")
    if withdrawal_step == "amount":
        try:
            amount = Decimal(choice); cents = amount * 100
            if not amount.is_finite() or amount <= 0 or cents != cents.to_integral_value(): raise InvalidOperation()
            amount_cents = int(cents)
        except (InvalidOperation, ValueError):
            await message.reply_text("ትክክለኛ የብር መጠን ያስገቡ።"); return
        with db() as con:
            wallet = con.execute("SELECT balance_cents FROM wallets WHERE user_id=?", (update.effective_user.id,)).fetchone()
        balance = wallet['balance_cents'] if wallet else 0
        if amount_cents > balance:
            await message.reply_text(f"Balanceዎ {balance/100:.2f} ETB ብቻ ነው።"); return
        context.user_data['withdrawal_amount_cents'] = amount_cents
        context.user_data['withdrawal_step'] = 'account'
        await message.reply_text('የሚቀበሉበት ቁጥር እና Account Name ይላኩ።'); return
    if withdrawal_step == 'account':
        uid = update.effective_user.id; amount_cents = int(context.user_data.get('withdrawal_amount_cents', 0)); method = context.user_data.get('withdrawal_method'); details = choice.strip()[:500]
        con = db()
        try:
            con.execute('BEGIN IMMEDIATE')
            cur = con.execute('UPDATE wallets SET balance_cents=balance_cents-? WHERE user_id=? AND balance_cents>=?', (amount_cents, uid, amount_cents))
            if cur.rowcount != 1: con.rollback(); await message.reply_text('በቂ Balance የለዎትም።'); return
            cur = con.execute('INSERT INTO withdrawal_requests(user_id,method,amount_cents,account_details,created_at) VALUES(?,?,?,?,?)', (uid, method, amount_cents, details, datetime.now(timezone.utc).isoformat()))
            rid = cur.lastrowid
            con.execute('INSERT INTO wallet_transactions(user_id,kind,amount_cents,description,created_at) VALUES(?,?,?,?,?)', (uid,'WITHDRAWAL_HOLD',-amount_cents,f'Withdrawal #{rid}',datetime.now(timezone.utc).isoformat()))
            con.commit()
        except Exception:
            con.rollback(); logger.exception('Withdrawal request failed'); await message.reply_text('Withdrawal አልተሳካም።'); return
        finally: con.close()
        for key in ('withdrawal_step','withdrawal_amount_cents','withdrawal_method'): context.user_data.pop(key,None)
        await context.bot.send_message(chat_id=ADMIN_ID, text=f'🏧 WITHDRAWAL #{rid}\\nUser: {uid}\\nMethod: {method}\\nAmount: {amount_cents/100:.2f} ETB\\nAccount: {details}\\nApprove after payment: /approve_withdrawal {rid}\\nReject/refund: /reject_withdrawal {rid}')
        await message.reply_text(f'✅ Withdrawal #{rid} ተመዝግቧል። {amount_cents/100:.2f} ETB ከWallet ተይዟል።'); return

    awaiting_method = context.user_data.get("awaiting_deposit_sms")
    if not awaiting_method:
        return
    if not message or not message.photo:
        return
    uid = update.effective_user.id
    photo = message.photo[-1]
    caption_text = (message.caption or "").strip()
    proof_text = f"[PHOTO] file_unique_id={photo.file_unique_id}\nCaption: {caption_text}"
    proof_hash = hashlib.sha256(f"{uid}:{photo.file_unique_id}".encode("utf-8")).hexdigest()
    try:
        with db() as con:
            cur = con.execute(
                """INSERT INTO deposit_requests
                   (user_id,method,sms_text,sms_hash,created_at)
                   VALUES(?,?,?,?,?)""",
                (uid, awaiting_method, proof_text, proof_hash, datetime.now(timezone.utc).isoformat())
            )
            request_id = cur.lastrowid
    except sqlite3.IntegrityError:
        context.user_data.pop("awaiting_deposit_sms", None)
        await message.reply_text("ይህ Screenshot ከዚህ በፊት ተልኳል። Admin እንዲያረጋግጥ ይጠብቁ።")
        return

    context.user_data.pop("awaiting_deposit_sms", None)
    user = update.effective_user
    admin_caption = (
        f"💳 BravoBingo Deposit Request #{request_id}\n"
        f"User ID: {uid}\n"
        f"Name: {user.full_name}\n"
        f"Username: @{user.username or 'none'}\n"
        f"Method: {awaiting_method}\n"
        f"Caption: {caption_text or 'None'}\n\n"
        f"Approve: /approve_deposit {request_id} AMOUNT_ETB\n"
        f"Reject: /reject_deposit {request_id}"
    )
    try:
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=photo.file_id,
            caption=admin_caption[:1024]
        )
    except Exception:
        logger.exception("Could not notify admin about screenshot deposit request %s", request_id)
        await message.reply_text("Screenshot ተመዝግቧል፣ ግን Admin ማሳወቂያ አልተላከም። እባክዎ Admin ያነጋግሩ።")
        return
    await message.reply_text(
        f"✅ የDeposit ጥያቄዎ #{request_id} ከScreenshot ጋር ተልኳል። Admin ካረጋገጠ በኋላ Wallet ይጨመራል።"
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    choice = (message.text or "").strip()
    normalized = choice.replace("\uFE0F", "").replace("\u200D", "").casefold()
    logger.info("Menu message received: %r (normalized=%r)", choice, normalized)

    awaiting_method = context.user_data.get("awaiting_deposit_sms")
    menu_labels = {
        "🎮 play", "play", "🎮", "play game", "💰 check your balance",
        "💳 deposit", "deposit", "🏧 withdrawal", "📜 transaction history",
        "📖 instruction", "👤 register", "⬅", "⬅️ back",
        "📱 tele-birr", "🏦 cbe-birr"
    }
    if awaiting_method and normalized not in menu_labels and choice:
        uid = update.effective_user.id
        sms_text = choice[:3500]
        sms_hash = hashlib.sha256(sms_text.strip().encode("utf-8")).hexdigest()
        try:
            with db() as con:
                cur = con.execute(
                    """INSERT INTO deposit_requests
                       (user_id,method,sms_text,sms_hash,created_at)
                       VALUES(?,?,?,?,?)""",
                    (uid, awaiting_method, sms_text, sms_hash, datetime.now(timezone.utc).isoformat())
                )
                request_id = cur.lastrowid
        except sqlite3.IntegrityError:
            context.user_data.pop("awaiting_deposit_sms", None)
            await message.reply_text("ይህ SMS ከዚህ በፊት ተልኳል። Admin እንዲያረጋግጥ ይጠብቁ።")
            return
        context.user_data.pop("awaiting_deposit_sms", None)
        user = update.effective_user
        admin_text = (
            f"💳 BravoBingo Deposit Request #{request_id}\n"
            f"User ID: {uid}\n"
            f"Name: {user.full_name}\n"
            f"Username: @{user.username or 'none'}\n"
            f"Method: {awaiting_method}\n\n"
            f"Payment SMS:\n{sms_text}\n\n"
            f"Approve: /approve_deposit {request_id} AMOUNT_ETB\n"
            f"Reject: /reject_deposit {request_id}"
        )
        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=admin_text)
        except Exception:
            logger.exception("Could not notify admin about deposit request %s", request_id)
            await message.reply_text("SMS ተመዝግቧል፣ ግን Admin ማሳወቂያ አልተላከም። እባክዎ Admin ያነጋግሩ።")
            return
        await message.reply_text(
            f"✅ የDeposit ጥያቄዎ #{request_id} ተልኳል። Admin ክፍያውን ካረጋገጠ በኋላ Wallet ይጨመራል።"
        )
        return

    if normalized in {"🎮 play", "play", "🎮", "play game"}:
        await message.reply_text(
            "🎮 PLAY — Choose a room:",
            reply_markup=ReplyKeyboardMarkup(ROOMS, resize_keyboard=True)
        )
    elif "play | free" in normalized or "play | vip" in normalized or "play | 10" in normalized or "play demo" in normalized:
        keys = [[InlineKeyboardButton("🎟 Open VIP Cartela 1–600", web_app=WebAppInfo(url=BASE_URL + "/"))]]
        await message.reply_text(
            "🎮 VIP ROOM\n1–600 ውስጥ እስከ 2 Cartela ምረጥ።\nከታች ያለውን Open VIP Cartela ቁልፍ ጫን።",
            reply_markup=InlineKeyboardMarkup(keys)
        )
    elif "super bingo" in normalized:
        await message.reply_text(
            "🌟 SUPER BINGO\nDemo information only.\nWin when all numbers on your Cartela are called.",
            reply_markup=ReplyKeyboardMarkup([["⬅️ Back"]], resize_keyboard=True)
        )
    elif "bravobingo bonus" in normalized:
        await message.reply_text(
            "🎁 BRAVOBINGO BONUS\n⚽ Football Match Prediction\n1️⃣ Home win\n2️⃣ Draw\n3️⃣ Away win\nDemo only. No real payments or prizes.",
            reply_markup=ReplyKeyboardMarkup([["⬅️ Back"]], resize_keyboard=True)
        )
    elif normalized in {"🏧 withdrawal", "withdrawal"}:
        context.user_data["withdrawal_method_pending"] = True
        await withdraw(update, context)
    elif ("tele-birr" in normalized or "cbe-birr" in normalized) and context.user_data.get("withdrawal_method_pending"):
        context.user_data["withdrawal_method"] = "TELE-Birr" if "tele-birr" in normalized else "CBE-Birr"
        context.user_data.pop("withdrawal_method_pending", None)
        context.user_data["withdrawal_step"] = "amount"
        await message.reply_text("የሚወጣውን ብር መጠን ያስገቡ።")
    elif normalized in {"💳 deposit", "deposit"}:
        context.user_data.pop("awaiting_deposit_sms", None)
        await message.reply_text(
            "💳 BravoBingo Deposit\nየማስገቢያ መንገድ ይምረጡ:",
            reply_markup=ReplyKeyboardMarkup(DEPOSIT_METHODS, resize_keyboard=True)
        )
    elif "tele-birr" in normalized or "cbe-birr" in normalized:
        method = "TELE-Birr" if "tele-birr" in normalized else "CBE-Birr"
        context.user_data["awaiting_deposit_sms"] = method
        await message.reply_text(
            f"💳 {method}\n"
            "ስልክ ቁጥር: 0945723383\n"
            "Account Name: Samuel Adino\n\n"
            "1. በላይ ባለው ቁጥር ክፍያ ይፈጽሙ።\n"
            "2. ከክፍያው በኋላ Screenshot ይላኩ ወይም የደረሰዎትን SMS Copy/Paste ያድርጉ።\n"
            "3. ከሁለቱ አንዱን ብቻ እዚህ ይላኩ።\n\n"
            "Admin ክፍያውን ካረጋገጠ በኋላ Wallet ይጨመራል።",
            reply_markup=ReplyKeyboardMarkup([["⬅️ Back"]], resize_keyboard=True)
        )
    elif "back" in normalized or normalized == "⬅":
        await start(update, context)
    else:
        if choice == "💰 Check your balance":
            with db() as con:
                row = con.execute("SELECT balance_cents FROM wallets WHERE user_id=?", (update.effective_user.id,)).fetchone()
            balance = (row["balance_cents"] if row else 0) / 100
            await message.reply_text(f"💰 Your BravoBingo balance: {balance:.2f} ETB")
            return
        if choice == "📜 Transaction History":
            with db() as con:
                rows = con.execute(
                    "SELECT kind,amount_cents,description,created_at FROM wallet_transactions WHERE user_id=? ORDER BY id DESC LIMIT 10",
                    (update.effective_user.id,)
                ).fetchall()
            if not rows:
                await message.reply_text("No transactions yet.")
            else:
                lines = ["📜 Recent transactions:"]
                for row in rows:
                    lines.append(f"{row['created_at'][:16]} | {row['kind']} | {row['amount_cents']/100:.2f} ETB | {row['description']}")
                await message.reply_text("\n".join(lines))
            return
        if choice == "📖 Instruction":
            await instructions(update, context); return
        if choice == "👤 Register":
            await register_user(update, context); return
        await message.reply_text("Please choose a menu button.")


async def approve_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.effective_message.reply_text("Not authorized.")
        return
    if len(context.args) != 2:
        await update.effective_message.reply_text("Usage: /approve_deposit REQUEST_ID AMOUNT_ETB")
        return
    try:
        request_id = int(context.args[0])
        amount = Decimal(context.args[1])
        if not amount.is_finite() or amount <= 0:
            raise InvalidOperation()
        cents_decimal = amount * 100
        if cents_decimal != cents_decimal.to_integral_value():
            raise InvalidOperation()
        amount_cents = int(cents_decimal)
    except (ValueError, InvalidOperation):
        await update.effective_message.reply_text("Invalid amount. Example: /approve_deposit 12 100")
        return

    con = db()
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT user_id,status FROM deposit_requests WHERE id=?", (request_id,)).fetchone()
        if not row:
            con.rollback()
            await update.effective_message.reply_text("Deposit request not found.")
            return
        if row["status"] != "pending":
            con.rollback()
            await update.effective_message.reply_text(f"Request #{request_id} is already {row['status']}.")
            return
        uid = row["user_id"]
        con.execute(
            "UPDATE deposit_requests SET status='approved',amount_cents=?,reviewed_at=? WHERE id=? AND status='pending'",
            (amount_cents, datetime.now(timezone.utc).isoformat(), request_id)
        )
        con.execute(
            "INSERT INTO wallets(user_id,balance_cents) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET balance_cents=balance_cents+excluded.balance_cents",
            (uid, amount_cents)
        )
        con.execute(
            "INSERT INTO wallet_transactions(user_id,kind,amount_cents,description,created_at) VALUES(?,?,?,?,?)",
            (uid, "DEPOSIT", amount_cents, f"Deposit request #{request_id}", datetime.now(timezone.utc).isoformat())
        )
        con.commit()
    except Exception:
        con.rollback()
        logger.exception("Failed to approve deposit request %s", request_id)
        await update.effective_message.reply_text("Approval failed. Check server logs.")
        return
    finally:
        con.close()
    await update.effective_message.reply_text(f"✅ Request #{request_id} approved: {amount_cents/100:.2f} ETB credited.")
    try:
        await context.bot.send_message(chat_id=uid, text=f"✅ Your deposit #{request_id} was approved. {amount_cents/100:.2f} ETB has been added to your BravoBingo Wallet.")
    except Exception:
        logger.exception("Could not notify user %s about approved deposit", uid)


async def reject_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.effective_message.reply_text("Not authorized.")
        return
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /reject_deposit REQUEST_ID")
        return
    try:
        request_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("Invalid request ID.")
        return
    with db() as con:
        cur = con.execute(
            "UPDATE deposit_requests SET status='rejected',reviewed_at=? WHERE id=? AND status='pending'",
            (datetime.now(timezone.utc).isoformat(), request_id)
        )
        row = con.execute("SELECT user_id FROM deposit_requests WHERE id=?", (request_id,)).fetchone()
    if cur.rowcount != 1:
        await update.effective_message.reply_text("Request not found or already reviewed.")
        return
    await update.effective_message.reply_text(f"Request #{request_id} rejected.")
    if row:
        try:
            await context.bot.send_message(chat_id=row["user_id"], text=f"❌ Your deposit request #{request_id} was rejected. Please contact the admin if you think this is a mistake.")
        except Exception:
            logger.exception("Could not notify user about rejected deposit %s", request_id)


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.effective_message.reply_text("⛔ Admin access only.")
        return
    keyboard = [
        [InlineKeyboardButton("📊 Overview", callback_data="admin:overview"),
         InlineKeyboardButton("💳 Pending deposits", callback_data="admin:deposits")],
        [InlineKeyboardButton("👥 Users", callback_data="admin:users"),
         InlineKeyboardButton("📜 Transactions", callback_data="admin:transactions")],
        [InlineKeyboardButton("🎮 Game status", callback_data="admin:game")]
    ]
    await update.effective_message.reply_text(
        "🛠 BravoBingo ADMIN PANEL\nChoose a section:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def admin_panel_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await query.answer("Admin access only.", show_alert=True)
        return
    await query.answer()
    action = (query.data or "").split(":", 1)[-1]
    with db() as con:
        if action == "overview":
            users = con.execute("SELECT COUNT(*) FROM wallets").fetchone()[0]
            wallet_total = con.execute("SELECT COALESCE(SUM(balance_cents),0) FROM wallets").fetchone()[0] / 100
            pending = con.execute("SELECT COUNT(*) FROM deposit_requests WHERE status='pending'").fetchone()[0]
            tickets = con.execute("SELECT COUNT(*) FROM round_tickets").fetchone()[0]
            text_out = (
                "📊 OVERVIEW\\n"
                f"👥 Wallet users: {users}\\n"
                f"💰 Total wallet balance: {wallet_total:.2f} ETB\\n"
                f"💳 Pending deposits: {pending}\\n"
                f"🎟 Tickets in current database: {tickets}"
            )
        elif action == "deposits":
            rows = con.execute(
                "SELECT id,user_id,method,created_at FROM deposit_requests WHERE status='pending' ORDER BY id DESC LIMIT 10"
            ).fetchall()
            if rows:
                text_out = "💳 PENDING DEPOSITS\\n" + "\\n".join(
                    f"#{x['id']} · User {x['user_id']} · {x['method']} · {x['created_at'][:16]}" for x in rows
                )
                text_out += "\\n\\nApprove: /approve_deposit ID AMOUNT_ETB\\nReject: /reject_deposit ID"
            else:
                text_out = "✅ No pending deposits."
        elif action == "users":
            rows = con.execute(
                "SELECT user_id,balance_cents FROM wallets ORDER BY balance_cents DESC LIMIT 15"
            ).fetchall()
            text_out = "👥 USERS (top 15 by wallet balance)\\n" + (
                "\\n".join(f"User {x['user_id']} · {x['balance_cents']/100:.2f} ETB" for x in rows)
                if rows else "No wallet users yet."
            )
        elif action == "transactions":
            rows = con.execute(
                "SELECT user_id,kind,amount_cents,description,created_at FROM wallet_transactions ORDER BY id DESC LIMIT 10"
            ).fetchall()
            text_out = "📜 LAST 10 TRANSACTIONS\\n" + (
                "\\n".join(f"{x['kind']} · User {x['user_id']} · {x['amount_cents']/100:.2f} ETB · {x['description']}" for x in rows)
                if rows else "No transactions yet."
            )
        elif action == "game":
            row = con.execute("SELECT * FROM game_rounds ORDER BY id DESC LIMIT 1").fetchone()
            if row:
                count = con.execute("SELECT COUNT(*) FROM round_tickets WHERE round_id=?", (row["id"],)).fetchone()[0]
                text_out = (
                    f"🎮 ROUND #{row['id']}\\nStatus: {row['status']}\\n"
                    f"Tickets: {count}\\nNumbers called: {len(json.loads(row['drawn_json']))}"
                )
                if row["status"] == "finished":
                    text_out += f"\\nPrize pool: {(row['prize_cents'] or 0)/100:.2f} ETB"
            else:
                text_out = "No game round yet."
        else:
            text_out = "Unknown admin section."
    keyboard = [[InlineKeyboardButton("⬅️ Admin menu", callback_data="admin:menu")]]
    if action == "menu":
        keyboard = [
            [InlineKeyboardButton("📊 Overview", callback_data="admin:overview"),
             InlineKeyboardButton("💳 Pending deposits", callback_data="admin:deposits")],
            [InlineKeyboardButton("👥 Users", callback_data="admin:users"),
             InlineKeyboardButton("📜 Transactions", callback_data="admin:transactions")],
            [InlineKeyboardButton("🎮 Game status", callback_data="admin:game")]
        ]
        text_out = "🛠 BravoBingo ADMIN PANEL\\nChoose a section:"
    await query.edit_message_text(text_out, reply_markup=InlineKeyboardMarkup(keyboard))


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
    await application.bot.set_my_commands([
        ("start", "Open BravoBingo menu"), ("play", "Play Bingo"),
        ("balance", "Check wallet balance"), ("deposit", "Deposit money"),
        ("withdraw", "Withdraw money"), ("history", "Transaction history"),
        ("instructions", "How to play"), ("register", "Register account")
    ])

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
    webapp["game_task"] = asyncio.create_task(game_loop())
    logger.info("Telegram application started")


async def on_cleanup(webapp):
    application = webapp["telegram"]
    try:
        task = webapp.get("game_task")
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        # Keep the webhook registered during Render restarts/deployments.
        await application.stop()
        await application.shutdown()
        logger.info("Telegram application stopped cleanly")
    except Exception:
        logger.exception("Error during Telegram application shutdown")


def main():
    init_db()

    telegram_app = Application.builder().token(TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("play", play))
    telegram_app.add_handler(CommandHandler("balance", show_balance))
    telegram_app.add_handler(CommandHandler("deposit", deposit))
    telegram_app.add_handler(CommandHandler("withdraw", withdraw))
    telegram_app.add_handler(CommandHandler("history", history))
    telegram_app.add_handler(CommandHandler("instructions", instructions))
    telegram_app.add_handler(CommandHandler("register", register_user))
    telegram_app.add_handler(CommandHandler("admin", admin_panel))
    telegram_app.add_handler(CallbackQueryHandler(admin_panel_action, pattern=r"^admin:"))
    telegram_app.add_handler(CommandHandler("approve_deposit", approve_deposit))
    telegram_app.add_handler(CommandHandler("reject_deposit", reject_deposit))
    telegram_app.add_handler(CommandHandler("approve_withdrawal", approve_withdrawal))
    telegram_app.add_handler(CommandHandler("reject_withdrawal", reject_withdrawal))
    telegram_app.add_handler(MessageHandler(filters.PHOTO, deposit_photo))
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
