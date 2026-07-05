# Batman Bot - Owner / Admin Manual

This guide matches the current build: product browsing, instant stock delivery,
withdrawals, referrals, admin commands, and the local dashboard. Payment handling
is intentionally pending and will be wired later.

## 1. First-Time Setup

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env`:

```text
BOT_TOKEN=your_botfather_token
ADMIN_IDS=your_telegram_user_id
BOT_USERNAME=
SHOP_NAME=Batman Store
SUPPORT_USERNAME=pvtbatman
DASHBOARD_PASSWORD=change_this
DASHBOARD_SESSION_SECRET=change_this_too
```

Start the bot:

```bat
.venv\Scripts\activate
python bot.py
```

The same command starts both the Telegram bot and dashboard. Locally, open
<http://127.0.0.1:8088>. On Railway, open the service's public URL.

## 2. User Flow

Users see these main menu options:

- **Shop**: browse products, choose an item, confirm, and receive credentials.
- **My Profile**: balance, order history, withdrawal requests, notifications, and API token.
- **Support**: opens the configured support Telegram username.
- **Refer & Earn**: referral stats and sharing tools.

The interface edits one Telegram message in place, so the chat stays clean.

## 3. Product Management

```text
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
```

Example:

```text
/addproduct chatgpt_pro_12m|ChatGPT Pro|🤖|12m|19.99
/setdesc chatgpt_pro_12m Premium ChatGPT account, 12-month access
/addstock chatgpt_pro_12m
```

Then upload or paste one stock payload per line:

```text
email1@example.com:password1
email2@example.com:password2
```

Each confirmed order claims one available stock row.

## 4. Withdrawals

Users can request withdrawals from **My Profile**.

Admin commands:

```text
/withdrawals
/approve_wd ID [note]
/reject_wd ID reason
```

Rejecting a withdrawal automatically returns the held amount to the user's wallet.

## 5. Users

```text
/whois USER_ID
/credit USER_ID 10.00 [note]
/debit USER_ID 5.00 [note]
/ban USER_ID
/unban USER_ID
```

Manual credit/debit is still available for admin adjustments.

## 6. Broadcasts

```text
/broadcast
```

After running it, send the next message. The bot forwards that message to active
users with notifications enabled. Use `/cancel` to stop before sending.

## 7. Premium Emojis

```text
/getemoji
/reload_emojis
/setemoji slug ID
/setemoji slug clear
```

To capture an emoji ID, send a premium emoji to the bot and reply with
`/getemoji`. Add the ID to `assets/premium_emojis.json`, then run
`/reload_emojis`.

## 8. Dashboard

The dashboard runs at <http://127.0.0.1:8088> by default.

Pages:

- **Dashboard**: overview stats.
- **Products**: create, edit, hide, delete, and restock products.
- **Withdrawals**: approve or reject requests.
- **Orders**: recent fulfillment history.
- **Users**: search users, ban/unban, and adjust balances.

The dashboard always binds to `0.0.0.0` and uses `PORT` (default `8088`).

## 9. Backup And Reset

- Database: `data/bot.db`
- Backup: copy `data/bot.db`
- Reset: stop `bot.py`, delete `data/bot.db`, start again
- Emoji map: `assets/premium_emojis.json`

## 10. Quick Reference

```text
RUN:
  python bot.py

ADD PRODUCT:
  /addproduct chatgpt_pro_12m|ChatGPT Pro|🤖|12m|19.99
  /setdesc chatgpt_pro_12m Premium account
  /addstock chatgpt_pro_12m

PAUSE PRODUCT:
  /setactive chatgpt_pro_12m off

RESUME PRODUCT:
  /setactive chatgpt_pro_12m on

CHANGE PRICE:
  /setprice chatgpt_pro_12m 24.99

WITHDRAWAL:
  /withdrawals
  /approve_wd 3

USER BALANCE:
  /credit 123456789 25.00 promo
  /debit 123456789 5.00 correction
```
