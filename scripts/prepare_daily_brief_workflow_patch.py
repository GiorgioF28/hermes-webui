"""Prepare a reviewed n8n workflow JSON patch; this tool never writes to n8n."""

from __future__ import annotations

import argparse
import copy
import json
import uuid
from pathlib import Path
from typing import Any


NORMALIZER = r'''const sourceLabel = 'gmail-personale';
const inputs = $input.all();
const sourceFailed = inputs.some(({json}) => Boolean(json.sourceError || json.error));
const rows = [];
let invalid = 0;
const header = (headers, name) => {
  const found = Array.isArray(headers) ? headers.find(item => String(item?.name || '').toLowerCase() === name.toLowerCase()) : null;
  return found?.value == null ? '' : String(found.value);
};
for (const {json} of inputs) {
  const rawFrom = json.from;
  const headers = json.headers || json.payload?.headers;
  const fromText = typeof rawFrom === 'string' ? rawFrom : (rawFrom?.value?.[0]?.address || rawFrom?.email || rawFrom?.address || rawFrom?.text || header(headers, 'from'));
  const fromName = typeof rawFrom === 'object' && rawFrom ? (rawFrom.value?.[0]?.name || rawFrom.name || '') : '';
  const rawSubject = json.subject ?? header(headers, 'subject');
  const subject = rawSubject == null ? '' : String(rawSubject).replace(/\s+/g, ' ').trim().slice(0, 200);
  const rawDate = json.date ?? json.receivedAt ?? json.internalDate ?? null;
  const dateValue = typeof rawDate === 'number' || (typeof rawDate === 'string' && /^\d{11,}$/.test(rawDate)) ? Number(rawDate) : rawDate;
  const date = dateValue == null || dateValue === '' ? null : new Date(dateValue);
  const receivedAt = date && Number.isFinite(date.getTime()) ? date.toISOString() : '';
  const messageId = String(json.messageId || json.message_id || json.id || '').trim();
  const textBody = String(json.bodyText || json.text || json.snippet || '');
  const htmlBody = String(json.html || '').replace(/<[^>]*>/g, ' ');
  const bodyText = textBody || htmlBody;
  const isBlankPassThrough = !fromText && !subject && !receivedAt && !messageId && !bodyText && !json.error && !json.sourceError;
  if (isBlankPassThrough) continue;
  if (!fromText || !receivedAt) { invalid += 1; continue; }
  rows.push({account: sourceLabel, messageId, from: String(fromText).trim(), fromName: String(fromName).trim(), subject, receivedAt, bodyText, bodyExcerpt: bodyText.slice(0, 2000), listUnsubscribe: Boolean(header(headers, 'list-unsubscribe'))});
}
const error = sourceFailed ? 'lettura Gmail non riuscita' : invalid ? `${invalid} messaggio/i senza mittente, data valida o identificativo` : null;
return [{json: {generatedAt: new Date().toISOString(), accounts: [{label: sourceLabel, count: rows.length, error}], noiseSkipped: 0, emails: rows}}];'''

IMAP_NORMALIZER = r'''const inputs = $input.all();
const rows = [];
const counts = new Map();
const invalid = new Map();
const clean = value => String(value || '').replace(/[\x00-\x1f\x7f-\x9f]/g, ' ').replace(/\s+/g, ' ').trim();
for (const {json} of inputs) {
  const account = clean(json.account);
  if (!account) continue;
  counts.set(account, (counts.get(account) || 0) + 1);
  const rawDate = json.receivedAt ?? json.date ?? null;
  const date = rawDate == null || rawDate === '' ? null : new Date(rawDate);
  const receivedAt = date && Number.isFinite(date.getTime()) ? date.toISOString() : '';
  const from = clean(json.from?.value?.[0]?.address || json.from?.text || json.from);
  if (!receivedAt || !from) {
    invalid.set(account, (invalid.get(account) || 0) + 1);
    continue;
  }
  const messageId = clean(json.messageId || json.message_id || '');
  const bodyText = String(json.bodyText || json.text || json.textAsHtml || '');
  rows.push({account, messageId, from, fromName: clean(json.from?.value?.[0]?.name), subject: clean(json.subject), receivedAt, bodyText, bodyExcerpt: clean(json.bodyExcerpt || bodyText).slice(0, 2000)});
}
const accounts = [...counts].map(([label, count]) => ({label, count: count - (invalid.get(label) || 0), error: invalid.has(label) ? `${invalid.get(label)} messaggio/i con data o mittente non valido` : null}));
return [{json: {emails: rows, accounts}}];'''


def _node(workflow: dict[str, Any], name: str) -> dict[str, Any]:
    found = [node for node in workflow.get("nodes", []) if node.get("name") == name]
    if len(found) != 1:
        raise ValueError(f"expected exactly one n8n node named {name!r}")
    return found[0]


def prepare(workflow: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(workflow)
    if result.get("id") != "HermesDailyBriefV2":
        raise ValueError("this patch is scoped to HermesDailyBriefV2")
    if not result.get("active"):
        raise ValueError("workflow is not active; review its current topology first")
    gmail = _node(result, "Gmail personale")
    normalizer = _node(result, "Normalize email rows")
    old_digest = _node(result, "POST email digest")
    imap_accumulator = _node(result, "POST email accumulate")
    imap_normalizer = {
        "id": str(uuid.uuid4()), "name": "Normalize IMAP rows", "type": "n8n-nodes-base.code",
        "typeVersion": 2, "position": [0, 700], "parameters": {"mode": "runOnceForAllItems", "jsCode": IMAP_NORMALIZER},
    }
    schedule = _node(result, "Schedule 07:00 Europe Rome")
    if not (gmail.get("onError") or "").startswith("continue"):
        raise ValueError("Gmail node must continue to the normalizer after provider failures")

    normalizer["parameters"]["jsCode"] = NORMALIZER
    normalizer["alwaysOutputData"] = True
    gmail["alwaysOutputData"] = True
    old_digest["name"] = "POST email accumulate Gmail"
    old_digest["parameters"]["url"] = "http://host.docker.internal:8788/api/cron/daily-brief/email-accumulate"
    old_digest["parameters"]["body"] = "={{ JSON.stringify($json) }}"
    old_digest["retryOnFail"] = True
    old_digest["maxTries"] = 3
    old_digest["waitBetweenTries"] = 3000
    old_digest["onError"] = "continueRegularOutput"

    # IMAP trigger events remain independent. They carry one message and mark
    # source success; they cannot claim a zero-count mailbox poll.
    imap_accumulator["retryOnFail"] = True
    imap_accumulator["maxTries"] = 3
    imap_accumulator["waitBetweenTries"] = 3000
    imap_accumulator["onError"] = "continueRegularOutput"
    imap_accumulator["parameters"]["body"] = "={{ JSON.stringify($json) }}"
    result["nodes"].append(imap_normalizer)

    recap = copy.deepcopy(old_digest)
    recap["id"] = str(uuid.uuid4())
    recap["name"] = "POST daily recap"
    old_position = old_digest.get("position", [0, 0])
    recap["position"] = [old_position[0] + 260, old_position[1]]
    recap["parameters"]["url"] = "http://host.docker.internal:8788/api/cron/daily-brief/email"
    recap["parameters"]["body"] = "={{ JSON.stringify({accounts: [], emails: []}) }}"
    recap["retryOnFail"] = True
    recap["maxTries"] = 3
    recap["waitBetweenTries"] = 5000
    recap["onError"] = "continueRegularOutput"
    result["nodes"].append(recap)

    connections = result["connections"]
    # Reroute Gmail normalizer to intake, then run the recap after intake
    # succeeds or exhausts its bounded retries. The daily schedule still runs
    # the DM check on its independent branch.
    for edge in connections["Normalize email rows"]["main"]:
        for target in edge:
            if target.get("node") == "POST email digest":
                target["node"] = "POST email accumulate Gmail"
    connections.pop("POST email digest", None)
    connections["POST email accumulate Gmail"] = {"main": [[{"node": "POST daily recap", "type": "main", "index": 0}]]}
    for label in ("Label Gmail secondario", "Label Yahoo"):
        connections[label]["main"] = [[{"node": "Normalize IMAP rows", "type": "main", "index": 0}]]
    connections["Normalize IMAP rows"] = {"main": [[{"node": "POST email accumulate", "type": "main", "index": 0}]]}
    schedule_edges = connections["Schedule 07:00 Europe Rome"]["main"]
    schedule["parameters"] = schedule.get("parameters", {})
    for edge in schedule_edges:
        edge[:] = [target for target in edge if target.get("node") != "POST email digest"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="n8n workflow export JSON")
    parser.add_argument("destination", type=Path, help="prepared workflow JSON output")
    args = parser.parse_args()
    workflow = json.loads(args.source.read_text(encoding="utf-8-sig"))
    prepared = prepare(workflow)
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    args.destination.write_text(json.dumps(prepared, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared {prepared['name']} patch at {args.destination}; n8n was not modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
