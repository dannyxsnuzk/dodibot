from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Order, Product, StockItem


async def create_order(
    session: AsyncSession,
    *,
    user_id: int,
    product: Product,
    stock_item: StockItem,
) -> Order:
    order = Order(
        user_id=user_id,
        product_id=product.id,
        stock_item_id=stock_item.id,
        price_usdt=product.price_usdt,
        payload_snapshot=stock_item.payload,
        status="completed",
    )
    session.add(order)
    stock_item.sold_to = user_id
    stock_item.sold_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(order)
    return order


async def list_orders(
    session: AsyncSession, user_id: int, *, page: int = 1, page_size: int = 5
) -> tuple[list[tuple[Order, Product]], int]:
    total = int(await session.scalar(
        select(func.count(Order.id)).where(Order.user_id == user_id)
    ) or 0)
    stmt = (
        select(Order, Product)
        .join(Product, Product.id == Order.product_id)
        .where(Order.user_id == user_id)
        .order_by(desc(Order.id))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await session.execute(stmt)).all()
    return [(o, p) for o, p in rows], total


async def get_order(session: AsyncSession, order_id: int, user_id: int) -> tuple[Order, Product] | None:
    stmt = (
        select(Order, Product)
        .join(Product, Product.id == Order.product_id)
        .where(Order.id == order_id, Order.user_id == user_id)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return None
    return row[0], row[1]


async def total_order_value(session: AsyncSession, user_id: int) -> Decimal:
    n = await session.scalar(
        select(func.coalesce(func.sum(Order.price_usdt), 0)).where(Order.user_id == user_id)
    )
    return Decimal(str(n or 0))


async def total_orders(session: AsyncSession, user_id: int) -> int:
    return int(await session.scalar(
        select(func.count(Order.id)).where(Order.user_id == user_id)
    ) or 0)
