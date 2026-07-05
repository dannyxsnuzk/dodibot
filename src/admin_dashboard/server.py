"""FastAPI application served by the single Uvicorn instance in ``bot.py``."""
from __future__ import annotations

import logging
import secrets
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import bcrypt
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import (
    ApiToken,
    DepositOrder,
    Order,
    PaymentVerification,
    Product,
    StockItem,
    Transaction,
    User,
    VerificationLog,
    Withdrawal,
)
from ..repositories import payments as payments_repo
from ..db.session import SessionLocal
from ..repositories import products as products_repo
from ..repositories import withdrawals as wd_repo
from ..services.shop import BuyError, OutOfStock, buy_product_quantity
from ..services import wallet
from ..services.deposit_settings import get_deposit_settings, update_deposit_settings

log = logging.getLogger("dashboard")

settings = get_settings()
ROOT = Path(__file__).parent
templates = Jinja2Templates(directory=str(ROOT / "templates"))
serializer = URLSafeSerializer(settings.dashboard_session_secret, salt="batman-bot")

PASSWORD_HASH = bcrypt.hashpw(
    settings.dashboard_password.encode("utf-8"), bcrypt.gensalt()
)

app = FastAPI(title="Batman Admin", docs_url=None, redoc_url=None)


# ───────── auth ─────────────────────────────────────────────────────────────

def _check_password(pw: str) -> bool:
    return bcrypt.checkpw(pw.encode("utf-8"), PASSWORD_HASH)


def _make_session_cookie() -> str:
    return serializer.dumps({"a": True, "n": secrets.token_hex(8)})


def _is_authed(request: Request) -> bool:
    cookie = request.cookies.get("dash_session")
    if not cookie:
        return False
    try:
        data = serializer.loads(cookie)
    except BadSignature:
        return False
    return bool(data.get("a"))


async def require_auth(request: Request) -> None:
    if not _is_authed(request):
        raise HTTPException(status_code=303, headers={"Location": "/login"})


# ───────── DB session helper ────────────────────────────────────────────────

async def get_db() -> AsyncSession:
    async with SessionLocal() as session:
        yield session


# ───────── routes ───────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, error: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html", {"error": error})


@app.post("/login")
async def login_submit(password: str = Form(...)) -> Response:
    if not _check_password(password):
        return RedirectResponse("/login?error=1", status_code=303)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        "dash_session", _make_session_cookie(),
        httponly=True, samesite="lax", max_age=60 * 60 * 24 * 7,
    )
    return resp


@app.get("/logout")
async def logout() -> Response:
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("dash_session")
    return resp


@app.get("/", response_class=HTMLResponse)
async def home(request: Request, db: AsyncSession = Depends(get_db),
               _: None = Depends(require_auth)) -> HTMLResponse:
    n_users = int(await db.scalar(select(func.count(User.id))) or 0)
    n_orders = int(await db.scalar(select(func.count(Order.id))) or 0)
    order_value = Decimal(str(await db.scalar(select(func.coalesce(func.sum(Order.price_usdt), 0))) or 0))
    n_wd_pending = int(await db.scalar(
        select(func.count(Withdrawal.id)).where(Withdrawal.status == "pending")
    ) or 0)
    n_stock = int(await db.scalar(
        select(func.count(StockItem.id)).where(StockItem.status == "available")
    ) or 0)
    return templates.TemplateResponse(request, "home.html", {
        "n_users": n_users, "n_orders": n_orders, "order_value": order_value,
        "n_wd_pending": n_wd_pending,
        "n_stock": n_stock,
    })


# ----- Products ----------

@app.get("/products", response_class=HTMLResponse)
async def products_page(
    request: Request,
    cleared: int | None = None,
    slug: str | None = None,
    deleted: str | None = None,
    delerr: str | None = None,
    added: int | None = None,
    err: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_auth),
) -> HTMLResponse:
    rows = await products_repo.list_active_products_with_stock(db)
    inactive = (await db.scalars(select(Product).where(Product.is_active.is_(False)))).all()
    # Order counts (so the template can show whether Delete is safe).
    all_ids = [p.id for p, _ in rows] + [p.id for p in inactive]
    order_counts: dict[int, int] = {}
    if all_ids:
        rows_oc = (await db.execute(
            select(Order.product_id, func.count(Order.id))
            .where(Order.product_id.in_(all_ids))
            .group_by(Order.product_id)
        )).all()
        order_counts = {pid: int(c) for pid, c in rows_oc}
    flash: str | None = None
    if cleared is not None and slug:
        flash = f"Cleared {cleared} unsold stock items from {slug}."
    elif added is not None and slug:
        flash = f"Added {added} stock items to {slug}."
    elif deleted:
        flash = f"Product {deleted} permanently deleted."
    elif delerr:
        flash = (f"Cannot delete {delerr} — it has order history. "
                 "Use the inactive toggle to hide it instead.")
    elif err == "price":
        flash = "Invalid price — please enter a number."
    return templates.TemplateResponse(request, "products.html", {
        "products": rows,
        "inactive": inactive,
        "order_counts": order_counts,
        "flash": flash,
    })


@app.post("/products/new")
async def products_new(
    slug: str = Form(...),
    display_name: str = Form(...),
    emoji: str = Form("📦"),
    emoji_id: str = Form(""),
    duration_label: str = Form("12m"),
    price_usdt: str = Form("0"),
    description: str = Form(""),
    sort_order: int = Form(0),
    is_active: str = Form("on"),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_auth),
) -> Response:
    try:
        price = Decimal(price_usdt)
    except InvalidOperation:
        return RedirectResponse("/products?err=price", status_code=303)
    await products_repo.upsert_product(
        db, slug=slug, display_name=display_name, emoji=emoji,
        duration_label=duration_label, price_usdt=price,
        description=description, sort_order=sort_order,
        is_active=is_active == "on",
        emoji_id=(emoji_id or "").strip() or None,
    )
    return RedirectResponse("/products", status_code=303)


@app.post("/products/{slug}/update")
async def products_update(
    slug: str,
    display_name: str = Form(...),
    emoji: str = Form("📦"),
    emoji_id: str = Form(""),
    duration_label: str = Form("12m"),
    price_usdt: str = Form("0"),
    description: str = Form(""),
    sort_order: int = Form(0),
    is_active: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_auth),
) -> Response:
    p = await db.scalar(select(Product).where(Product.slug == slug))
    if p is None:
        raise HTTPException(404)
    try:
        p.price_usdt = Decimal(price_usdt)
    except InvalidOperation:
        return RedirectResponse("/products?err=price", status_code=303)
    p.display_name = display_name
    p.emoji = emoji
    p.emoji_id = (emoji_id or "").strip() or None
    p.duration_label = duration_label
    p.description = description
    p.sort_order = sort_order
    p.is_active = bool(is_active)
    await db.commit()
    return RedirectResponse("/products", status_code=303)


@app.post("/products/{slug}/clearstock")
async def products_clearstock(
    slug: str,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_auth),
) -> Response:
    p = await db.scalar(select(Product).where(Product.slug == slug))
    if p is None:
        raise HTTPException(404)
    n = await products_repo.clear_available_stock(db, p.id)
    return RedirectResponse(f"/products?cleared={n}&slug={slug}", status_code=303)


@app.post("/products/{slug}/delete")
async def products_delete(
    slug: str,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_auth),
) -> Response:
    p = await db.scalar(select(Product).where(Product.slug == slug))
    if p is None:
        raise HTTPException(404)
    ok, _msg = await products_repo.delete_product(db, p.id)
    if not ok:
        return RedirectResponse(f"/products?delerr={slug}", status_code=303)
    return RedirectResponse(f"/products?deleted={slug}", status_code=303)


@app.post("/products/{slug}/stock")
async def products_stock_upload(
    slug: str,
    paste: str = Form(""),
    file: UploadFile | None = File(None),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_auth),
) -> Response:
    p = await db.scalar(select(Product).where(Product.slug == slug))
    if p is None:
        raise HTTPException(404)
    lines: list[str] = []
    if paste.strip():
        lines.extend(paste.splitlines())
    if file is not None and file.filename:
        content = (await file.read()).decode("utf-8", errors="replace")
        lines.extend(content.splitlines())
    n = await products_repo.add_stock_lines(db, p.id, lines)
    return RedirectResponse(f"/products?added={n}&slug={slug}", status_code=303)


# ----- Withdrawals ----------

@app.get("/withdrawals", response_class=HTMLResponse)
async def withdrawals_page(request: Request, db: AsyncSession = Depends(get_db),
                           _: None = Depends(require_auth)) -> HTMLResponse:
    pending = await wd_repo.get_pending_withdrawals(db, limit=200)
    decided = (await db.scalars(
        select(Withdrawal).where(Withdrawal.status != "pending").order_by(desc(Withdrawal.id)).limit(50)
    )).all()
    return templates.TemplateResponse(request, "withdrawals.html", {
        "pending": pending, "decided": decided,
    })


@app.post("/withdrawals/{wid}/approve")
async def withdrawals_approve(wid: int, note: str = Form(""),
                              db: AsyncSession = Depends(get_db),
                              _: None = Depends(require_auth)) -> Response:
    w = await wd_repo.get_withdrawal(db, wid)
    if w is None or w.status != "pending":
        raise HTTPException(404)
    await wd_repo.decide_withdrawal(db, withdrawal=w, approved=True, admin_id=0, admin_note=note)
    db.add(Transaction(user_id=w.user_id, kind="withdrawal",
                       amount_usdt=-Decimal(str(w.amount_usdt)),
                       ref_id=w.id, note=f"withdrawal#{w.id} approved"))
    await db.commit()
    return RedirectResponse("/withdrawals", status_code=303)


@app.post("/withdrawals/{wid}/reject")
async def withdrawals_reject(wid: int, note: str = Form(""),
                             db: AsyncSession = Depends(get_db),
                             _: None = Depends(require_auth)) -> Response:
    w = await wd_repo.get_withdrawal(db, wid)
    if w is None or w.status != "pending":
        raise HTTPException(404)
    await wallet.credit(db, user_id=w.user_id, amount=Decimal(str(w.amount_usdt)),
                        kind="withdrawal_refund", ref_id=w.id,
                        note=f"withdrawal#{w.id} rejected: {note}")
    await wd_repo.decide_withdrawal(db, withdrawal=w, approved=False, admin_id=0, admin_note=note)
    return RedirectResponse("/withdrawals", status_code=303)


# ----- Users ----------

@app.get("/users", response_class=HTMLResponse)
async def users_page(request: Request, q: str = "", db: AsyncSession = Depends(get_db),
                     _: None = Depends(require_auth)) -> HTMLResponse:
    stmt = select(User).order_by(desc(User.joined_at)).limit(200)
    if q.strip():
        try:
            uid = int(q.strip())
            stmt = select(User).where(User.id == uid)
        except ValueError:
            qlike = f"%{q.strip()}%"
            stmt = select(User).where(User.username.ilike(qlike)).limit(100)
    users = (await db.scalars(stmt)).all()
    return templates.TemplateResponse(request, "users.html", {"users": users, "q": q})


@app.post("/users/{uid}/ban")
async def users_ban(uid: int, db: AsyncSession = Depends(get_db),
                    _: None = Depends(require_auth)) -> Response:
    u = await db.get(User, uid)
    if u is None:
        raise HTTPException(404)
    u.is_banned = not u.is_banned
    await db.commit()
    return RedirectResponse(f"/users?q={uid}", status_code=303)


@app.post("/users/{uid}/credit")
async def users_credit(uid: int, amount: str = Form(...), note: str = Form("admin credit"),
                       db: AsyncSession = Depends(get_db),
                       _: None = Depends(require_auth)) -> Response:
    try:
        amt = Decimal(amount)
    except InvalidOperation:
        return RedirectResponse(f"/users?q={uid}", status_code=303)
    if amt > 0:
        await wallet.credit(db, user_id=uid, amount=amt, kind="admin_credit", note=note)
    elif amt < 0:
        try:
            await wallet.debit(db, user_id=uid, amount=-amt, kind="admin_debit", note=note)
        except ValueError:
            pass
    return RedirectResponse(f"/users?q={uid}", status_code=303)


# ----- Orders ----------

@app.get("/orders", response_class=HTMLResponse)
async def orders_page(request: Request, db: AsyncSession = Depends(get_db),
                      _: None = Depends(require_auth)) -> HTMLResponse:
    rows = (await db.execute(
        select(Order, Product, User)
        .join(Product, Product.id == Order.product_id)
        .join(User, User.id == Order.user_id)
        .order_by(desc(Order.id))
        .limit(200)
    )).all()
    return templates.TemplateResponse(request, "orders.html", {"rows": rows})


# ----- Payments ----------

@app.get("/payments", response_class=HTMLResponse)
async def payments_page(
    request: Request,
    status: str = "all",
    flash: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_auth),
) -> HTMLResponse:
    stmt = (
        select(PaymentVerification, Product, User)
        .join(Product, Product.id == PaymentVerification.product_id)
        .join(User, User.id == PaymentVerification.user_id)
        .order_by(desc(PaymentVerification.id))
        .limit(300)
    )
    if status and status != "all":
        stmt = stmt.where(PaymentVerification.status == status)
    rows = (await db.execute(stmt)).all()
    payment_rows = [payment for payment, _product, _user in rows]
    total_expected = sum((Decimal(str(p.expected_amount_usdt or 0)) for p in payment_rows), Decimal("0"))
    total_received = sum((Decimal(str(p.received_amount_usdt or 0)) for p in payment_rows), Decimal("0"))
    delivered_count = sum(1 for p in payment_rows if p.status == "delivered")
    pending_count = sum(1 for p in payment_rows if p.status in {"pending", "auto_verified", "manual_review"})
    return templates.TemplateResponse(request, "payments.html", {
        "rows": rows,
        "status": status,
        "flash": flash,
        "total_expected": total_expected,
        "total_received": total_received,
        "delivered_count": delivered_count,
        "pending_count": pending_count,
    })


@app.post("/payments/{pid}/approve")
async def payments_approve(
    pid: int,
    note: str = Form("manual approval from dashboard"),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_auth),
) -> Response:
    payment = await payments_repo.get_payment_verification(db, pid)
    if payment is None:
        raise HTTPException(404)
    if payment.status == "delivered":
        return RedirectResponse("/payments?flash=already_delivered", status_code=303)

    await payments_repo.mark_manual_approved(
        db,
        payment,
        admin_id=0,
        note=note,
        received_amount=payment.received_amount_usdt or payment.expected_amount_usdt,
    )

    try:
        result = await buy_product_quantity(
            db,
            user_id=payment.user_id,
            product_id=payment.product_id,
            qty=payment.qty,
        )
    except OutOfStock:
        await payments_repo.mark_rejected(db, payment, status="delivery_failed", note="out_of_stock_manual")
        return RedirectResponse("/payments?flash=delivery_failed", status_code=303)
    except BuyError:
        await payments_repo.mark_rejected(db, payment, status="delivery_failed", note="delivery_error")
        return RedirectResponse("/payments?flash=delivery_failed", status_code=303)

    order_ids = [o.id for o in result.orders]
    await payments_repo.mark_delivered(db, payment, order_ids=order_ids)
    return RedirectResponse("/payments?flash=approved", status_code=303)


@app.post("/payments/{pid}/reject")
async def payments_reject(
    pid: int,
    note: str = Form("rejected from dashboard"),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_auth),
) -> Response:
    payment = await payments_repo.get_payment_verification(db, pid)
    if payment is None:
        raise HTTPException(404)
    if payment.status == "delivered":
        return RedirectResponse("/payments?flash=already_delivered", status_code=303)
    await payments_repo.mark_rejected(db, payment, status="manual_rejected", note=note, decided_by=0)
    return RedirectResponse("/payments?flash=rejected", status_code=303)


# ----- Payment settings ----------

@app.get("/payment-settings", response_class=HTMLResponse)
@app.get("/deposit-settings", response_class=HTMLResponse)
async def deposit_settings_page(
    request: Request,
    saved: int | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_auth),
) -> HTMLResponse:
    config = await get_deposit_settings(db)
    deposits = (await db.scalars(
        select(DepositOrder).order_by(desc(DepositOrder.id)).limit(100)
    )).all()
    logs = (await db.scalars(
        select(VerificationLog).order_by(desc(VerificationLog.id)).limit(100)
    )).all()
    return templates.TemplateResponse(request, "deposit_settings.html", {
        "config": config,
        "deposits": deposits,
        "logs": logs,
        "saved": saved,
        "error": error,
        "api_key_hint": (
            f"{config.binance_api_key[:4]}…{config.binance_api_key[-4:]}"
            if len(config.binance_api_key) >= 10 else ("configured" if config.binance_api_key else "")
        ),
        "merchant_key_hint": (
            f"{config.binance_pay_api_key[:4]}…{config.binance_pay_api_key[-4:]}"
            if len(config.binance_pay_api_key) >= 10 else (
                "configured" if config.binance_pay_api_key else ""
            )
        ),
    })


@app.post("/payment-settings")
@app.post("/deposit-settings")
async def deposit_settings_save(
    binance_uid: str = Form(""),
    binance_api_key: str = Form(""),
    binance_secret: str = Form(""),
    binance_pay_api_key: str = Form(""),
    binance_pay_secret: str = Form(""),
    binance_wallet_address: str = Form(""),
    bep20_wallet_address: str = Form(""),
    binance_api_base_url: str = Form(...),
    binance_pay_api_base_url: str = Form(...),
    bsc_rpc_url: str = Form(...),
    bep20_usdt_contract: str = Form(...),
    minimum: str = Form(...),
    maximum: str = Form(...),
    required_confirmations: int = Form(...),
    allowed_window_minutes: int = Form(...),
    uid_enabled: str | None = Form(None),
    order_id_enabled: str | None = Form(None),
    bep20_enabled: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_auth),
) -> Response:
    current = await get_deposit_settings(db)
    try:
        min_amount = Decimal(minimum)
        max_amount = Decimal(maximum)
    except InvalidOperation:
        return RedirectResponse("/payment-settings?error=amount", status_code=303)
    if min_amount <= 0 or max_amount < min_amount:
        return RedirectResponse("/payment-settings?error=range", status_code=303)
    if required_confirmations < 1 or required_confirmations > 1000:
        return RedirectResponse("/payment-settings?error=confirmations", status_code=303)
    if allowed_window_minutes < 1 or allowed_window_minutes > 43200:
        return RedirectResponse("/payment-settings?error=timeout", status_code=303)
    wallet_address = bep20_wallet_address.strip()
    token_contract = bep20_usdt_contract.strip()
    try:
        for address in (wallet_address, token_contract):
            if address:
                if len(address) != 42 or not address.startswith("0x"):
                    raise ValueError
                int(address[2:], 16)
    except ValueError:
        return RedirectResponse("/payment-settings?error=wallet", status_code=303)
    urls = (
        binance_api_base_url.strip(),
        binance_pay_api_base_url.strip(),
        bsc_rpc_url.strip(),
    )
    if any(not url.startswith(("https://", "http://")) for url in urls):
        return RedirectResponse("/payment-settings?error=url", status_code=303)

    await update_deposit_settings(db, {
        "binance_uid": binance_uid.strip(),
        "binance_api_key": binance_api_key.strip() or current.binance_api_key,
        "binance_secret": binance_secret.strip() or current.binance_secret,
        "binance_pay_api_key": (
            binance_pay_api_key.strip() or current.binance_pay_api_key
        ),
        "binance_pay_secret": (
            binance_pay_secret.strip() or current.binance_pay_secret
        ),
        "binance_wallet_address": binance_wallet_address.strip(),
        "bep20_wallet_address": wallet_address,
        "binance_api_base_url": urls[0],
        "binance_pay_api_base_url": urls[1],
        "bsc_rpc_url": urls[2],
        "bep20_usdt_contract": token_contract,
        "minimum": str(min_amount),
        "maximum": str(max_amount),
        "required_confirmations": str(required_confirmations),
        "allowed_window_minutes": str(allowed_window_minutes),
        "uid_enabled": str(uid_enabled is not None).lower(),
        "order_id_enabled": str(order_id_enabled is not None).lower(),
        "bep20_enabled": str(bep20_enabled is not None).lower(),
    })
    return RedirectResponse("/payment-settings?saved=1", status_code=303)
