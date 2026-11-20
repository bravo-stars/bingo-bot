const crypto = require("node:crypto");

const BOT_TOKEN = process.env.BOT_TOKEN;
if (!BOT_TOKEN) throw new Error("BOT_TOKEN is required");

function verifyTelegramInitData(initData) {
  if (!initData) {
    const error = new Error("Open this page from the Telegram bot.");
    error.status = 401;
    throw error;
  }
  const params = new URLSearchParams(initData);
  const receivedHash = params.get("hash");
  if (!receivedHash) {
    const error = new Error("Telegram verification missing.");
    error.status = 401;
    throw error;
  }
  params.delete("hash");
  const dataCheckString = [...params.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, value]) => `${key}=${value}`)
    .join("\n");
  const secretKey = crypto.createHmac("sha256", BOT_TOKEN).update("WebAppData").digest();
  const calculatedHash = crypto.createHmac("sha256", secretKey).update(dataCheckString).digest("hex");
  const a = Buffer.from(calculatedHash, "hex");
  const b = Buffer.from(receivedHash, "hex");
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) {
    const error = new Error("Telegram verification failed.");
    error.status = 401;
    throw error;
  }
  const authDate = Number(params.get("auth_date") || 0);
  if (!authDate || Math.abs(Date.now() / 1000 - authDate) > 86400) {
    const error = new Error("Please reopen the game from Telegram.");
    error.status = 401;
    throw error;
  }
  let user;
  try {
    user = JSON.parse(params.get("user") || "{}");
  } catch {
    user = null;
  }
  if (!user || !Number.isSafeInteger(Number(user.id))) {
    const error = new Error("Telegram user data is invalid.");
    error.status = 401;
    throw error;
  }
  const fullName = [user.first_name, user.last_name].filter(Boolean).join(" ").trim();
  return { userId: String(user.id), userName: (fullName || `Player ${user.id}`).slice(0, 120), user };
}

function requireTelegramUser(req, res, next) {
  try {
    const header = req.get("authorization") || "";
    if (!header.startsWith("tma ")) {
      const error = new Error("Open this page from Telegram.");
      error.status = 401;
      throw error;
    }
    req.telegramUser = verifyTelegramInitData(header.slice(4));
    next();
  } catch (error) {
    next(error);
  }
}

module.exports = { verifyTelegramInitData, requireTelegramUser };
