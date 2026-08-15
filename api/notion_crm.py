"""Deterministic Notion CRM adapter for VisionBuilts intake."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


NOTION_VERSION = "2022-06-28"
EXPECTED_PROPERTIES = {
    "Handle IG": "title",
    "Stato": "status",
    "Follower": "number",
    "Fonte": "rich_text",
    "Lingua": "select",
    "URL profilo": "url",
    "Nicchia/Ricette": "multi_select",
    "Note": "rich_text",
    "Owner": "select",
    "External ID": "rich_text",
    "Canale": "select",
    "Email": "email",
    "Nome": "rich_text",
    "Ultimo intake": "date",
    "Ultimo oggetto": "rich_text",
}
class NotionError(RuntimeError):
    pass


class NotionSchemaError(NotionError):
    pass


class NotionIdentityConflict(NotionError):
    pass


@dataclass
class NotionResult:
    page_id: str
    action: str


class NotionCRM:
    def __init__(self, token: str, database_id: str, *, transport=None, sleep=time.sleep):
        self.token = token
        self.database_id = database_id
        self.transport = transport or self._transport
        self.sleep = sleep
        self._schema_validated = False

    @classmethod
    def from_env(cls):
        token = os.getenv("NOTION_API_TOKEN", "").strip()
        database_id = os.getenv("NOTION_CRM_DATABASE_ID", "").strip()
        if not token or not database_id:
            raise NotionError("notion_not_configured")
        return cls(token, database_id)

    def _transport(self, method: str, path: str, payload: dict | None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            "https://api.notion.com/v1" + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers), json.loads(response.read() or b"{}")

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        for attempt in range(3):
            try:
                status, headers, body = self.transport(method, path, payload)
                if 200 <= status < 300:
                    return body
                if status == 429 or status >= 500:
                    if attempt < 2:
                        self.sleep(float(headers.get("Retry-After") or 2**attempt))
                        continue
                raise NotionError(f"notion_http_{status}")
            except urllib.error.HTTPError as exc:
                if (exc.code == 429 or exc.code >= 500) and attempt < 2:
                    self.sleep(float(exc.headers.get("Retry-After") or 2**attempt))
                    continue
                raise NotionError(f"notion_http_{exc.code}") from None
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt < 2:
                    self.sleep(2**attempt)
                    continue
                raise NotionError("notion_network_error") from None
        raise NotionError("notion_retry_exhausted")

    def validate_schema(self) -> None:
        database = self._request("GET", f"/databases/{self.database_id}")
        props = database.get("properties") or {}
        invalid = [
            name
            for name, expected_type in EXPECTED_PROPERTIES.items()
            if (props.get(name) or {}).get("type") != expected_type
        ]
        if invalid:
            raise NotionSchemaError("notion_schema_incompatible:" + ",".join(sorted(invalid)))
        self._schema_validated = True

    def _query(self, prop: str, kind: str, value: str) -> list[dict]:
        if not value:
            return []
        body = {
            "filter": {"property": prop, kind: {"equals": value}},
            "page_size": 10,
        }
        return self._request("POST", f"/databases/{self.database_id}/query", body).get("results", [])

    @staticmethod
    def _plain(prop: dict) -> str:
        kind = prop.get("type")
        if kind == "status":
            return ((prop.get("status") or {}).get("name") or "")
        return ""

    def _find_identity(self, external_id: str, email: str, handle_title: str) -> dict | None:
        groups = [
            self._query("External ID", "rich_text", external_id),
            self._query("Email", "email", email),
            self._query("Handle IG", "title", handle_title),
        ]
        matches = {page["id"]: page for group in groups for page in group}
        if len(matches) > 1:
            raise NotionIdentityConflict("notion_identity_conflict")
        return next(iter(matches.values()), None)

    @staticmethod
    def _rich_text(value: str) -> dict:
        chunks = [value[i : i + 2000] for i in range(0, len(value), 2000)] or [""]
        return {"rich_text": [{"type": "text", "text": {"content": chunk}} for chunk in chunks]}

    def _properties(self, event: dict, extraction: dict, existing: dict | None) -> dict:
        lead = extraction["lead"]
        email = lead.get("email") or event.get("sender", {}).get("email") or ""
        handle = lead.get("instagram_handle") or event.get("sender", {}).get("handle") or ""
        title = f"@{handle}" if handle else email
        external_id = f"email:{email}" if email else event["sender"]["external_id"]
        current_status = ""
        if existing:
            current_status = self._plain((existing.get("properties") or {}).get("Stato") or {})
        # Conservative v1 default: a new application starts at Da contattare.
        # Existing records retain their state because this event contract does
        # not yet carry a verified outreach-thread link/In-Reply-To relation.
        desired_status = current_status or "Da contattare"
        profile_url = next((u for u in lead.get("profile_urls", []) if "instagram.com/" in u), "")
        note = "\n".join(v for v in [lead.get("message_summary"), lead.get("next_action")] if v)
        subject = str(event.get("message", {}).get("subject") or "")[:500]
        props = {
            "Handle IG": {"title": [{"text": {"content": title[:200]}}]},
            "External ID": self._rich_text(external_id[:500]),
            "Canale": {"select": {"name": "Email"}},
            "Email": {"email": email or None},
            "Nome": self._rich_text(lead.get("name", "")[:500]),
            "Ultimo intake": {"date": {"start": event["occurred_at"]}},
            "Ultimo oggetto": self._rich_text(subject),
            "Stato": {"status": {"name": desired_status}},
            "Fonte": self._rich_text(
                f"{lead.get('source_label') or 'VisionBuilts email'} | {event['event_id']}"[:2000]
            ),
            "Note": self._rich_text(note[:4000]),
            "Owner": {"select": {"name": "Auto"}},
        }
        if lead.get("followers") is not None:
            props["Follower"] = {"number": lead["followers"]}
        if lead.get("language"):
            props["Lingua"] = {"select": {"name": lead["language"]}}
        if profile_url:
            props["URL profilo"] = {"url": profile_url[:2000]}
        if lead.get("niches"):
            props["Nicchia/Ricette"] = {
                "multi_select": [{"name": n[:100]} for n in lead["niches"][:20]]
            }
        return props

    def upsert(self, event: dict, extraction: dict) -> NotionResult:
        if not self._schema_validated:
            self.validate_schema()
        lead = extraction["lead"]
        email = lead.get("email") or event.get("sender", {}).get("email") or ""
        handle = lead.get("instagram_handle") or event.get("sender", {}).get("handle") or ""
        external_id = f"email:{email}" if email else event["sender"]["external_id"]
        existing = self._find_identity(external_id, email, f"@{handle}" if handle else "")
        properties = self._properties(event, extraction, existing)
        if existing:
            result = self._request("PATCH", f"/pages/{existing['id']}", {"properties": properties})
            return NotionResult(result.get("id") or existing["id"], "updated")
        result = self._request(
            "POST", "/pages", {"parent": {"database_id": self.database_id}, "properties": properties}
        )
        return NotionResult(result["id"], "created")
