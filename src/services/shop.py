"""Buy flow: atomically pop one stock item and write an order."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Order, Product, ResellerFulfillment
from ..repositories import orders as orders_repo
from ..repositories import products as products_repo
from ..repositories.users import get_user
from .canboso import CanbosoError, CanbosoUnknownResult, purchase as canboso_purchase


class BuyError(Exception):
    pass


class OutOfStock(BuyError):
    pass


class ExternalFulfillmentFailed(BuyError):
    """Supplier clearly declined the order; a wallet charge may be refunded."""


class ExternalFulfillmentUnknown(BuyError):
    """Supplier might have charged us; requires manual review, never retry."""


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


def canboso_product_id(product: Product) -> str | None:
    """Return the vendor ID only for an explicitly configured reseller item."""
    if product.delivery_type != "canboso":
        return None
    handler = (product.api_handler or "").strip()
    prefix = "canboso:"
    if not handler.lower().startswith(prefix):
        return None
    vendor_id = handler[len(prefix):].strip()
    return vendor_id or None


def uses_local_stock(product: Product) -> bool:
    return product.delivery_type != "canboso"


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
    idempotency_key: str | None = None,
) -> MultiBuyResult:
    if qty < 1:
        raise BuyError("Quantity must be at least 1.")

    product = await products_repo.get_product(session, product_id)
    if product is None or not product.is_active:
        raise BuyError("Product not found.")

    user = await get_user(session, user_id)
    if user is None:
        raise BuyError("User not found.")

    vendor_product_id = canboso_product_id(product)
    if product.delivery_type == "canboso":
        if vendor_product_id is None:
            raise BuyError("This reseller product is not configured yet.")
        return await _buy_canboso_product(
            session,
            user_id=user_id,
            product=product,
            vendor_product_id=vendor_product_id,
            qty=qty,
            idempotency_key=idempotency_key or f"canboso:{uuid4().hex}",
        )

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


async def _buy_canboso_product(
    session: AsyncSession,
    *,
    user_id: int,
    product: Product,
    vendor_product_id: str,
    qty: int,
    idempotency_key: str,
) -> MultiBuyResult:
    existing = await session.scalar(
        select(ResellerFulfillment).where(ResellerFulfillment.request_key == idempotency_key)
    )
    if existing is not None:
        order = await session.get(Order, existing.order_id)
        if existing.status == "delivered" and order is not None:
            return MultiBuyResult(product=product, orders=[order], payloads=[order.payload_snapshot])
        raise BuyError("This reseller order is already being reviewed. Please contact support.")

    # Commit a durable record before the external charge. A timeout must never
    # result in an automatic second supplier purchase.
    order = await orders_repo.create_external_order(
        session, user_id=user_id, product=product
    )
    fulfillment = ResellerFulfillment(
        order_id=order.id,
        provider="canboso",
        request_key=idempotency_key,
        vendor_product_id=vendor_product_id,
        status="pending",
    )
    session.add(fulfillment)
    await session.commit()

    try:
        result = await canboso_purchase(vendor_product_id=vendor_product_id, quantity=qty)
    except CanbosoUnknownResult as exc:
        fulfillment.status = "manual_review"
        order.status = "manual_review"
        await session.commit()
        raise ExternalFulfillmentUnknown(
            "Supplier response is unclear. Your order was sent for manual review."
        ) from exc
    except CanbosoError as exc:
        fulfillment.status = "failed"
        order.status = "delivery_failed"
        await session.commit()
        raise ExternalFulfillmentFailed(str(exc)) from exc

    fulfillment.status = "delivered"
    fulfillment.vendor_order_code = result.order_code
    fulfillment.response_payload = result.raw_response
    order.payload_snapshot = result.delivered_payload
    order.status = "completed"
    order.price_usdt = (Decimal(str(product.price_usdt)) * Decimal(qty)).quantize(Decimal("0.01"))
    await session.commit()
    return MultiBuyResult(product=product, orders=[order], payloads=[result.delivered_payload])
