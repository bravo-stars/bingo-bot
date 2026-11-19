const { Bot, InlineKeyboard, Keyboard } = require("grammy");
const { query } = require("./db");

const TOKEN = process.env.BOT_TOKEN;
const BASE_URL = (process.env.RENDER_EXTERNAL_URL || "").replace(/\/$/, "");
const ADMIN_ID = String(process.env.ADMIN_ID || "7504691975");
if (!TOKEN) throw new Error("BOT_TOKEN is required");
if (!BASE_URL) throw new Error("RENDER_EXTERNAL_URL is required");

const bot = new Bot(TOKEN);
const mainMenu = new Keyboard()
  .text("🎮 Play").row()
  .text("💰 Check your balance").row()
  .text("💳 Deposit").row()
  .text("🏧 Withdrawal").row()
  .text("📜 Transaction History").row()
  .text("📖 Instruction").row()
  .text("👤 Register").resized();

bot.command("start", async ctx => {
  const user = ctx.from;
  await query(
    `INSERT INTO bot_users(user_id,full_name,username,registered_at)
     VALUES($1,$2,$3,$4)
     ON CONFLICT(user_id) DO UPDATE SET full_name=EXCLUDED.full_name, username=EXCLUDED.username`,
    [String(user.id), [user.first_name, user.last_name].filter(Boolean).join(" "), user.username || null, new Date().toISOString()]
  );
  await ctx.reply("🎉 Welcome to BravoBingo! Choose an option:", { reply_markup: mainMenu });
});

bot.hears("🎮 Play", async ctx => {
  const keyboard = new InlineKeyboard().webApp("🎮 Open VIP Bingo", BASE_URL + "/");
  await ctx.reply("Choose your Bingo room:", { reply_markup: keyboard });
});

bot.hears("💰 Check your balance", async ctx => {
  const result = await query("SELECT balance_cents FROM wallets WHERE user_id=$1", [String(ctx.from.id)]);
  const balance = Number(result.rows[0]?.balance_cents || 0) / 100;
  await ctx.reply(`💰 Your BravoBingo balance: ${balance.toFixed(2)} ETB`);
});

bot.hears("📖 Instruction", async ctx => {
  await ctx.reply(
    "🎯 BravoBingo VIP Rules\n" +
    "• Each Cartela costs 10 ETB.\n" +
    "• Choose up to 2 Cartelas during the 30-second selection period.\n" +
    "• The server calls numbers every 3 seconds.\n" +
    "• Row, column, diagonal, or four corners wins.\n" +
    "• 80% of the ticket pool goes to winners; 20% is the house share."
  );
});

bot.hears("👤 Register", async ctx => {
  const user = ctx.from;
  await query(
    `INSERT INTO bot_users(user_id,full_name,username,registered_at)
     VALUES($1,$2,$3,$4)
     ON CONFLICT(user_id) DO UPDATE SET full_name=EXCLUDED.full_name, username=EXCLUDED.username`,
    [String(user.id), [user.first_name, user.last_name].filter(Boolean).join(" "), user.username || null, new Date().toISOString()]
  );
  await ctx.reply("✅ Your BravoBingo account is registered.");
});

// Deposit, withdrawal, transaction-history and admin review handlers are
// deliberately kept out of this first runtime cutover. They must be ported
// before production traffic is switched from the existing Python service.
bot.catch(error => console.error("Telegram bot error:", error));
module.exports = { bot, ADMIN_ID };
