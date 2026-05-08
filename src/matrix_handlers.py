from __future__ import annotations

import logging

from nio import (
    AsyncClient,
    InviteMemberEvent,
    JoinError,
    JoinResponse,
    RoomMemberEvent,
    RoomMessageAudio,
    RoomMessageFile,
    RoomMessageImage,
    RoomMessageText,
    RoomMessageVideo,
    SyncResponse,
)

from src.bridge_service import BridgeService

logger = logging.getLogger(__name__)


def _own_mxid(client: AsyncClient) -> str:
    # nio may leave user_id empty when using a pre-existing access token.
    uid = (getattr(client, "user_id", "") or "").strip()
    if uid:
        return uid
    return (getattr(client, "user", "") or "").strip()


def register_matrix_callbacks(client: AsyncClient, bridge: BridgeService) -> None:
    first_sync_done = False

    async def on_first_sync(_response: SyncResponse) -> None:
        nonlocal first_sync_done
        if first_sync_done:
            return
        first_sync_done = True
        for room_id in list(client.rooms.keys()):
            try:
                await bridge.on_bot_joined_matrix_room(room_id)
            except Exception:
                logger.exception("Matrix welcome bootstrap failed for %s", room_id)
        me = _own_mxid(client)
        for room_id in list(client.invited_rooms.keys()):
            if room_id in client.rooms:
                continue
            try:
                logger.info("Auto-joining residual invite: %s (mxid=%s)", room_id, me)
                resp = await client.join(room_id)
                if isinstance(resp, JoinError):
                    logger.error("Matrix join failed for %s: %s", room_id, resp.message)
                elif isinstance(resp, JoinResponse):
                    logger.info("Joined Matrix room %s", room_id)
            except Exception:
                logger.exception("Matrix join (bootstrap invite) failed for %s", room_id)

    async def on_invite(room, event: InviteMemberEvent) -> None:
        me = _own_mxid(client)
        if event.state_key != me:
            logger.debug("Invite m.room.member for another user: state_key=%s us=%s", event.state_key, me)
            return
        room_id = room.room_id
        logger.info("Accepting invite to room %s (mxid=%s)", room_id, me)
        resp = await client.join(room_id)
        if isinstance(resp, JoinError):
            logger.error("Matrix join failed for %s: %s", room_id, resp.message)
        elif isinstance(resp, JoinResponse):
            logger.info("Joined Matrix room %s", room_id)

    async def on_room_member(room, event: RoomMemberEvent) -> None:
        if event.state_key != _own_mxid(client):
            return
        if event.membership != "join":
            return
        await bridge.on_bot_joined_matrix_room(room.room_id)

    async def on_text(room, event: RoomMessageText) -> None:
        room_id = room.room_id
        body = event.body or ""
        if not bridge.is_fresh_matrix_event(getattr(event, "server_timestamp", None)):
            return
        if bridge.parse_link_command(body) or bridge.is_unlink_command(body):
            await bridge.try_link_from_matrix(room_id, body)
        else:
            await bridge.relay_matrix_to_telegram(
                room,
                event.sender,
                body,
                server_ts_ms=getattr(event, "server_timestamp", None),
            )

    async def on_room_media(room, event) -> None:
        if isinstance(event, RoomMessageImage):
            mt = "m.image"
        elif isinstance(event, RoomMessageVideo):
            mt = "m.video"
        elif isinstance(event, RoomMessageAudio):
            mt = "m.audio"
        elif isinstance(event, RoomMessageFile):
            mt = "m.file"
        else:
            return
        await bridge.relay_matrix_media_to_telegram(room, event, mt)

    client.add_response_callback(on_first_sync, SyncResponse)
    client.add_event_callback(on_invite, InviteMemberEvent)
    client.add_event_callback(on_room_member, RoomMemberEvent)
    client.add_event_callback(on_text, RoomMessageText)
    client.add_event_callback(
        on_room_media,
        (RoomMessageImage, RoomMessageVideo, RoomMessageAudio, RoomMessageFile),
    )
