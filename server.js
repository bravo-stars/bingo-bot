const http = require("node:http");
const path = require("node:path");
const express = require("express");
const { Server } = require("socket.io");
const { webhookCallback } = require("grammy");
const { initDb, query, pool } = require("./src/db");
const { requireTelegramUser } = require("./src/auth");
const { ensureRound, getRoundStatus, buyTickets, advanceGame, makeCard } = require("./src/gameEngine");
const { bot } = require("./src/telegram");

const PORT = Number(process.env.PORT || 10000);
const BASE_URL = (process.env.RENDER_EXTERNAL_URL || "").replace(/\/$/, "");
const WEBHOOK_SECRET = process.env.WEBHOOK_SECRET || "";
const app = express();
const server = http.createServer(app);
const io = new Server(server, { cors: { origin: false } });

app.disable("x-powered-by");
app.use(express.json({ limit: "32kb" }));
app.use("/telegram", (req, res, next) => {
  if (WEBHOOK_SECRET && req.get("x-telegram-bot-api-secret-token") !== WEBHOOK_SECRET) {
    return res.sendStatus(403);
  }
  next();
}, webhookCallback(bot, "express"));

app.get("/health", (_req, res) => res.json({ ok: true, service: "bravobingo-node" }));
app.get("/", (_req, res) => res.sendFile(path.join(__dirname, "webapp", "index.html")));
app.use(express.static(path.join(__dirname, "webapp")));

app.get("/api/tickets", requireTelegramUser, async (req, res, next) => {
  try {
    res.set("Cache-Control", "no-store");
    res.json(await getRoundStatus(req.telegramUser.userId));
  } catch (error) { next(error); }
});

app.get("/api/preview", requireTelegramUser, async (req, res, next) => {
  try {
    const raw = String(req.query.numbers || "");
    const numbers = [...new Set(raw.split(",").filter(Boolean).map(Number))].sort((a, b) => a - b);
    if (numbers.length < 1 || numbers.length > 2 || numbers.some(n => !Number.isInteger(n) || n < 1 || n > 600)) {
      return res.status(400).send("Choose one or two Cartelas from 1 to 600.");
    }
    const round = await ensureRound();
    if (round.status !== "open" || Date.now() / 1000 >= Number(round.closes_at)) {
      return res.status(409).send("Cartela selection is closed. Wait for the next round.");
    }
    const sold = await query("SELECT ticket_no FROM round_tickets WHERE round_id=$1 AND ticket_no=ANY($2::int[])", [round.id, numbers]);
    if (sold.rowCount) return res.status(409).send("One of the selected Cartelas is already sold.");
    res.json({ round_id: Number(round.id), cards: Object.fromEntries(numbers.map(n => [String(n), makeCard(`${round.id}:${n}`)])) });
  } catch (error) { next(error); }
});

app.post("/api/select", requireTelegramUser, async (req, res, next) => {
  try {
    const result = await buyTickets({
      userId: req.telegramUser.userId,
      userName: req.telegramUser.userName,
      ticketNumbers: req.body?.tickets
    });
    io.emit("game:update", { round_id: result.round_id });
    res.json({ ok: true, ...result });
  } catch (error) { next(error); }
});

app.use((error, _req, res, _next) => {
  console.error(error);
  const status = Number(error.status || 500);
  res.status(status).send(status >= 500 ? "Server error. Please try again." : error.message);
});

let worker;
let workerClient;
let workerBusy = false;
async function start() {
  await initDb();
  const telegramBot = await bot.api.getMe();
  console.log(`Telegram connected to @${telegramBot.username}`);
  if (telegramBot.username !== "bravobingo1bot") {
    console.warn(`WARNING: BOT_TOKEN belongs to @${telegramBot.username}, expected @bravobingo1bot`);
  }
  await ensureRound();

  // Only one live Render instance may run the draw worker for this database.
  // Hold a session-level PostgreSQL advisory lock for the worker lifetime.
  workerClient = await pool.connect();
  const workerLock = await workerClient.query("SELECT pg_try_advisory_lock(77192352) AS locked");
  if (workerLock.rows[0].locked) {
    console.log("Game worker lock acquired; this instance controls ball drawing.");
    worker = setInterval(async () => {
      if (workerBusy) return;
      workerBusy = true;
      try {
        await advanceGame();
        io.emit("game:tick", { at: Date.now() });
      } catch (error) {
        console.error("Game worker error:", error);
      } finally {
        workerBusy = false;
      }
    }, 500);
  } else {
    console.log("Another instance owns the game worker; ball drawing is disabled on this instance.");
    workerClient.release();
    workerClient = null;
  }

  if (BASE_URL) {
    const options = WEBHOOK_SECRET ? { secret_token: WEBHOOK_SECRET } : undefined;
    await bot.api.setWebhook(`${BASE_URL}/telegram`, options);
  } else {
    console.warn("RENDER_EXTERNAL_URL missing: Telegram webhook was not configured.");
  }
  server.listen(PORT, "0.0.0.0", () => console.log(`BravoBingo Node server listening on ${PORT}`));
}

async function shutdown() {
  if (worker) clearInterval(worker);
  if (workerClient) {
    try {
      await workerClient.query("SELECT pg_advisory_unlock(77192352)");
    } finally {
      workerClient.release();
      workerClient = null;
    }
  }
  await bot.stop();
  server.close();
}
process.once("SIGINT", shutdown);
process.once("SIGTERM", shutdown);

start().catch(error => {
  console.error("Startup failed:", error);
  process.exit(1);
});
