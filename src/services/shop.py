"""Buy flow: atomically pop one stock item and write an order."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Order, Product
from ..repositories import orders as orders_repo
from ..repositories import products as products_repo
from ..repositories.users import get_user


class BuyError(Exception):
    pass


class OutOfStock(BuyError):
    pass


@dataclass
class BuyResult:
    order: Order
    product: Product
    payload: str


@dataclass
class MultiBuyResult:
    product: Product
    orders: list[Order]
    payloads: list[str]


async def buy_product(
    session: AsyncSession, *, user_id: int, product_id: int
) -> BuyResult:
    product = await products_repo.get_product(session, product_id)
    if product is None or not product.is_active:
        raise BuyError("Product not found.")

    user = await get_user(session, user_id)
    if user is None:
        raise BuyError("User not found.")

    item = await products_repo.pop_one_stock_item(session, product_id)
    if item is None:
        raise OutOfStock("This product is out of stock.")

    order = await orders_repo.create_order(session, user_id=user_id, product=product, stock_item=item)

    return BuyResult(order=order, product=product, payload=item.payload)


async def buy_product_quantity(
    session: AsyncSession,
    *,
    user_id: int,
    product_id: int,
    qty: int,
) -> MultiBuyResult:
    if qty < 1:
        raise BuyError("Quantity must be at least 1.")

    product = await products_repo.get_product(session, product_id)
    if product is None or not product.is_active:
        raise BuyError("Product not found.")

    user = await get_user(session, user_id)
    if user is None:
        raise BuyError("User not found.")

    available = await products_repo.count_available_stock(session, product_id)
    if available < qty:
        raise OutOfStock(f"Only {available} item(s) in stock right now.")

    orders: list[Order] = []
    payloads: list[str] = []
    for _ in range(qty):
        item = await products_repo.pop_one_reserved_or_available_stock_item(
            session,
            product_id=product_id,
            user_id=user_id,
        )
        if item is None:
            raise OutOfStock("This product is out of stock.")
        order = await orders_repo.create_order(
            session,
            user_id=user_id,
            product=product,
            stock_item=item,
        )
        orders.append(order)
        payloads.append(item.payload)

    return MultiBuyResult(product=product, orders=orders, payloads=payloads)
