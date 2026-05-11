from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from nio import AsyncClient

from src.bridge_service import BridgeService
from src.config import MATRIX_NIO_DEVICE_ID, Settings
from src.db import Database
from src.matrix_handlers import register_matrix_callbacks
from src.nio_patch import apply_nio_schema_patches
from src.strings import make_strings
from src.telegram_handlers import build_telegram_router

_PRIVACY_SENSITIVE_KEYS = frozenset({"body", "text", "caption", "message_text"})


class _PrivacyFilter(logging.Filter):
    """Defence-in-depth: drops log records that accidentally include user message content."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, dict) and _PRIVACY_SENSITIVE_KEYS.intersection(args):
            record.msg = "[PRIVACY FILTERED] log record contained sensitive keys"
            record.args = ()
        return True


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger().addFilter(_PrivacyFilter())


async def async_main() -> None:
    apply_nio_schema_patches()

    settings = Settings()
    db = await Database.connect(settings.database_path)
    await db.cleanup_expired_pending()

    bot = Bot(settings.telegram_bot_token)
    matrix = AsyncClient(
        settings.matrix_homeserver_base(),
        user=settings.matrix_user_id,
        device_id=MATRIX_NIO_DEVICE_ID,
        store_path="",
    )
    # restore_login is required: constructing with access_token alone leaves user_id empty in nio.
    matrix.restore_login(
        settings.matrix_user_id,
        MATRIX_NIO_DEVICE_ID,
        settings.matrix_access_token,
    )

    strings = make_strings(settings.locale, settings.telegram_bot_username, settings.matrix_user_id)

    bridge = BridgeService(settings, db, bot, matrix, strings)
    register_matrix_callbacks(matrix, bridge)

    router = build_telegram_router(bridge, bot)
    dp = Dispatcher()
    dp.include_router(router)

    log = logging.getLogger(__name__)
    log.info("Starting Matrix sync and Telegram polling")

    try:
        await asyncio.gather(
            matrix.sync_forever(timeout=30000, full_state=True),
            dp.start_polling(bot, handle_signals=False, drop_pending_updates=True),
        )
    finally:
        await bot.session.close()
        await matrix.close()


def main() -> None:
    _configure_logging()
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
