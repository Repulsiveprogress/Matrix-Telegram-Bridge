"""Patches for matrix-nio to support Matrix room version 11+.

In v11+, m.room.create no longer includes a `creator` field in content (MSC2175);
the author is in `sender`. nio 0.24 has a strict JSON schema and a from_dict
that KeyErrors on missing `creator`. This patch relaxes both.
"""

from __future__ import annotations

import copy
from typing import Any


def apply_nio_schema_patches() -> None:
    from nio.events import room_events
    from nio.schemas import Schemas

    rc_content = Schemas.room_create["properties"]["content"]
    req = list(rc_content.get("required", []))
    if "creator" in req:
        rc_content["required"] = [r for r in req if r != "creator"]

    _orig = room_events.RoomCreateEvent.from_dict.__func__

    @classmethod
    def _room_create_from_dict(
        cls: Any,
        parsed_dict: dict[str, Any],
    ) -> Any:
        pd = copy.deepcopy(parsed_dict)
        content = pd.setdefault("content", {})
        if "creator" not in content:
            content["creator"] = pd.get("sender") or ""
        if "m.federate" not in content:
            content["m.federate"] = True
        if "room_version" not in content:
            content["room_version"] = "1"
        return _orig(cls, pd)

    room_events.RoomCreateEvent.from_dict = _room_create_from_dict
