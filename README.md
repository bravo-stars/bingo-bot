# BravoBingo

Telegram Mini App Bingo game with a Node.js server and PostgreSQL (Neon).

## Migration status

The `nodejs-migration` branch is a staged migration from the existing Python service.
The original `main` branch and its Render deployment are intentionally unchanged.

### Node.js runtime in this branch

- Express HTTP server and Telegram Mini App API
- grammY Telegram bot webhook
- PostgreSQL access using the existing Neon schema and data
- Server-owned 30-second Cartela selection window
- Server-owned number draw every 3 seconds
- Up to two Cartelas per player per round; 10 ETB per Cartela
- Server-side row, column, diagonal, and four-corner winner checks
- 80% winner pool / 20% house share
- Existing `webapp/index.html` retained without a redesign

## Required environment variables

- `BOT_TOKEN`: Telegram bot token
- `DATABASE_URL`: existing Neon PostgreSQL connection string
- `RENDER_EXTERNAL_URL`: public Render service URL
- `ADMIN_ID`: administrator Telegram numeric ID

Optional: `PORT` (defaults to 10000), `WEBHOOK_SECRET`, `PG_POOL_MAX`.

## Run

```bash
npm install
npm start
```

Health check: `/health`.

## Important before production cutover

This is not ready to replace the live Python service yet. The deposit submission and approval flow, withdrawal requests and review, transaction-history view, and full admin panel still need to be ported and tested. Do not change the existing Render service's start/build commands until those flows and the Node.js deployment have been verified. The Node branch reuses the existing database schema and must be tested against a backup/staging database before production.
