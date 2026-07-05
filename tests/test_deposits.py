from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.db.base import Base
from src.db.models import User
from src.repositories import deposits
from src.services.deposit_settings import (
    DepositSettings,
    get_deposit_settings,
    update_deposit_settings,
)
from src.services.deposit_verification import (
    BinanceOrderMatch,
    verify_bep20_tx,
)
from src.ui import keyboards


class DepositTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions() as session:
            session.add(User(id=7, referral_code="TESTCODE", balance_usdt=Decimal("0")))
            await session.commit()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_duplicate_binance_order_cannot_credit_twice(self) -> None:
        match = BinanceOrderMatch(
            order_id="123456789",
            transaction_id="txn-1",
            amount=Decimal("10"),
            currency="USDT",
            status="PAID",
            paid_at=datetime.now(timezone.utc),
            raw={"status": "SUCCESS"},
        )
        async with self.sessions() as session:
            first = await deposits.create_deposit(
                session, user_id=7, method="binance_uid", expected_amount=Decimal("10")
            )
            balance = await deposits.finalize_binance(
                session, first, submitted_order_id="123456789", match=match
            )
            self.assertEqual(balance, Decimal("10"))

            second = await deposits.create_deposit(
                session, user_id=7, method="binance_uid", expected_amount=Decimal("10")
            )
            with self.assertRaises(deposits.DepositAlreadyUsed):
                await deposits.finalize_binance(
                    session, second, submitted_order_id="123456789", match=match
                )
            user = await session.get(User, 7)
            self.assertEqual(Decimal(str(user.balance_usdt)), Decimal("10"))

    async def test_bep20_transfer_log_is_validated(self) -> None:
        wallet = "0x1111111111111111111111111111111111111111"
        contract = "0x55d398326f99059ff775485246999027b3197955"
        txid = "0x" + "ab" * 32
        topic_wallet = "0x" + "0" * 24 + wallet[2:]
        settings = DepositSettings(
            binance_uid="",
            binance_api_key="",
            binance_secret="",
            binance_api_base_url="https://api.binance.com",
            binance_pay_api_key="",
            binance_pay_secret="",
            binance_pay_api_base_url="",
            binance_wallet_address="",
            bep20_wallet_address=wallet,
            bsc_rpc_url="https://rpc.invalid",
            bep20_usdt_contract=contract,
            minimum=Decimal("1"),
            maximum=Decimal("100"),
            required_confirmations=3,
            allowed_window_minutes=60,
            uid_enabled=True,
            order_id_enabled=True,
            bep20_enabled=True,
        )
        receipt = {
            "status": "0x1",
            "blockNumber": "0x64",
            "logs": [{
                "address": contract,
                "topics": [
                    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
                    "0x" + "0" * 64,
                    topic_wallet,
                ],
                "data": hex(Decimal("5").as_integer_ratio()[0] * 10**18),
            }],
        }
        with patch(
            "src.services.deposit_verification._rpc_batch",
            new=AsyncMock(return_value=[receipt, {"from": wallet}, "0x66"]),
        ):
            match = await verify_bep20_tx(txid, Decimal("5"), settings)
        self.assertEqual(match.amount, Decimal("5"))
        self.assertEqual(match.confirmations, 3)

    async def test_payment_settings_apply_immediately_from_database(self) -> None:
        async with self.sessions() as session:
            await update_deposit_settings(session, {
                "binance_uid": "123456789",
                "binance_wallet_address": "wallet-reference",
                "minimum": "2.5",
                "maximum": "250",
                "required_confirmations": "8",
                "allowed_window_minutes": "45",
                "uid_enabled": "false",
                "order_id_enabled": "true",
                "bep20_enabled": "true",
            })
            config = await get_deposit_settings(session)
        self.assertEqual(config.binance_uid, "123456789")
        self.assertEqual(config.binance_wallet_address, "wallet-reference")
        self.assertEqual(config.minimum, Decimal("2.5"))
        self.assertEqual(config.required_confirmations, 8)
        self.assertFalse(config.uid_enabled)
        self.assertTrue(config.order_id_enabled)

    async def test_main_and_deposit_menu_layout(self) -> None:
        main_rows = keyboards.main_menu_kb().inline_keyboard
        self.assertEqual(
            [[button.callback_data for button in row] for row in main_rows],
            [
                [keyboards.CB_SHOP, keyboards.CB_DEPOSIT],
                [keyboards.CB_PROFILE, keyboards.CB_MY_ORDERS],
                [keyboards.CB_SUPPORT, keyboards.CB_REFER],
            ],
        )
        self.assertEqual(
            [[button.text for button in row] for row in main_rows],
            [
                ["🛒 Shop", "💰 Deposit"],
                ["👤 My Profile", "📦 My Orders"],
                ["🆘 Support", "⭐ Refer & Earn"],
            ],
        )
        deposit_rows = keyboards.deposit_methods_kb(
            binance_enabled=True, bep20_enabled=True
        ).inline_keyboard
        self.assertEqual(
            [row[0].callback_data for row in deposit_rows],
            [
                keyboards.CB_DEPOSIT_BINANCE,
                keyboards.CB_DEPOSIT_BEP20,
                keyboards.CB_MAIN,
            ],
        )
        self.assertEqual(
            [row[0].text for row in deposit_rows],
            [
                "🟡 Binance Pay (UID / Order ID)",
                "🟢 BEP20 (USDT)",
                "🔙 Back",
            ],
        )


if __name__ == "__main__":
    unittest.main()
