from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.db.base import Base
from src.db.models import User
from src.repositories import deposits, payments
from src.services.deposit_settings import (
    DepositSettings,
    get_deposit_settings,
    update_deposit_settings,
)
from src.services.deposit_verification import (
    BinanceOrderMatch,
    DepositVerificationError,
    verify_bep20_tx,
)
from src.services.payment_flow import (
    bep20_reference_error,
    detect_reference_provider,
    normalize_payment_reference,
)
from src.ui import keyboards


class DepositTests(unittest.IsolatedAsyncioTestCase):
    def test_reference_provider_detection(self) -> None:
        self.assertEqual(detect_reference_provider("0x" + "ab" * 32), "bep20")
        self.assertEqual(detect_reference_provider("1234567890123456"), "binance_pay")
        self.assertEqual(detect_reference_provider("P_ABCDEF123456"), "binance_pay")
        self.assertIsNone(detect_reference_provider("not-a-transaction"))
        self.assertIsNone(bep20_reference_error("0x" + "ab" * 32))
        self.assertIsNone(bep20_reference_error("388358724421"))
        self.assertIn("wallet address", bep20_reference_error("0x" + "ab" * 20))
        txid = "0x" + "ab" * 32
        self.assertEqual(
            normalize_payment_reference(f"https://bscscan.com/tx/{txid}"), txid
        )

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

    async def test_manual_review_can_credit_once_and_claim_reference(self) -> None:
        txid = "0x" + "de" * 32
        async with self.sessions() as session:
            order = await deposits.create_deposit(
                session, user_id=7, method="bep20", expected_amount=None
            )
            await deposits.reject_deposit(
                session,
                order,
                reference=txid,
                code="wrong_recipient",
                detail="Recipient mismatch.",
                provider="bsc",
            )
            self.assertTrue(
                await deposits.reference_has_been_submitted(session, reference=txid)
            )
            retry_order = await deposits.find_retryable_bep20_deposit(
                session, user_id=7, txid=txid
            )
            self.assertIsNotNone(retry_order)
            self.assertEqual(retry_order.id, order.id)
            self.assertTrue(await deposits.submit_for_manual_review(session, order))
            balance = await deposits.manually_credit_deposit(
                session,
                order,
                amount=Decimal("4.99"),
                note="Confirmed on BSC explorer.",
            )
            self.assertEqual(balance, Decimal("4.99"))
            self.assertTrue(
                await deposits.reference_is_used(
                    session, method="bep20", reference=txid
                )
            )
            user = await session.get(User, 7)
            self.assertEqual(Decimal(str(user.balance_usdt)), Decimal("4.99"))

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
        block = {"timestamp": hex(int(datetime.now(timezone.utc).timestamp()))}
        with patch(
            "src.services.deposit_verification._rpc_batch",
            new=AsyncMock(side_effect=[
                [receipt, {"from": wallet}, "0x66"],
                [block],
            ]),
        ):
            match = await verify_bep20_tx(txid, Decimal("5"), settings)
        self.assertEqual(match.amount, Decimal("5"))
        self.assertEqual(match.confirmations, 3)

    async def test_old_bep20_transfer_is_rejected(self) -> None:
        wallet = "0x1111111111111111111111111111111111111111"
        contract = "0x55d398326f99059ff775485246999027b3197955"
        txid = "0x" + "cd" * 32
        topic_wallet = "0x" + "0" * 24 + wallet[2:]
        settings = DepositSettings(
            binance_uid="", binance_api_key="", binance_secret="",
            binance_api_base_url="https://api.binance.com", binance_pay_api_key="",
            binance_pay_secret="", binance_pay_api_base_url="", binance_wallet_address="",
            bep20_wallet_address=wallet, bsc_rpc_url="https://rpc.invalid",
            bep20_usdt_contract=contract, minimum=Decimal("1"), maximum=Decimal("100"),
            required_confirmations=3, allowed_window_minutes=60, uid_enabled=True,
            order_id_enabled=True, bep20_enabled=True,
        )
        receipt = {
            "status": "0x1", "blockNumber": "0x64",
            "logs": [{
                "address": contract,
                "topics": [
                    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
                    "0x" + "0" * 64, topic_wallet,
                ],
                "data": hex(5 * 10**18),
            }],
        }
        old_block = {"timestamp": hex(int(datetime.now(timezone.utc).timestamp()) - 7200)}
        with patch(
            "src.services.deposit_verification._rpc_batch",
            new=AsyncMock(side_effect=[
                [receipt, {"from": wallet}, "0x66"],
                [old_block],
            ]),
        ):
            with self.assertRaisesRegex(DepositVerificationError, "outside the allowed"):
                await verify_bep20_tx(txid, Decimal("5"), settings)

    async def test_duplicate_shop_reference_cannot_be_claimed_twice(self) -> None:
        async with self.sessions() as session:
            await payments.create_payment_verification(
                session,
                user_id=7,
                product_id=1,
                provider="bep20",
                reference="0x" + "ef" * 32,
                qty=1,
                expected_amount_usdt=Decimal("5"),
            )
            with self.assertRaises(payments.PaymentReferenceAlreadyUsed):
                await payments.create_payment_verification(
                    session,
                    user_id=7,
                    product_id=1,
                    provider="bep20",
                    reference="0x" + "ef" * 32,
                    qty=1,
                    expected_amount_usdt=Decimal("5"),
                )

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
                [keyboards.CB_SHOP],
                [keyboards.CB_DEPOSIT, keyboards.CB_PROFILE],
                [keyboards.CB_MY_ORDERS],
                [keyboards.CB_SUPPORT, keyboards.CB_REFER],
            ],
        )
        self.assertEqual(
            [[button.text for button in row] for row in main_rows],
            [
                ["🛍️ Shop"],
                ["💳 Deposit", "👤 My Profile"],
                ["📦 My Orders"],
                ["🆘 Support", "🌟 Refer & Earn"],
            ],
        )
        product_rows = keyboards.product_detail_kb(42, can_buy=True).inline_keyboard
        self.assertIn(
            f"{keyboards.CB_REFRESH_PRODUCT}:42",
            [row[0].callback_data for row in product_rows],
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
