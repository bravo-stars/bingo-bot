process.env.DATABASE_URL = process.env.DATABASE_URL || "postgresql://unused:unused@127.0.0.1:5432/unused";
const test = require("node:test");
const assert = require("node:assert/strict");
const { makeCard, hasWinningPattern } = require("../src/gameEngine");

test("generated card is a valid 5x5 Bingo card with FREE center", () => {
  const card = makeCard("test-round:12");
  assert.equal(card.length, 5);
  assert.ok(card.every(row => row.length === 5));
  assert.equal(card[2][2], "FREE");
  for (let c = 0; c < 5; c++) {
    const [min, max] = [[1,15],[16,30],[31,45],[46,60],[61,75]][c];
    for (let r = 0; r < 5; r++) {
      if (r === 2 && c === 2) continue;
      assert.ok(card[r][c] >= min && card[r][c] <= max);
    }
  }
});

test("same round and Cartela number produce the same card", () => {
  assert.deepEqual(makeCard("4:99"), makeCard("4:99"));
});

test("recognizes a completed row", () => {
  const card = makeCard("row");
  const drawn = card[0].filter(n => n !== "FREE");
  assert.equal(hasWinningPattern(card, drawn), true);
});

test("recognizes a completed column", () => {
  const card = makeCard("column");
  const drawn = card.map(row => row[0]).filter(n => n !== "FREE");
  assert.equal(hasWinningPattern(card, drawn), true);
});

test("recognizes either diagonal", () => {
  const card = makeCard("diagonal");
  const drawn = card.map((row, i) => row[i]).filter(n => n !== "FREE");
  assert.equal(hasWinningPattern(card, drawn), true);
});

test("recognizes four corners", () => {
  const card = makeCard("corners");
  const drawn = [card[0][0], card[0][4], card[4][0], card[4][4]];
  assert.equal(hasWinningPattern(card, drawn), true);
});

test("does not declare a win without a complete pattern", () => {
  const card = makeCard("not-yet");
  assert.equal(hasWinningPattern(card, []), false);
});
