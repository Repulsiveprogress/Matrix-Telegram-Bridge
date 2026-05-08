from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.enums import ContentType
from aiogram.types import ChatMemberUpdated, Message

from src.bridge_service import BridgeService

logger = logging.getLogger(__name__)

_RELAY_MEDIA_TYPES = (
    ContentType.PHOTO,
    ContentType.DOCUMENT,
    ContentType.VIDEO,
    ContentType.ANIMATION,
    ContentType.AUDIO,
    ContentType.VOICE,
    ContentType.VIDEO_NOTE,
    ContentType.STICKER,
)


def build_telegram_router(bridge: BridgeService, bot: Bot) -> Router:
    router = Router()

    @router.my_chat_member(F.chat.type.in_({"group", "supergroup"}))
    async def on_bot_chat_member(event: ChatMemberUpdated) -> None:
        old = event.old_chat_member.status
        new = event.new_chat_member.status
        if new not in ("member", "administrator"):
            return
        if old in ("member", "administrator"):
            return
        await bridge.issue_link_for_telegram_chat(event.chat.id, event.chat.title)

    @router.message(F.migrate_to_chat_id)
    async def on_migrate_to(message: Message) -> None:
        if message.migrate_to_chat_id is None:
            return
        old_id = message.chat.id
        new_id = message.migrate_to_chat_id
        await bridge.db.update_tg_chat_id(old_id, new_id)
        logger.info("Telegram chat migrated %s -> %s", old_id, new_id)

    @router.message(F.migrate_from_chat_id)
    async def on_migrate_from(message: Message) -> None:
        if message.migrate_from_chat_id is None:
            return
        old_id = message.migrate_from_chat_id
        new_id = message.chat.id
        await bridge.db.update_tg_chat_id(old_id, new_id)
        logger.info("Telegram chat migrated %s -> %s", old_id, new_id)

    @router.message(
        F.chat.type.in_({"group", "supergroup"}),
        F.content_type == ContentType.TEXT,
        F.text,
    )
    async def on_group_text(message: Message) -> None:
        if message.from_user is None:
            return
        if message.from_user.id == bot.id:
            return
        text = message.text or ""
        if bridge.is_link_request_command(text):
            await bridge.issue_link_for_telegram_chat(message.chat.id, message.chat.title)
            return
        if bridge.is_unlink_command(text):
            await bridge.unlink_from_telegram(message.chat.id)
            return
        label = message.from_user.username or message.from_user.full_name or str(message.from_user.id)
        await bridge.relay_telegram_to_matrix(message.chat.id, label, text)

    @router.message(F.chat.type.in_({"group", "supergroup"}), F.content_type.in_(_RELAY_MEDIA_TYPES))
    async def on_group_media(message: Message) -> None:
        if message.from_user is None:
            return
        if message.from_user.id == bot.id:
            return
        await bridge.relay_telegram_media(message)

    return router
