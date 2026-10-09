#!/usr/bin/env python3
"""Consume an authorized JSONL reply export and sync mapped CRM pages to Notion."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from api.verified_reply_sync import IdentityReview, ReplyLedger, ReplySyncError, sync_reply


class NotionClient:
    def __init__(self, token: str, database_id: str):
        self.token = token
        self.database_id = database_id

    def _request(self, method: str, path: str, payload=None):
        request = urllib.request.Request(
            "https://api.notion.com/v1" + path,
            data=None if payload is None else json.dumps(payload).encode(),
            method=method,
            headers={"Authorization": f"Bearer {self.token}", "Notion-Version": "2022-06-28",
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ReplySyncError("notion_request_failed") from exc

    def get_page(self, page_id):
        return self._request("GET", f"/pages/{page_id}")

    def patch_page(self, page_id, properties):
        return self._request("PATCH", f"/pages/{page_id}", {"properties": properties})


def _plain(prop):
    kind = (prop or {}).get("type")
    value = (prop or {}).get(kind) or {}
    if kind in {"title", "rich_text"}:
        return "".join(item.get("plain_text", item.get("text", {}).get("content", "")) for item in value)
    if kind == "email":
        return value or ""
    return ""


def consume(events_path: Path, mapping_path: Path, ledger_path: Path, notion: NotionClient):
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    ledger = ReplyLedger(ledger_path)
    counts = {"updated": 0, "duplicate": 0, "stale": 0, "reconciled": 0,
              "review": 0, "error": 0, "excluded": 0}
    with events_path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                source_event = json.loads(line)
                # The export may never nominate a Notion page or assert that
                # identity was verified. Resolve both from the reviewed map.
                source_event.pop("identity_verified", None)
                source_event.pop("crm_page_id", None)
                if (source_event.get("inbound") is not True
                        or source_event.get("autoresponder") is not False
                        or source_event.get("echo") is not False):
                    counts["excluded"] += 1
                    continue
                identity = source_event.get("identity_key")
                key = "|".join((source_event.get("account", ""), source_event.get("channel", ""), str(identity or "")))
                match = mapping.get(key)
                if not match or not match.get("page_id") or not match.get("identity_value"):
                    counts["review"] += 1
                    continue
                if source_event.get("channel") == "instagram":
                    if (source_event.get("recipient_account") != source_event.get("account")
                            or source_event.get("sender_igsid") != identity):
                        counts["review"] += 1
                        continue
                elif source_event.get("channel") == "email":
                    refs = set(source_event.get("references") or [])
                    if source_event.get("in_reply_to"):
                        refs.add(source_event["in_reply_to"])
                    outbound_ids = set(match.get("outbound_message_ids") or [])
                    if (not source_event.get("sender_email")
                            or source_event["sender_email"].strip().casefold() != str(match["identity_value"]).strip().casefold()
                            or not match.get("thread_id")
                            or source_event.get("thread_id") != match["thread_id"]
                            or not refs.intersection(outbound_ids)):
                        counts["review"] += 1
                        continue
                source_event["crm_page_id"] = match["page_id"]
                source_event["identity_verified"] = True

                def identity_check(event, page):
                    props = page.get("properties") or {}
                    field = match.get("notion_property")
                    actual = _plain(props.get(field))
                    parent = page.get("parent") or {}
                    return (page.get("id") == match["page_id"]
                            and parent.get("database_id") == notion.database_id
                            and actual.casefold() == str(match["identity_value"]).casefold())

                sync_fields = {"channel", "account", "message_id", "occurred_at", "summary",
                               "next_action", "identity_key", "crm_page_id", "identity_verified",
                               "inbound", "autoresponder", "echo"}
                event = {name: value for name, value in source_event.items() if name in sync_fields}
                result = sync_reply(event, notion=notion, ledger=ledger, identity_check=identity_check)
                counts[result["action"]] += 1
            except IdentityReview:
                counts["review"] += 1
            except ReplySyncError:
                counts["error"] += 1
            except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                counts["error"] += 1
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True, help="Authorized inbound event export, JSONL")
    parser.add_argument("--mapping", type=Path, required=True, help="Reviewed identity to existing Notion page map")
    parser.add_argument("--ledger", type=Path, required=True, help="Durable local receipt database")
    args = parser.parse_args()
    token = os.environ.get("NOTION_API_TOKEN", "").strip()
    if not token:
        parser.error("NOTION_API_TOKEN is required in the environment")
    database_id = os.environ.get("NOTION_CRM_DATABASE_ID", "").strip()
    if not database_id:
        parser.error("NOTION_CRM_DATABASE_ID is required in the environment")
    # Prevent arbitrary page IDs in a mapping accidentally crossing CRM scope.
    notion = NotionClient(token, database_id)
    try:
        counts = consume(args.events, args.mapping, args.ledger, notion)
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__}), file=sys.stderr)
        return 2
    print(json.dumps(counts, sort_keys=True))
    return 0 if counts["error"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
