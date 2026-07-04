"""Seed initial product list (taken from the Dodi Store screenshots)."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Product

LINKEDIN_CAREER_DESCRIPTION = (
    "Only new users or those who have not used any premium in the last 12 months can redeem it.\n\n"
    "Delivery is automatic after payment confirmation."
)

# (slug, name, emoji, duration, default_price, sort_order)
INITIAL_PRODUCTS: list[tuple[str, str, str, str, str, int]] = [
    ("linkedin_career_3m", "Linkedin Career (New User)", "\U0001f4e6", "3m", "0.30", 10),
    ("cursor_pro_12m", "Cursor Pro", "\U0001f5b1\ufe0f", "12m", "29.99", 20),
    ("supabase_pro_12m", "Supabase Pro", "\U0001f5c4\ufe0f", "12m", "24.99", 30),
    ("canva_business_12m", "Canva Business", "\U0001f3a8", "12m", "19.99", 40),
    ("replit_core_12m", "Replit Core", "\U0001f7e7", "12m", "24.99", 50),
    ("n8n_starter_12m", "n8n Starter", "\U0001f517", "12m", "14.99", 60),
    ("coursera_plus_12m", "Coursera Plus", "\U0001f393", "12m", "39.99", 70),
    ("notion_business_12m", "Notion Business", "\U0001f4d2", "12m", "24.99", 80),
    ("elevenlabs_creator_1m", "ElevenLabs Creator", "\U0001f399\ufe0f", "1m", "5.99", 90),
    ("elevenlabs_creator_12m", "ElevenLabs Creator", "\U0001f399\ufe0f", "12m", "29.99", 91),
    ("elevenlabs_creator_acc_12m", "ElevenLabs Creator Account", "\U0001f399\ufe0f", "12m", "29.99", 92),
    ("google_ai_pro_12m", "Google AI Pro", "\U0001f916", "12m", "34.99", 100),
    ("chatprd_pro_12m", "ChatPRD Pro", "\U0001f4ac", "12m", "14.99", 110),
    ("framer_pro_12m", "Framer Pro", "\U0001f3ac", "12m", "24.99", 120),
    ("granola_business_12m", "Granola Business", "\U0001f300", "12m", "24.99", 130),
    ("gumloop_pro_12m", "Gumloop Pro", "\U0001f7e2", "12m", "19.99", 140),
    ("intercom_advanced_12m", "Intercom Advanced", "\U0001f4ad", "12m", "29.99", 150),
    ("linear_business_12m", "Linear Business", "\U0001f4d0", "12m", "24.99", 160),
    ("magic_patterns_12m", "Magic Patterns", "\u2728", "12m", "19.99", 170),
    ("posthog_scale_12m", "PostHog Scale", "\U0001f4ca", "12m", "29.99", 180),
    ("warp_build_12m", "Warp Build", "\u26a1", "12m", "19.99", 190),
]


async def seed_products(session: AsyncSession) -> int:
    """Insert any products that are missing. Returns number of newly inserted rows."""
    inserted = 0
    for slug, name, emoji, duration, price, sort_order in INITIAL_PRODUCTS:
        existing = await session.scalar(select(Product).where(Product.slug == slug))
        if existing is not None:
            if slug == "linkedin_career_3m":
                existing.display_name = name
                existing.emoji = emoji
                existing.duration_label = duration
                existing.price_usdt = Decimal(price)
                existing.sort_order = sort_order
                existing.description = LINKEDIN_CAREER_DESCRIPTION
                existing.is_active = True
            continue
        session.add(
            Product(
                slug=slug,
                display_name=name,
                emoji=emoji,
                duration_label=duration,
                price_usdt=Decimal(price),
                sort_order=sort_order,
                is_active=True,
                description=LINKEDIN_CAREER_DESCRIPTION if slug == "linkedin_career_3m" else "",
                delivery_type="stock_pool",
            )
        )
        inserted += 1
    await session.commit()
    return inserted
