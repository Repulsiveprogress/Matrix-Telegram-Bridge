from __future__ import annotations

import io
import logging
import mimetypes
import os
from urllib.parse import quote
from typing import Callable

import aiohttp
from aiogram import Bot
from aiogram.types import BufferedInputFile, Message
from nio import AsyncClient, DownloadError, MemoryDownloadResponse, UploadError, UploadResponse

logger = logging.getLogger(__name__)

TG_MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024


def _bytes_provider(data: bytes) -> Callable[[int, int], io.BytesIO]:
    def provider(_got_429: int, _got_timeouts: int) -> io.BytesIO:
        return io.BytesIO(data)

    return provider


async def upload_bytes_to_matrix(
    client: AsyncClient,
    data: bytes,
    content_type: str,
    filename: str | None,
) -> str | None:
    if len(data) > TG_MAX_DOWNLOAD_BYTES:
        logger.warning("Skipping Matrix upload: file exceeds %s MB", TG_MAX_DOWNLOAD_BYTES // (1024 * 1024))
        return None
    resp, _ = await client.upload(
        _bytes_provider(data),
        content_type=content_type or "application/octet-stream",
        filename=filename,
        filesize=len(data),
    )
    if isinstance(resp, UploadError):
        logger.error("Matrix upload failed: %s", resp.message)
        return None
    if isinstance(resp, UploadResponse):
        return resp.content_uri
    return None


async def download_mxc_to_bytes(
    client: AsyncClient,
    mxc_url: str,
) -> tuple[bytes, str, str | None] | None:
    authenticated = await _download_mxc_authenticated(client, mxc_url)
    if authenticated is not None:
        return authenticated

    # Fallback for older homeservers / configurations with legacy media APIs.
    result = await client.download(mxc_url)
    if isinstance(result, DownloadError):
        logger.error("Matrix download failed (legacy endpoint): %s", result.message)
        return None
    if isinstance(result, MemoryDownloadResponse):
        return result.body, result.content_type or "application/octet-stream", result.filename
    return None


def _parse_mxc_uri(mxc_url: str) -> tuple[str, str] | None:
    if not mxc_url.startswith("mxc://"):
        return None
    rest = mxc_url[len("mxc://"):]
    if "/" not in rest:
        return None
    server_name, media_id = rest.split("/", 1)
    if not server_name or not media_id:
        return None
    return server_name, media_id


def _filename_from_content_disposition(header_value: str | None) -> str | None:
    if not header_value:
        return None
    parts = [p.strip() for p in header_value.split(";")]
    for p in parts:
        if p.lower().startswith("filename*="):
            raw = p.split("=", 1)[1].strip().strip('"')
            if "''" in raw:
                raw = raw.split("''", 1)[1]
            return raw
        if p.lower().startswith("filename="):
            return p.split("=", 1)[1].strip().strip('"')
    return None


async def _download_mxc_authenticated(
    client: AsyncClient,
    mxc_url: str,
) -> tuple[bytes, str, str | None] | None:
    parsed = _parse_mxc_uri(mxc_url)
    if not parsed:
        logger.warning("Invalid mxc url: %s", mxc_url)
        return None
    if not client.access_token:
        logger.warning("No Matrix access token for authenticated media download")
        return None
    server_name, media_id = parsed
    base = client.homeserver.rstrip("/")
    url = (
        f"{base}/_matrix/client/v1/media/download/"
        f"{quote(server_name, safe='[]:.')}/{quote(media_id, safe='')}"
    )
    headers = {"Authorization": f"Bearer {client.access_token}"}
    timeout = aiohttp.ClientTimeout(total=60)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    body = await resp.read()
                    ctype = resp.headers.get("Content-Type", "application/octet-stream")
                    disposition = resp.headers.get("Content-Disposition")
                    filename = _filename_from_content_disposition(disposition)
                    return body, ctype, filename
                if resp.status != 404:
                    text = await resp.text()
                    logger.warning(
                        "Matrix authenticated media download failed status=%s url=%s body=%s",
                        resp.status,
                        url,
                        text[:300],
                    )
                    return None
                logger.info(
                    "Matrix authenticated media endpoint returned 404, falling back to legacy: %s",
                    mxc_url,
                )
                return None
    except Exception:
        logger.exception("Matrix authenticated media download exception for %s", mxc_url)
        return None


def _truncate_caption(s: str, max_len: int = 1024) -> str:
    if len(s) <= max_len:
        return s
    return s[:max_len - 1] + "…"


async def send_matrix_media_to_telegram(
    bot: Bot,
    chat_id: int,
    caption_html: str,
    data: bytes,
    filename: str,
    mime: str,
    matrix_msgtype: str,
    *,
    matrix_voice: bool = False,
) -> None:
    cap = _truncate_caption(caption_html)
    fn0 = filename or "file"

    if matrix_msgtype == "m.image":
        fn = filename or "image.jpg"
        try:
            await bot.send_photo(
                chat_id, BufferedInputFile(data, filename=fn), caption=cap, parse_mode="HTML"
            )
        except Exception:
            await bot.send_document(
                chat_id, BufferedInputFile(data, filename=fn), caption=cap, parse_mode="HTML"
            )
        return
    if matrix_msgtype == "m.video":
        await bot.send_video(
            chat_id, BufferedInputFile(data, filename=fn0), caption=cap, parse_mode="HTML"
        )
        return
    if matrix_msgtype == "m.audio":
        info_voice = matrix_voice or mime in (
            "audio/ogg",
            "audio/opus",
            "application/ogg",
        ) or fn0.lower().endswith(".ogg")
        bio = BufferedInputFile(data, filename=fn0)
        if info_voice:
            await bot.send_voice(chat_id, bio, caption=cap, parse_mode="HTML")
        else:
            await bot.send_audio(chat_id, bio, caption=cap, parse_mode="HTML")
        return
    await bot.send_document(
        chat_id, BufferedInputFile(data, filename=fn0), caption=cap, parse_mode="HTML"
    )


async def telegram_photo_to_matrix(
    client: AsyncClient,
    room_id: str,
    body: str,
    data: bytes,
    mime: str,
    filename: str,
) -> bool:
    uri = await upload_bytes_to_matrix(client, data, mime, filename)
    if not uri:
        return False
    info: dict = {"mimetype": mime, "size": len(data)}
    await client.room_send(
        room_id,
        "m.room.message",
        {"msgtype": "m.image", "body": body, "url": uri, "info": info},
    )
    return True


async def telegram_video_like_to_matrix(
    client: AsyncClient,
    room_id: str,
    msgtype: str,
    body: str,
    data: bytes,
    mime: str,
    filename: str,
    duration: int | None = None,
) -> bool:
    uri = await upload_bytes_to_matrix(client, data, mime, filename)
    if not uri:
        return False
    info: dict = {"mimetype": mime, "size": len(data)}
    if duration is not None:
        info["duration"] = int(duration)
    await client.room_send(room_id, "m.room.message", {"msgtype": msgtype, "body": body, "url": uri, "info": info})
    return True


async def telegram_audio_to_matrix(
    client: AsyncClient,
    room_id: str,
    body: str,
    data: bytes,
    mime: str,
    filename: str,
    duration: int | None,
    is_voice: bool,
) -> bool:
    uri = await upload_bytes_to_matrix(client, data, mime, filename)
    if not uri:
        return False
    info: dict = {"mimetype": mime, "size": len(data)}
    if duration is not None:
        info["duration"] = int(duration)
    if is_voice:
        info["org.matrix.msc3245.voice"] = True
    await client.room_send(
        room_id,
        "m.room.message",
        {"msgtype": "m.audio", "body": body, "url": uri, "info": info},
    )
    return True


async def telegram_file_to_matrix(
    client: AsyncClient,
    room_id: str,
    body: str,
    data: bytes,
    mime: str,
    filename: str,
) -> bool:
    uri = await upload_bytes_to_matrix(client, data, mime, filename)
    if not uri:
        return False
    info: dict = {"mimetype": mime, "size": len(data)}
    await client.room_send(
        room_id,
        "m.room.message",
        {
            "msgtype": "m.file",
            "body": body,
            "url": uri,
            "filename": filename,
            "info": info,
        },
    )
    return True


def _guess_ext(mime: str, fallback: str) -> str:
    ext = mimetypes.guess_extension(mime or "") or ""
    if ext in (".htm", ".html", ".php"):
        ext = ""
    return ext or fallback


def _compose_media_body(label: str, kind: str, caption: str) -> str:
    prefix = f"{label}: [{kind}]"
    if caption:
        return f"{prefix} {caption}"
    return prefix


async def relay_telegram_message_media(
    bot: Bot,
    matrix: AsyncClient,
    room_id: str,
    label: str,
    message: Message,
) -> bool:
    caption = (message.caption or "").strip()

    if message.photo:
        p = message.photo[-1]
        buf = await bot.download(p)
        if buf is None:
            return False
        raw = buf.read()
        mime = "image/jpeg"
        fn = f"photo{_guess_ext(mime, '.jpg')}"
        return await telegram_photo_to_matrix(
            matrix, room_id, _compose_media_body(label, "photo", caption), raw, mime, fn
        )

    if message.video:
        v = message.video
        buf = await bot.download(v)
        if buf is None:
            return False
        raw = buf.read()
        mime = v.mime_type or "video/mp4"
        fn = v.file_name or f"video{_guess_ext(mime, '.mp4')}"
        return await telegram_video_like_to_matrix(
            matrix,
            room_id,
            "m.video",
            _compose_media_body(label, "video", caption),
            raw,
            mime,
            fn,
            v.duration,
        )

    if message.animation:
        a = message.animation
        buf = await bot.download(a)
        if buf is None:
            return False
        raw = buf.read()
        mime = a.mime_type or "video/mp4"
        fn = a.file_name or f"animation{_guess_ext(mime, '.mp4')}"
        return await telegram_video_like_to_matrix(
            matrix,
            room_id,
            "m.video",
            _compose_media_body(label, "GIF", caption),
            raw,
            mime,
            fn,
            a.duration,
        )

    if message.video_note:
        vn = message.video_note
        buf = await bot.download(vn)
        if buf is None:
            return False
        raw = buf.read()
        mime = "video/mp4"
        fn = f"videonote{_guess_ext(mime, '.mp4')}"
        return await telegram_video_like_to_matrix(
            matrix,
            room_id,
            "m.video",
            _compose_media_body(label, "video note", caption),
            raw,
            mime,
            fn,
            vn.duration,
        )

    if message.audio:
        a = message.audio
        buf = await bot.download(a)
        if buf is None:
            return False
        raw = buf.read()
        mime = a.mime_type or "audio/mpeg"
        fn = a.file_name or f"audio{_guess_ext(mime, '.mp3')}"
        return await telegram_audio_to_matrix(
            matrix,
            room_id,
            _compose_media_body(label, "audio", caption),
            raw,
            mime,
            fn,
            a.duration,
            False,
        )

    if message.voice:
        v = message.voice
        buf = await bot.download(v)
        if buf is None:
            return False
        raw = buf.read()
        mime = v.mime_type or "audio/ogg"
        fn = f"voice{_guess_ext(mime, '.ogg')}"
        return await telegram_audio_to_matrix(
            matrix,
            room_id,
            _compose_media_body(label, "voice", caption),
            raw,
            mime,
            fn,
            v.duration,
            True,
        )

    if message.document:
        d = message.document
        buf = await bot.download(d)
        if buf is None:
            return False
        raw = buf.read()
        mime = d.mime_type or "application/octet-stream"
        fn = d.file_name or f"file{_guess_ext(mime, '')}" or "file.bin"
        return await telegram_file_to_matrix(
            matrix, room_id, _compose_media_body(label, "file", caption), raw, mime, fn
        )

    if message.sticker:
        s = message.sticker
        buf = await bot.download(s)
        if buf is None:
            return False
        raw = buf.read()
        if s.is_video:
            mime = "video/webm"
            fn = "sticker.webm"
            return await telegram_video_like_to_matrix(
                matrix,
                room_id,
                "m.video",
                _compose_media_body(label, "sticker", caption),
                raw,
                mime,
                fn,
                None,
            )
        if s.is_animated:
            return await telegram_file_to_matrix(
                matrix,
                room_id,
                _compose_media_body(label, "sticker (tgs)", caption),
                raw,
                "application/x-tgsticker",
                "sticker.tgs",
            )
        mime = "image/webp"
        fn = "sticker.webp"
        return await telegram_photo_to_matrix(
            matrix, room_id, _compose_media_body(label, "sticker", caption), raw, mime, fn
        )

    return False


async def relay_matrix_media_event_to_telegram(
    bot: Bot,
    matrix: AsyncClient,
    chat_id: int,
    caption_html: str,
    event,
    matrix_msgtype: str,
) -> None:
    url = getattr(event, "url", None)
    if not url or not str(url).startswith("mxc://"):
        logger.warning("Skipping Matrix media: no mxc url (%s)", matrix_msgtype)
        return
    dl = await download_mxc_to_bytes(matrix, url)
    if not dl:
        return
    data, mime, fname = dl
    body = getattr(event, "body", "") or ""
    filename = fname or os.path.basename(body) or "file"
    if "." not in filename and mime:
        filename += _guess_ext(mime, ".bin")
    info = event.source.get("content", {}).get("info") or {}
    matrix_voice = bool(info.get("org.matrix.msc3245.voice"))
    try:
        await send_matrix_media_to_telegram(
            bot,
            chat_id,
            caption_html,
            data,
            filename,
            mime,
            matrix_msgtype,
            matrix_voice=matrix_voice,
        )
    except Exception:
        logger.exception("Failed to send media to Telegram")
