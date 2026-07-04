"""FSM state groups for text-input flows that keep the single-message UX."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class WithdrawStates(StatesGroup):
    waiting_amount = State()
    waiting_address = State()


class AdminStates(StatesGroup):
    waiting_broadcast = State()
    waiting_stock_upload = State()
    waiting_product_field = State()


class ShopStates(StatesGroup):
    waiting_custom_qty = State()
    waiting_binance_reference = State()
