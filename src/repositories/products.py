from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Order, Product, StockItem


def _lock_stock_rows(session: AsyncSession, stmt):
    """Use row locks on transactional databases; SQLite serializes writers itself."""
    if session.get_bind().dialect.name != "sqlite":
        return stmt.with_for_update(skip_locked=True)
    return stmt


async def list_active_products_with_stock(
    session: AsyncSession,
    *,
    in_stock_only: bool = False,
) -> list[tuple[Product, int]]:
    """Return active products plus count of available stock items, ordered."""
    await release_expired_reservations(session)
    stock_subq = (
        select(StockItem.product_id, func.count(StockItem.id).label("c"))
        .where(StockItem.status == "available")
        .group_by(StockItem.product_id)
        .subquery()
    )
    stmt = (
        select(Product, func.coalesce(stock_subq.c.c, 0))
        .outerjoin(stock_subq, stock_subq.c.product_id == Product.id)
        .where(Product.is_active.is_(True))
        .order_by(Product.sort_order, Product.id)
    )
    if in_stock_only:
        stmt = stmt.where(
            (func.coalesce(stock_subq.c.c, 0) > 0)
            | (Product.delivery_type == "canboso")
        )
    rows = (await session.execute(stmt)).all()
    return [(p, -1 if p.delivery_type == "canboso" else int(c)) for p, c in rows]


async def get_product(session: AsyncSession, product_id: int) -> Product | None:
    return await session.get(Product, product_id)


async def get_product_with_stock(
    session: AsyncSession, product_id: int
) -> tuple[Product, int] | None:
    """Fetch one product and its available stock count in a single query."""
    await release_expired_reservations(session)
    stock_count = (
        select(func.count(StockItem.id))
        .where(
            StockItem.product_id == Product.id,
            StockItem.status == "available",
        )
        .correlate(Product)
        .scalar_subquery()
    )
    row = (await session.execute(
        select(Product, stock_count).where(Product.id == product_id)
    )).first()
    if row is None:
        return None
    product = row[0]
    return product, (-1 if product.delivery_type == "canboso" else int(row[1] or 0))


async def count_available_stock(session: AsyncSession, product_id: int) -> int:
    await release_expired_reservations(session)
    n = await session.scalar(
        select(func.count(StockItem.id))
        .where(StockItem.product_id == product_id, StockItem.status == "available")
    )
    return int(n or 0)


async def upsert_product(
    session: AsyncSession,
    *,
    slug: str,
    display_name: str,
    emoji: str,
    duration_label: str,
    price_usdt,
    description: str = "",
    is_active: bool = True,
    sort_order: int = 0,
    emoji_id: str | None = None,
    delivery_type: str = "stock_pool",
    api_handler: str | None = None,
) -> Product:
    existing = await session.scalar(select(Product).where(Product.slug == slug))
    if existing is None:
        existing = Product(slug=slug)
        session.add(existing)
    existing.display_name = display_name
    existing.emoji = emoji
    existing.duration_label = duration_label
    existing.price_usdt = price_usdt
    existing.description = description
    existing.is_active = is_active
    existing.sort_order = sort_order
    existing.emoji_id = emoji_id or None
    existing.delivery_type = delivery_type
    existing.api_handler = api_handler or None
    await session.commit()
    return existing


async def add_stock_lines(session: AsyncSession, product_id: int, lines: list[str]) -> int:
    cleaned = [ln.strip() for ln in lines if ln.strip()]
    for payload in cleaned:
        session.add(StockItem(product_id=product_id, payload=payload, status="available"))
    await session.commit()
    return len(cleaned)


async def clear_available_stock(session: AsyncSession, product_id: int) -> int:
    """Delete every unsold stock item for a product. Returns rows deleted."""
    result = await session.execute(
        delete(StockItem).where(
            StockItem.product_id == product_id,
            StockItem.status == "available",
        )
    )
    await session.commit()
    return int(result.rowcount or 0)


async def count_orders(session: AsyncSession, product_id: int) -> int:
    n = await session.scalar(
        select(func.count(Order.id)).where(Order.product_id == product_id)
    )
    return int(n or 0)


async def delete_product(session: AsyncSession, product_id: int) -> tuple[bool, str]:
    """Hard-delete a product. Refuses if it has any order history.

    Returns (success, message). On success, also wipes its unsold stock.
    """
    p = await session.get(Product, product_id)
    if p is None:
        return False, "Product not found."
    n_orders = await count_orders(session, product_id)
    if n_orders > 0:
        return False, (
            f"Refusing to delete: product has {n_orders} order(s) on record. "
            "Use /setactive slug off to hide it instead."
        )
    await session.execute(
        delete(StockItem).where(StockItem.product_id == product_id)
    )
    await session.delete(p)
    await session.commit()
    return True, f"Deleted {p.slug}"


async def pop_one_stock_item(session: AsyncSession, product_id: int) -> StockItem | None:
    """Atomically reserve & sell one available stock item.

    Locks rows on transactional databases so simultaneous checkouts cannot sell
    the same stock item.
    """
    async with session.begin_nested():
        item = await session.scalar(
            _lock_stock_rows(session, select(StockItem)
            .where(StockItem.product_id == product_id, StockItem.status == "available")
            .order_by(StockItem.id)
            .limit(1))
        )
        if item is None:
            return None
        item.status = "sold"
    return item


async def count_user_reserved_stock(
    session: AsyncSession,
    *,
    product_id: int,
    user_id: int,
) -> int:
    await release_expired_reservations(session)
    n = await session.scalar(
        select(func.count(StockItem.id)).where(
            StockItem.product_id == product_id,
            StockItem.status == "reserved",
            StockItem.reserved_by == user_id,
            StockItem.reserved_until > datetime.now(timezone.utc),
        )
    )
    return int(n or 0)


async def reserve_stock_items(
    session: AsyncSession,
    *,
    product_id: int,
    user_id: int,
    qty: int,
    ttl_minutes: int = 10,
) -> int:
    """Reserve stock for a checkout attempt. Returns number reserved."""
    await release_expired_reservations(session)
    current = await count_user_reserved_stock(session, product_id=product_id, user_id=user_id)
    if current >= qty:
        return current

    needed = qty - current
    until = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    items = list((await session.scalars(
        _lock_stock_rows(session, select(StockItem)
        .where(StockItem.product_id == product_id, StockItem.status == "available")
        .order_by(StockItem.id)
        .limit(needed))
    )).all())
    for item in items:
        item.status = "reserved"
        item.reserved_by = user_id
        item.reserved_until = until
    await session.commit()
    return current + len(items)


async def release_user_reservations(
    session: AsyncSession,
    *,
    product_id: int | None,
    user_id: int,
) -> int:
    stmt = select(StockItem).where(
        StockItem.status == "reserved",
        StockItem.reserved_by == user_id,
    )
    if product_id is not None:
        stmt = stmt.where(StockItem.product_id == product_id)
    items = list((await session.scalars(stmt)).all())
    for item in items:
        item.status = "available"
        item.reserved_by = None
        item.reserved_until = None
    if items:
        await session.commit()
    return len(items)


async def release_expired_reservations(session: AsyncSession) -> int:
    now = datetime.now(timezone.utc)
    items = list((await session.scalars(
        select(StockItem).where(
            StockItem.status == "reserved",
            StockItem.reserved_until <= now,
        )
    )).all())
    for item in items:
        item.status = "available"
        item.reserved_by = None
        item.reserved_until = None
    if items:
        await session.commit()
    return len(items)


async def pop_one_reserved_or_available_stock_item(
    session: AsyncSession,
    *,
    product_id: int,
    user_id: int,
) -> StockItem | None:
    await release_expired_reservations(session)
    async with session.begin_nested():
        now = datetime.now(timezone.utc)
        item = await session.scalar(
            _lock_stock_rows(session, select(StockItem)
            .where(
                StockItem.product_id == product_id,
                StockItem.status == "reserved",
                StockItem.reserved_by == user_id,
                StockItem.reserved_until > now,
            )
            .order_by(StockItem.id)
            .limit(1))
        )
        if item is None:
            item = await session.scalar(
                _lock_stock_rows(session, select(StockItem)
                .where(StockItem.product_id == product_id, StockItem.status == "available")
                .order_by(StockItem.id)
                .limit(1))
            )
        if item is None:
            return None
        item.status = "sold"
        item.reserved_by = None
        item.reserved_until = None
    return item
