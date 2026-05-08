# Matrix–Telegram Bridge

Bidirectional text bridge between a Telegram group and a Matrix room. Runs as a single Docker process: a Telegram bot (Aiogram) and a Matrix client (matrix-nio), with state stored in SQLite.

Messages are relayed as `**Display Name**: text` — the sender name is bold via HTML on both sides.

**Media (bidirectional):** photos, documents, video, GIF/animations, audio, voice messages, video notes, stickers (including `.tgs` sent as a file). Matrix uploads go through the content repository; Telegram Bot API limit is ~20 MB per file. Voice messages from Matrix tagged with `org.matrix.msc3245.voice` are sent as voice in Telegram.

## Scaling

No absolute guarantee under arbitrary load: you are bounded by Telegram Bot API rate limits, your Matrix homeserver speed, and a single asyncio process. For a typical number of Telegram group ↔ Matrix room pairs this architecture is fine: SQLite with WAL handles many bridge rows, matrix-nio processes all rooms in one sync loop. If message volume is very high, you may hit delays or throttling — in that case consider splitting into multiple instances with separate bots.

## Requirements

- A working Matrix homeserver and a bot account with an access token.
- An unencrypted Matrix room (`m.room.encryption` must not be set). Encrypted rooms are not supported.
- A Telegram bot token from @BotFather.
- The Telegram bot must be an admin in the target group (or at minimum have permission to read messages/media).

## Quick start

1. Copy `.env.example` to `.env` and fill in the variables (see table below).
2. `docker compose up -d`
3. Add the Telegram bot to the group and grant it admin rights — the bot will print a code and instructions for Matrix.
4. Invite the Matrix bot to the room; after joining it will send a welcome message.
5. In Matrix, send `/tg link <code>` (from step 3). Messages will start relaying in both directions.

The `./data` directory is mounted into the container for the SQLite file.

## Environment variables

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_BOT_USERNAME` | Bot username without `@`, used in the `t.me/…` link |
| `MATRIX_HS_URL` | Homeserver URL (with `https://`) |
| `MATRIX_USER_ID` | Full bot MXID (also shown in Telegram instructions) |
| `MATRIX_ACCESS_TOKEN` | Bot access token |
| `DATABASE_PATH` | Path to SQLite file (Docker default: `/data/bridge.db`) |
| `LINK_CODE_TTL_SECONDS` | Link code lifetime in seconds (default: 3600) |
| `MATRIX_ALLOWED_SERVER` | Optional: restrict linking to rooms on this domain only. Leave unset to allow all. |
| `LOCALE` | Message language: `en` (default) or `ru` |
| `RATE_LIMIT_*` | Anti-abuse thresholds for `/tg link` attempts and code generation |

## Local development (without Docker)

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux / macOS
pip install -r requirements.txt
set DATABASE_PATH=.\data\bridge.db
python -m src.main
```

Create the `data` directory if it does not exist.

## Security

- Do not commit `.env`; pass tokens via environment variables only.
- Link codes are one-time-use with TTL; issuing a new code for the same chat revokes the previous pending code.
- Optionally restrict room domains via `MATRIX_ALLOWED_SERVER`.
