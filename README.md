# Dodi Store Bot

A Telegram shop bot for digital products: product catalog, stock delivery,
Binance payment verification, referrals, withdrawals, admin commands, and a
FastAPI dashboard.

## Stack

- Python 3.11+
- aiogram v3
- SQLAlchemy async
- SQLite by default
- FastAPI dashboard

## Quick Start

```sh
cd baba-swift-bot
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python bot.py
```

The same `python bot.py` process starts both the Telegram bot and the dashboard.
For local development, open <http://127.0.0.1:8088> and log in with
`DASHBOARD_PASSWORD`. On Railway, use the service's public URL.

## Configuration

Copy `.env.example` to `.env` and fill in:

| Variable                   | Notes                                                     |
| -------------------------- | --------------------------------------------------------- |
| `BOT_TOKEN`                | Token from `@BotFather`.                                  |
| `ADMIN_IDS`                | Comma-separated Telegram user IDs with admin access.      |
| `BOT_USERNAME`             | Optional. Auto-detected at startup if blank.              |
| `SHOP_NAME`                | Display name shown in bot headers.                        |
| `SUPPORT_USERNAME`         | Telegram username for the support button.                 |
| `BINANCE_UID`              | Binance UID shown on the Binance Pay payment screen.      |
| `BINANCE_API_KEY`          | Binance API key used to verify submitted TxID / Order ID. |
| `BINANCE_SECRET_KEY`       | Binance API secret used for signed verification calls.    |
| `BINANCE_API_BASE_URL`     | Defaults to `https://api.binance.com`.                    |
| `DATABASE_URL`             | Defaults to `sqlite+aiosqlite:///./data/bot.db`.          |
| `DASHBOARD_PASSWORD`       | Password for the local dashboard.                         |
| `DASHBOARD_SESSION_SECRET` | Random string for dashboard cookies.                      |
| `WITHDRAW_MIN`             | Minimum withdrawal amount.                                |
| `WITHDRAW_MAX`             | Maximum withdrawal amount.                                |
| `LOG_LEVEL`                | `INFO`, `DEBUG`, etc.                                     |
| `PREMIUM_BUTTON_ICONS`     | Enables Bot API button icons when supported.              |
| `BUTTON_STYLES_ENABLED`    | Enables Bot API button colors when supported.             |

## User Flow

- **Shop**: browse products, view stock count and price, confirm an order, receive the stock payload.
- **My Profile**: view balance, order history, withdrawals, notifications, and API token.
- **Support**: opens a chat with the configured support username.
- **Refer & Earn**: shows referral stats and sharing tools.

## Admin Commands

```text
/admin
/products
/addproduct slug|Name|emoji|duration|price
/setprice slug 24.99
/setactive slug on|off
/setdesc slug Description text...
/setemoji slug ID
/setemoji slug clear
/addstock slug
/stock slug
/clearstock slug confirm
/delproduct slug confirm

/withdrawals
/approve_wd ID [note]
/reject_wd ID reason

/whois USER_ID
/credit USER_ID 10.00 [note]
/debit USER_ID 5.00 [note]
/ban USER_ID
/unban USER_ID

/broadcast
/getemoji
/reload_emojis
/stats
```

## Dashboard Pages

- **Dashboard**: high-level stats.
- **Products**: add, edit, hide, delete, and restock products.
- **Withdrawals**: approve or reject withdrawal requests.
- **Orders**: recent fulfillment history.
- **Users**: search users, ban/unban, and adjust wallet balances.

## Architecture

```text
src/
  config.py
  main.py
  db/
    models.py
    session.py
    seed.py
  repositories/
  services/
    shop.py
    wallet.py
  ui/
    editor.py
    emoji.py
    keyboards.py
    texts.py
  handlers/
    start.py
    shop.py
    profile.py
    support.py
    refer.py
    admin.py
  admin_dashboard/
    server.py
    templates/
```

## Notes

- Stock is delivered from `stock_items` one row at a time.
- The bot keeps a single-message menu UX by editing the same Telegram message.
- The dashboard always binds to `0.0.0.0` and uses `PORT` (default `8088`).
- `.env` is ignored by git and should stay private.
