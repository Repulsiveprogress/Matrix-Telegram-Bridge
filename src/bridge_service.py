from __future__ import annotations

import html
import logging
import time

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message
from nio import AsyncClient, RoomGetStateEventResponse

from src.config import Settings
from src.db import Database
from src.linking import (
    generate_link_code,
    is_expired,
    is_tg_link_request_command,
    is_tg_unlink_command,
    parse_tg_link_command,
)
from src.media_relay import (
    download_mxc_to_bytes,
    guess_ext,
    relay_matrix_media_event_to_telegram,
    relay_telegram_message_media,
    send_matrix_sticker_to_telegram,
)
from src.rate_limit import SlidingWindowLimiter
from src.strings import Strings

logger = logging.getLogger(__name__)

# PRIVACY CONTRACT: message body/text must never appear in log output.
# Do not pass `body`, `text`, or event content to any logger call at any level.


def _matrix_sender_display_name(room, sender_id: str) -> str:
    user = room.users.get(sender_id)
    if user is not None:
        return user.name
    return sender_id


def _format_relay_telegram_html(username: str, body: str) -> str:
    u = html.escape(username.strip() or "?", quote=True)
    b = html.escape(body, quote=True)
    return f"<b>{u}</b>: {b}"


def _format_relay_matrix_content(username: str, body: str) -> dict:
    u_plain = username.strip() or "?"
    plain = f"{u_plain}: {body}"
    u = html.escape(u_plain, quote=True)
    b = html.escape(body, quote=True)
    formatted = f"<b>{u}</b>: {b}"
    return {
        "msgtype": "m.text",
        "body": plain,
        "format": "org.matrix.custom.html",
        "formatted_body": formatted,
    }


class BridgeService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        tg_bot: Bot,
        matrix: AsyncClient,
        strings: Strings,
    ) -> None:
        self.settings = settings
        self.db = db
        self.tg_bot = tg_bot
        self.matrix = matrix
        self.strings = strings
        self.started_at_ms = int(time.time() * 1000)
        self._link_attempts = SlidingWindowLimiter(
            settings.rate_limit_link_attempts,
            float(settings.rate_limit_link_window_seconds),
            global_max=settings.rate_limit_link_attempts * 10,
        )
        self._code_gen = SlidingWindowLimiter(
            settings.rate_limit_code_generations,
            float(settings.rate_limit_code_window_seconds),
            global_max=settings.rate_limit_code_generations * 10,
        )

    def is_fresh_matrix_event(self, server_ts_ms: int | None) -> bool:
        if server_ts_ms is None:
            return True
        return int(server_ts_ms) >= self.started_at_ms

    def parse_link_command(self, body: str) -> str | None:
        return parse_tg_link_command(body)

    def is_unlink_command(self, body: str) -> bool:
        return is_tg_unlink_command(body)

    def is_link_request_command(self, body: str) -> bool:
        return is_tg_link_request_command(body)

    @staticmethod
    def _hostname_from_server_name(server_name: str) -> str:
        s = (server_name or "").strip().lower()
        if not s:
            return ""
        if s.startswith("["):
            end = s.find("]")
            if end == -1:
                return s
            return s[1:end]
        if ":" in s:
            host, maybe_port = s.rsplit(":", 1)
            if maybe_port.isdigit():
                return host
        return s

    @staticmethod
    def _server_name_from_mxid(mxid: str) -> str | None:
        if ":" not in mxid:
            return None
        return mxid.split(":", 1)[1].strip()

    def _is_allowed_hostname(self, hostname: str) -> bool:
        allowed = (self.settings.matrix_allowed_server or "").strip().lower()
        if not allowed:
            return True
        h = (hostname or "").strip().lower()
        if not h:
            # Cannot determine hostname — deny when allowlist is configured.
            return False
        return h == allowed or h.endswith(f".{allowed}")

    async def room_server_allowed(self, room_id: str) -> bool:
        dom = self.settings.matrix_allowed_server
        if not dom:
            return True
        room_server = None
        if ":" in room_id:
            room_server = room_id.split(":", 1)[1].strip()
        if room_server:
            return self._is_allowed_hostname(self._hostname_from_server_name(room_server))

        # Room IDs v11+ may omit the domain — fall back to the creator's server.
        room = self.matrix.rooms.get(room_id)
        creator = room.creator if room else ""
        creator_server = self._server_name_from_mxid(creator) if creator else None
        if creator_server:
            return self._is_allowed_hostname(self._hostname_from_server_name(creator_server))

        logger.warning(
            "Cannot determine room server for MATRIX_ALLOWED_SERVER check, "
            "denying: room_id=%s creator=%s",
            room_id,
            creator,
        )
        return False

    async def is_room_encrypted(self, room_id: str) -> bool:
        resp = await self.matrix.room_get_state_event(room_id, "m.room.encryption", "")
        return isinstance(resp, RoomGetStateEventResponse)

    def _matrix_sender_is_moderator(self, room, sender_id: str) -> bool:
        """Return True if sender has power level >= 50 (moderator or above)."""
        try:
            pl = room.power_levels
            level = pl.get_user_level(sender_id)
            return level >= 50
        except Exception:
            return False

    async def _telegram_sender_is_admin(self, tg_chat_id: int, user_id: int) -> bool:
        """Return True if the Telegram user is creator or administrator of the chat."""
        try:
            member = await self.tg_bot.get_chat_member(tg_chat_id, user_id)
            return member.status in ("creator", "administrator")
        except TelegramBadRequest:
            return False

    async def send_matrix_plain(self, room_id: str, text: str) -> None:
        await self.matrix.room_send(
            room_id,
            "m.room.message",
            {"msgtype": "m.text", "body": text},
        )

    async def send_matrix_room_message(self, room_id: str, content: dict) -> None:
        await self.matrix.room_send(room_id, "m.room.message", content)

    async def maybe_send_matrix_welcome(self, room_id: str) -> None:
        if await self.db.is_welcome_sent(room_id):
            return
        if await self.is_room_encrypted(room_id):
            await self.send_matrix_plain(room_id, self.strings.encrypted_room)
            await self.db.mark_welcome_sent(room_id)
            return
        await self.send_matrix_plain(room_id, self.strings.matrix_welcome)
        await self.db.mark_welcome_sent(room_id)

    async def on_bot_joined_matrix_room(self, room_id: str) -> None:
        if not await self.room_server_allowed(room_id):
            logger.warning("Skipping welcome for room outside allowed server: %s", room_id)
            return
        await self.maybe_send_matrix_welcome(room_id)

    async def issue_link_for_telegram_chat(self, tg_chat_id: int) -> None:
        if not self._code_gen.allow(tg_chat_id):
            await self.tg_bot.send_message(tg_chat_id, self.strings.rate_limit_code)
            return
        if await self.db.get_bridge_by_tg(tg_chat_id):
            await self.tg_bot.send_message(tg_chat_id, self.strings.already_linked_telegram)
            return
        await self.db.cleanup_expired_pending()
        await self.db.revoke_pending_for_tg(tg_chat_id)
        code = generate_link_code()
        expires = time.time() + float(self.settings.link_code_ttl_seconds)
        await self.db.insert_pending(code, tg_chat_id, expires)
        await self.tg_bot.send_message(
            tg_chat_id,
            self.strings.telegram_welcome(self.settings.matrix_user_id, code),
        )

    async def try_link_from_matrix(self, room, sender_id: str, raw_body: str) -> None:
        room_id = room.room_id
        if self.is_unlink_command(raw_body):
            await self.unlink_from_matrix(room, sender_id)
            return
        code = self.parse_link_command(raw_body)
        if not code:
            return
        if not self._matrix_sender_is_moderator(room, sender_id):
            await self.send_matrix_plain(room_id, self.strings.not_authorized)
            return
        if not self._link_attempts.allow(room_id):
            await self.send_matrix_plain(room_id, self.strings.rate_limit_link)
            return
        if not await self.room_server_allowed(room_id):
            logger.warning(
                "Link denied by MATRIX_ALLOWED_SERVER. room_id=%s allowed_domain=%s",
                room_id,
                self.settings.matrix_allowed_server,
            )
            return
        if await self.is_room_encrypted(room_id):
            await self.send_matrix_plain(room_id, self.strings.encrypted_room_link_denied)
            return
        if await self.db.get_bridge_by_matrix(room_id):
            await self.send_matrix_plain(room_id, self.strings.already_linked_matrix)
            return
        pending = await self.db.get_pending_by_code(code)
        if pending is None:
            await self.send_matrix_plain(room_id, self.strings.code_not_found)
            return
        if is_expired(pending.expires_at):
            await self.db.delete_pending(code)
            await self.send_matrix_plain(room_id, self.strings.code_expired)
            return
        linked = await self.db.try_link_atomic(code, pending.tg_chat_id, room_id)
        if not linked:
            await self.send_matrix_plain(room_id, self.strings.tg_chat_already_linked)
            return
        await self.send_matrix_plain(room_id, self.strings.link_success_matrix)
        try:
            await self.tg_bot.send_message(pending.tg_chat_id, self.strings.link_success_telegram)
        except Exception as exc:
            logger.error("Failed to notify Telegram after link: %s", exc)

    async def unlink_from_matrix(self, room, sender_id: str) -> None:
        room_id = room.room_id
        if not self._matrix_sender_is_moderator(room, sender_id):
            await self.send_matrix_plain(room_id, self.strings.not_authorized)
            return
        bridge = await self.db.get_bridge_by_matrix(room_id)
        if not bridge:
            await self.send_matrix_plain(room_id, self.strings.unlink_no_bridge_matrix)
            return
        removed = await self.db.delete_bridge_by_matrix(room_id)
        if not removed:
            await self.send_matrix_plain(room_id, self.strings.unlink_failed_matrix)
            return
        await self.send_matrix_plain(room_id, self.strings.unlink_success_matrix)
        try:
            await self.tg_bot.send_message(bridge.tg_chat_id, self.strings.unlink_success_telegram)
        except Exception:
            logger.exception("Failed to notify Telegram after unlink")

    async def unlink_from_telegram(self, tg_chat_id: int, from_user_id: int) -> None:
        if not await self._telegram_sender_is_admin(tg_chat_id, from_user_id):
            await self.tg_bot.send_message(tg_chat_id, self.strings.not_authorized)
            return
        bridge = await self.db.get_bridge_by_tg(tg_chat_id)
        if not bridge:
            await self.tg_bot.send_message(tg_chat_id, self.strings.unlink_no_bridge_telegram)
            return
        removed = await self.db.delete_bridge_by_tg(tg_chat_id)
        if not removed:
            await self.tg_bot.send_message(tg_chat_id, self.strings.unlink_failed_telegram)
            return
        await self.tg_bot.send_message(tg_chat_id, self.strings.unlink_success_telegram)
        try:
            await self.send_matrix_plain(bridge.matrix_room_id, self.strings.unlink_success_matrix)
        except Exception:
            logger.exception("Failed to notify Matrix after unlink")

    async def relay_matrix_to_telegram(
        self,
        room,
        sender_id: str,
        body: str,
        *,
        server_ts_ms: int | None = None,
    ) -> None:
        room_id = room.room_id
        if self.parse_link_command(body):
            return
        if not self.is_fresh_matrix_event(server_ts_ms):
            return
        bridge = await self.db.get_bridge_by_matrix(room_id)
        if not bridge:
            return
        if sender_id == self.matrix.user_id:
            return
        label = _matrix_sender_display_name(room, sender_id)
        text = _format_relay_telegram_html(label, body)
        try:
            await self.tg_bot.send_message(bridge.tg_chat_id, text, parse_mode=ParseMode.HTML)
        except Exception as exc:
            logger.error("relay_matrix_to_telegram failed: %s", exc)

    async def relay_telegram_to_matrix(
        self, tg_chat_id: int, from_user_id: int, label: str, body: str
    ) -> None:
        if self.is_unlink_command(body):
            await self.unlink_from_telegram(tg_chat_id, from_user_id)
            return
        if body.strip().startswith("/"):
            return
        bridge = await self.db.get_bridge_by_tg(tg_chat_id)
        if not bridge:
            logger.warning("No bridge mapping for Telegram chat_id=%s", tg_chat_id)
            return
        content = _format_relay_matrix_content(label, body)
        try:
            await self.send_matrix_room_message(bridge.matrix_room_id, content)
            logger.debug(
                "Relayed TG->Matrix chat_id=%s room_id=%s",
                tg_chat_id,
                bridge.matrix_room_id,
            )
        except Exception as exc:
            logger.error(
                "relay_telegram_to_matrix failed chat_id=%s room_id=%s: %s",
                tg_chat_id,
                bridge.matrix_room_id,
                exc,
            )

    async def relay_telegram_media(self, message: Message) -> None:
        if message.from_user is None:
            return
        bridge = await self.db.get_bridge_by_tg(message.chat.id)
        if not bridge:
            return
        label = (
            message.from_user.username or message.from_user.full_name or str(message.from_user.id)
        )
        ok = await relay_telegram_message_media(
            self.tg_bot, self.matrix, bridge.matrix_room_id, label, message
        )
        if not ok:
            try:
                await self.send_matrix_plain(bridge.matrix_room_id, self.strings.media_relay_failed)
            except Exception:
                logger.exception("relay_telegram_media: failed to send error message")

    async def relay_matrix_media_to_telegram(self, room, event, matrix_msgtype: str) -> None:
        room_id = room.room_id
        if not self.is_fresh_matrix_event(getattr(event, "server_timestamp", None)):
            return
        bridge = await self.db.get_bridge_by_matrix(room_id)
        if not bridge:
            return
        if event.sender == self.matrix.user_id:
            return
        label = _matrix_sender_display_name(room, event.sender)
        body = getattr(event, "body", None) or self.strings.attachment_label
        caption = _format_relay_telegram_html(label, body)
        await relay_matrix_media_event_to_telegram(
            self.tg_bot, self.matrix, bridge.tg_chat_id, caption, event, matrix_msgtype
        )

    async def relay_matrix_sticker_to_telegram(self, room, event) -> None:
        room_id = room.room_id
        if not self.is_fresh_matrix_event(getattr(event, "server_timestamp", None)):
            return
        bridge = await self.db.get_bridge_by_matrix(room_id)
        if not bridge:
            return
        if event.sender == self.matrix.user_id:
            return
        content = event.source.get("content", {})
        mxc_url = content.get("url", "")
        if not mxc_url or not str(mxc_url).startswith("mxc://"):
            logger.warning("Matrix sticker has no valid mxc url")
            return
        dl = await download_mxc_to_bytes(self.matrix, mxc_url)
        if not dl:
            return
        data, mime, fname = dl
        body = content.get("body", "") or "sticker"
        filename = fname or f"sticker{guess_ext(mime, '.webp')}"
        label = _matrix_sender_display_name(room, event.sender)
        caption = _format_relay_telegram_html(label, body)
        try:
            await send_matrix_sticker_to_telegram(
                self.tg_bot, bridge.tg_chat_id, caption, data, filename, mime
            )
        except Exception:
            logger.exception("Failed to send Matrix sticker to Telegram")
