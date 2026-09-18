"""Analyze demonstrations after a user has explicitly recorded/imported them."""
from __future__ import annotations
import json
from claude_agent_sdk import tool
from .context import ok, err
from .. import demonstrations as demos, config


@tool("demonstration_list", "List locally recorded demonstrations. Recording is started by the user with /learn, never by this tool.",
      {"type": "object", "properties": {}})
async def demonstration_list(args):
    return ok(json.dumps(demos.inventory(), ensure_ascii=False))


@tool("demonstration_read", "Read demonstration metadata and a page of sampled frame paths. Paths are not visual evidence. Use see if listed, or load it once when tool_schema lists it as deferred; otherwise visual inspection is unavailable. Sampling does not log keystrokes or prove clicks. annotations are human-authored, not captured tool actions; Read one annotation at a time with event_offset; next_event_offset identifies the following annotation. shortened_fields lists previews; retrieve complete text using event_field (action, target, before, after, outcome, application, app_version), field_offset and next_field_offset. Pass evidence_revision from the summary when paging to reject concurrent edits.",
      {"type": "object", "properties": {"id": {"type": "string"}, "offset": {"type": "integer"}, "event_offset": {"type": "integer"},
         "event_field": {"type": "string"}, "field_offset": {"type": "integer"},
         "evidence_revision": {"type": "integer"},
                                          "limit": {"type": "integer"}}, "required": ["id"]})
async def demonstration_read(args):
    try:
        info = demos.read(args["id"])
        offset = max(0, int(args.get("offset", 0)))
        limit = min(30, max(1, int(args.get("limit", 12))))
        frames = info.get("frames", [])
        annotations = demos.read_evidence(args["id"])
        events = annotations.pop("events")
        event_offset = max(0, int(args.get("event_offset", 0)))
        cap = config.TOOL_RESULT_CAP
        encode = lambda value: json.dumps(value, ensure_ascii=False)
        text_fields = ("action", "target", "before", "after", "outcome")
        field = args.get("event_field")
        if field is not None:
            if args.get("evidence_revision", annotations["revision"]) != annotations["revision"]:
                raise ValueError("Evidence changed; restart paging from the current revision")
            if field in {"application", "app_version"}:
                value = annotations[field]
            elif field in text_fields and event_offset < len(events):
                value = events[event_offset][field]
            else:
                raise ValueError("Choose an existing annotation and a supported event_field")
            start = max(0, int(args.get("field_offset", 0)))
            length = min(2000, max(0, len(value) - start))
            while True:
                end = start + length
                result = {"demonstration": args["id"], "revision": annotations["revision"],
                          "event_offset": event_offset, "event_field": field, "field_offset": start,
                          "text": value[start:end], "total_characters": len(value),
                          "next_field_offset": end if end < len(value) else None}
                encoded = encode(result)
                if len(encoded) <= cap:
                    return ok(encoded)
                if length <= 1:
                    fallback = encode({"error": "Increase TOOL_RESULT_CAP to read evidence fields"})
                    return err(fallback if len(fallback) <= cap else "{}")
                length //= 2
        result = {**info, "annotations": {**annotations, "events": events[event_offset:event_offset+1],
                  "total_events": len(events), "next_event_offset": event_offset + 1 if event_offset + 1 < len(events) else None},
                  "frames": frames[offset:offset+limit], "total_frames": len(frames),
                  "next_frame_offset": offset + limit if offset + limit < len(frames) else None,
                  "directory": str(demos.directory(args["id"]))}
        encoded = encode(result)
        if len(encoded) <= cap:
            return ok(encoded)
        # Preserve complete evidence on disk; expose explicit previews and a lossless field reader.
        result["field_paging"] = "Use event_field and field_offset to retrieve shortened fields; pass evidence_revision to reject concurrent edits."
        width = 256
        while True:
            for event in result["annotations"]["events"]:
                original = events[event_offset]
                event = dict(original)
                event["shortened_fields"] = {key: len(original[key]) for key in text_fields if len(original[key]) > width}
                for key in text_fields:
                    event[key] = original[key][:width]
                result["annotations"]["events"] = [event]
            result["annotations"]["shortened_fields"] = {
                key: len(annotations[key]) for key in ("application", "app_version") if len(annotations[key]) > width}
            for key in ("application", "app_version"):
                result["annotations"][key] = annotations[key][:width]
            encoded = encode(result)
            if len(encoded) <= cap:
                return ok(encoded)
            if width:
                width //= 2
                continue
            if result["frames"]:
                result["frames"].pop()
                result["next_frame_offset"] = offset + len(result["frames"])
                continue
            optional = [key for key in result if key not in {"id", "annotations", "frames", "total_frames", "next_frame_offset", "field_paging", "metadata_omitted"}]
            if optional:
                result.pop(optional[0])
                result["metadata_omitted"] = True
                continue
            fallback = encode({"error": "Tool result cap is too small; request event_field directly"})
            return err(fallback if len(fallback) <= cap else "{}")
    except (OSError, ValueError, KeyError) as exc:
        return err(str(exc))


@tool("demonstration_draft", "Write a reusable SKILL.md draft after inspecting demonstration frames. Cite frame paths, label inferred actions, and state checks and uncertainty. Cite saved annotation evidence_ids to preserve app version, before/action/after and outcome. User-confirmed basis requires saved human evidence. This never installs, enables or replays the skill.",
      {"type": "object", "properties": {"id": {"type": "string"}, "goal": {"type": "string"},
         "steps": {"type": "array", "items": {"type": "object", "properties": {
             "action": {"type": "string"}, "frames": {"type": "array", "items": {"type": "string"}},
             "basis": {"type": "string", "enum": ["visible", "inferred", "user-confirmed"]},
             "evidence_ids": {"type": "array", "items": {"type": "string"}},
             "verify": {"type": "string"}}, "required": ["action", "frames", "basis"]}},
         "uncertainty": {"type": "string"}}, "required": ["id", "goal", "steps"]})
async def demonstration_draft(args):
    try:
        path = demos.create_draft(args["id"], goal=args["goal"], steps=args["steps"], uncertainty=args.get("uncertainty", ""))
        return ok(f"Draft saved: {path}. Ask the user to review it; /learn install {args['id']} installs it disabled.")
    except (OSError, ValueError, KeyError) as exc:
        return err(str(exc))


DEMONSTRATION_TOOLS = [demonstration_list, demonstration_read, demonstration_draft]
