const { withTransaction } = require("./db");

const SELECTION_SECONDS = 30;
const DRAW_INTERVAL_SECONDS = 6;
const ROUND_PAUSE_SECONDS = 10;
const TICKET_PRICE_CENTS = 1000;

function makeCard(seed = "") {
  // Stable card generation for a round/ticket pair.
  let state = 2166136261;
  for (const ch of String(seed)) state = Math.imul(state ^ ch.charCodeAt(0), 16777619) >>> 0;
  const random = () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 4294967296;
  };
  const columns = [
    [1, 15], [16, 30], [31, 45], [46, 60], [61, 75]
  ].map(([min, max]) => {
    const values = Array.from({ length: max - min + 1 }, (_, i) => min + i);
    for (let i = values.length - 1; i > 0; i--) {
      const j = Math.floor(random() * (i + 1));
      [values[i], values[j]] = [values[j], values[i]];
    }
    return values.slice(0, 5);
  });
  const card = Array.from({ length: 5 }, (_, r) =>
    Array.from({ length: 5 }, (_, c) => columns[c][r])
  );
  card[2][2] = "FREE";
  return card;
}

function hasWinningPattern(card, drawn) {
  const called = new Set(drawn);
  const marked = card.map(row => row.map(value => value === "FREE" || called.has(Number(value))));
  if (marked.some(row => row.every(Boolean))) return true;
  for (let c = 0; c < 5; c++) {
    if (marked.every(row => row[c])) return true;
  }
  if (marked.every((row, i) => row[i])) return true;
  if (marked.every((row, i) => row[4 - i])) return true;
  return marked[0][0] && marked[0][4] && marked[4][0] && marked[4][4];
}

function shuffledNumbers() {
  const values = Array.from({ length: 75 }, (_, i) => i + 1);
  for (let i = values.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [values[i], values[j]] = [values[j], values[i]];
  }
  return values;
}

async function createRound(client, now) {
  const result = await client.query(
    "INSERT INTO game_rounds(status,opens_at,closes_at,created_at) VALUES('open',$1,$2,$3) RETURNING *",
    [now, now + SELECTION_SECONDS, new Date().toISOString()]
  );
  return result.rows[0];
}

async function ensureRound() {
  return withTransaction(async client => {
    const lock = await client.query("SELECT pg_advisory_xact_lock(77192351)");
    const result = await client.query("SELECT * FROM game_rounds ORDER BY id DESC LIMIT 1 FOR UPDATE");
    const row = result.rows[0];
    const now = Date.now() / 1000;
    if (!row || (row.status === "finished" && row.finished_at && now >= Number(row.finished_at) + ROUND_PAUSE_SECONDS)) {
      return createRound(client, now);
    }

    // Make round rollover self-healing on every API read, not only in the
    // background worker. At zero, close selection immediately.
    if (row.status === "open" && now >= Number(row.closes_at)) {
      const count = await client.query(
        "SELECT COUNT(*)::int AS count FROM round_tickets WHERE round_id=$1",
        [row.id]
      );
      if (!count.rows[0].count) {
        return createRound(client, now);
      }
      await client.query(
        "UPDATE game_rounds SET status='drawing',numbers_json=$1,next_draw_at=$2 WHERE id=$3",
        [JSON.stringify(shuffledNumbers()), now + DRAW_INTERVAL_SECONDS, row.id]
      );
      const updated = await client.query("SELECT * FROM game_rounds WHERE id=$1", [row.id]);
      return updated.rows[0];
    }

    return row;
  });
}

async function buyTickets({ userId, userName, ticketNumbers }) {
  if (!Array.isArray(ticketNumbers) || ticketNumbers.length < 1 || ticketNumbers.length > 2 ||
      ticketNumbers.some(n => !Number.isInteger(n) || n < 1 || n > 600) ||
      new Set(ticketNumbers).size !== ticketNumbers.length) {
    const error = new Error("Choose one or two different Cartelas from 1 to 600.");
    error.status = 400;
    throw error;
  }

  return withTransaction(async client => {
    await client.query("SELECT pg_advisory_xact_lock(77192351)");
    const roundResult = await client.query("SELECT * FROM game_rounds ORDER BY id DESC LIMIT 1 FOR UPDATE");
    let round = roundResult.rows[0];
    const now = Date.now() / 1000;
    if (!round) round = await createRound(client, now);
    if (round.status !== "open" || now >= Number(round.closes_at)) {
      const error = new Error("Ticket selection is closed. Wait for the next round.");
      error.status = 409;
      throw error;
    }

    const existingResult = await client.query(
      "SELECT ticket_no FROM round_tickets WHERE round_id=$1 AND user_id=$2",
      [round.id, userId]
    );
    const existing = new Set(existingResult.rows.map(row => Number(row.ticket_no)));
    const added = ticketNumbers.filter(n => !existing.has(n));
    if (existing.size + added.length > 2) {
      const error = new Error("You can own at most two Cartelas in one round.");
      error.status = 400;
      throw error;
    }
    const cost = added.length * TICKET_PRICE_CENTS;
    const walletResult = await client.query(
      "SELECT balance_cents FROM wallets WHERE user_id=$1 FOR UPDATE", [userId]
    );
    const balance = Number(walletResult.rows[0]?.balance_cents || 0);
    if (balance < cost) {
      const error = new Error("Insufficient balance. Each Cartela costs 10 ETB.");
      error.status = 402;
      throw error;
    }
    let balanceAfter = balance;
    if (cost) {
      const updated = await client.query(
        "UPDATE wallets SET balance_cents=balance_cents-$1 WHERE user_id=$2 AND balance_cents >= $1 RETURNING balance_cents",
        [cost, userId]
      );
      if (!updated.rowCount) {
        const error = new Error("Wallet balance is not enough.");
        error.status = 402;
        throw error;
      }
      balanceAfter = Number(updated.rows[0].balance_cents);
      await client.query(
        "INSERT INTO wallet_transactions(user_id,kind,amount_cents,description,created_at) VALUES($1,'TICKET',$2,$3,$4)",
        [userId, -cost, `Round ${round.id}: ${added.length} Cartela(s)`, new Date().toISOString()]
      );
    }
    for (const ticketNo of added) {
      await client.query(
        "INSERT INTO round_tickets(round_id,ticket_no,user_id,card_json,created_at,user_name) VALUES($1,$2,$3,$4,$5,$6)",
        [round.id, ticketNo, userId, JSON.stringify(makeCard(`${round.id}:${ticketNo}`)), new Date().toISOString(), userName]
      );
    }
    const cardsResult = await client.query(
      "SELECT ticket_no,card_json FROM round_tickets WHERE round_id=$1 AND user_id=$2 ORDER BY ticket_no",
      [round.id, userId]
    );
    return {
      round_id: Number(round.id),
      tickets: Object.fromEntries(cardsResult.rows.map(r => [String(r.ticket_no), JSON.parse(r.card_json)])),
      balance_cents: balanceAfter,
      balance_etb: balanceAfter / 100
    };
  });
}

async function getRoundStatus(userId) {
  const round = await ensureRound();
  const result = await withTransaction(async client => {
    const full = await client.query("SELECT * FROM game_rounds WHERE id=$1", [round.id]);
    const all = await client.query(
      "SELECT ticket_no,user_id,user_name,card_json FROM round_tickets WHERE round_id=$1 ORDER BY ticket_no",
      [round.id]
    );
    const wallet = await client.query("SELECT balance_cents FROM wallets WHERE user_id=$1", [userId]);
    const rows = all.rows;
    const mineRows = rows.filter(r => String(r.user_id) === String(userId));
    const game = full.rows[0];
    const winners = game.status === "finished" ? JSON.parse(game.winners_json || "[]") : [];
    const cardsByTicket = new Map(rows.map(r => [Number(r.ticket_no), JSON.parse(r.card_json)]));
    const namesByTicket = new Map(rows.map(r => [Number(r.ticket_no), r.user_name]));
    for (const winner of winners) {
      winner.card = cardsByTicket.get(Number(winner.ticket_no)) || null;
      winner.user_name = winner.user_name || namesByTicket.get(Number(winner.ticket_no)) || "";
    }
    const balance = Number(wallet.rows[0]?.balance_cents || 0);
    return {
      round_id: Number(game.id),
      status: game.status,
      closes_at: Number(game.closes_at),
      server_now: Date.now() / 1000,
      finished_at: game.finished_at === null ? null : Number(game.finished_at),
      next_draw_at: game.next_draw_at === null ? null : Number(game.next_draw_at),
      drawn: JSON.parse(game.drawn_json || "[]"),
      winner_ticket: game.winner_ticket,
      winner_user_id: game.winner_user_id,
      winners,
      winner_card: game.winner_ticket ? cardsByTicket.get(Number(game.winner_ticket)) || null : null,
      winner_name: winners[0]?.user_name || null,
      prize_etb: Number(game.prize_cents || 0) / 100,
      house_etb: Number(game.house_cents || 0) / 100,
      claimed: rows.map(r => Number(r.ticket_no)),
      player_count: new Set(rows.map(r => String(r.user_id))).size,
      prize_pool_etb: rows.length * 8,
      mine: mineRows.map(r => Number(r.ticket_no)),
      balance_etb: balance / 100,
      cards: Object.fromEntries(mineRows.map(r => [String(r.ticket_no), JSON.parse(r.card_json)]))
    };
  });
  return result;
}

async function advanceGame() {
  await withTransaction(async client => {
    await client.query("SELECT pg_advisory_xact_lock(77192351)");
    const result = await client.query("SELECT * FROM game_rounds ORDER BY id DESC LIMIT 1 FOR UPDATE");
    let round = result.rows[0];
    const now = Date.now() / 1000;
    if (!round) {
      await createRound(client, now);
      return;
    }
    if (round.status === "finished") {
      if (round.finished_at && now >= Number(round.finished_at) + ROUND_PAUSE_SECONDS) await createRound(client, now);
      return;
    }
    if (round.status === "open" && now >= Number(round.closes_at)) {
      const count = await client.query("SELECT COUNT(*)::int AS count FROM round_tickets WHERE round_id=$1", [round.id]);
      if (!count.rows[0].count) {
        await createRound(client, now);
      } else {
        await client.query(
          "UPDATE game_rounds SET status='drawing',numbers_json=$1,next_draw_at=$2 WHERE id=$3",
          [JSON.stringify(shuffledNumbers()), now + DRAW_INTERVAL_SECONDS, round.id]
        );
      }
      return;
    }
    if (round.status !== "drawing" || round.next_draw_at === null || now < Number(round.next_draw_at)) return;

    const order = JSON.parse(round.numbers_json || "[]");
    const drawn = JSON.parse(round.drawn_json || "[]");
    if (drawn.length >= 75) {
      await client.query("UPDATE game_rounds SET status='finished',finished_at=$1,next_draw_at=NULL WHERE id=$2", [now, round.id]);
      return;
    }
    drawn.push(order[drawn.length]);
    const ticketResult = await client.query(
      "SELECT ticket_no,user_id,user_name,card_json FROM round_tickets WHERE round_id=$1 ORDER BY ticket_no",
      [round.id]
    );
    const winners = ticketResult.rows.filter(t => hasWinningPattern(JSON.parse(t.card_json), drawn)).map(t => ({
      ticket_no: Number(t.ticket_no),
      user_id: String(t.user_id),
      user_name: t.user_name || "",
      prize_cents: 0
    }));

    if (winners.length) {
      const pool = ticketResult.rows.length * TICKET_PRICE_CENTS;
      const house = Math.floor(pool / 5);
      const winnerPool = pool - house;
      const base = Math.floor(winnerPool / winners.length);
      let remainder = winnerPool % winners.length;
      for (const winner of winners) {
        const share = base + (remainder-- > 0 ? 1 : 0);
        winner.prize_cents = share;
        await client.query(
          "INSERT INTO wallets(user_id,balance_cents) VALUES($1,$2) ON CONFLICT(user_id) DO UPDATE SET balance_cents=wallets.balance_cents+EXCLUDED.balance_cents",
          [winner.user_id, share]
        );
        if (share) await client.query(
          "INSERT INTO wallet_transactions(user_id,kind,amount_cents,description,created_at) VALUES($1,'WIN',$2,$3,$4)",
          [winner.user_id, share, `Round ${round.id} Cartela ${winner.ticket_no} prize`, new Date().toISOString()]
        );
      }
      const adminId = process.env.ADMIN_ID || "7504691975";
      await client.query(
        "INSERT INTO wallets(user_id,balance_cents) VALUES($1,$2) ON CONFLICT(user_id) DO UPDATE SET balance_cents=wallets.balance_cents+EXCLUDED.balance_cents",
        [adminId, house]
      );
      if (house) await client.query(
        "INSERT INTO wallet_transactions(user_id,kind,amount_cents,description,created_at) VALUES($1,'HOUSE',$2,$3,$4)",
        [adminId, house, `Round ${round.id} 20% house share`, new Date().toISOString()]
      );
      await client.query(
        "UPDATE game_rounds SET drawn_json=$1,status='finished',winner_ticket=$2,winner_user_id=$3,winners_json=$4,prize_cents=$5,house_cents=$6,finished_at=$7,next_draw_at=NULL WHERE id=$8",
        [JSON.stringify(drawn), winners[0].ticket_no, winners[0].user_id, JSON.stringify(winners), winnerPool, house, now, round.id]
      );
    } else if (drawn.length >= 75) {
      await client.query(
        "UPDATE game_rounds SET drawn_json=$1,status='finished',finished_at=$2,next_draw_at=NULL WHERE id=$3",
        [JSON.stringify(drawn), now, round.id]
      );
    } else {
      await client.query(
        "UPDATE game_rounds SET drawn_json=$1,next_draw_at=$2 WHERE id=$3",
        [JSON.stringify(drawn), now + DRAW_INTERVAL_SECONDS, round.id]
      );
    }
  });
}

module.exports = {
  SELECTION_SECONDS, DRAW_INTERVAL_SECONDS, TICKET_PRICE_CENTS,
  makeCard, hasWinningPattern, ensureRound, buyTickets, getRoundStatus, advanceGame
};
