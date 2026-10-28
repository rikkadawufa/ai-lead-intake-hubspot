"""Check T1–T8 against the run logs, HubSpot and the demo inbox, then write output/results.md.

Every number in the results comes from this script, not from the AI.
  python scripts/collect_results.py            # evaluate the latest initial batch and reviews
  python scripts/collect_results.py --replay   # evaluate the latest replay batch (idempotency)
"""
import argparse
import email
import imaplib
import json
from datetime import datetime
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from pathlib import Path

try:
    from .demo_common import OUTPUT, RUN_LOG_DIR, by_id, load_fixtures, read_json_files, source_id
    from .envfile import load_env, require
    from .hubspot_client import HubSpot
except ImportError:
    from demo_common import OUTPUT, RUN_LOG_DIR, by_id, load_fixtures, read_json_files, source_id
    from envfile import load_env, require
    from hubspot_client import HubSpot


def latest_send_log(mode, output=OUTPUT):
    paths = sorted(Path(output).glob(f"send-log-{mode}-*.json"))
    return json.loads(paths[-1].read_text(encoding="utf-8")) if paths else None


def logs_since(logs, started):
    return [log for log in logs if log.get("ts", "") >= started]


def find_log(logs, sid, action=None):
    matches = [log for log in logs if log.get("source_id") == sid and (action is None or log.get("action") == action)]
    return matches[-1] if matches else None


def row(fixture_id, title, checks):
    failed = [f"{name}: expected {expected!r}, got {actual!r}" for name, expected, actual in checks if expected != actual]
    shown = "; ".join(f"{name}={actual}" for name, _, actual in checks)
    return {"id": fixture_id, "title": title, "pass": not failed, "observed": shown, "problems": failed}


def evaluate(fixtures, reviews, logs, send_log, review_log, contacts, sent_mail, redirect_to):
    fx = by_id(fixtures)
    sid = {fixture["id"]: source_id(fixture, fixtures) for fixture in fixtures}
    sends = {item["id"]: item for item in (send_log or {}).get("results", [])}
    by_email = {contact["email"]: contact for contact in contacts}
    log = lambda fixture_id, action=None: find_log(logs, sid[fixture_id], action) or {}
    first = {k: log(k, "created") or log(k, "updated") for k in ("T1", "T2", "T4", "T5", "T7")}
    dana = by_email.get(fx["T1"]["payload"]["email"], {})
    t1_review = next((item for item in (review_log or {}).get("results", []) if item.get("fixture") == "T1"), {})
    demo_mail = [item for item in sent_mail if item["to"] == redirect_to]
    t1_edit = next((item.get("edit_body") for item in reviews if item["fixture"] == "T1"), "")
    edited_line = next((line for line in t1_edit.split("\n") if len(line) > 60), None)
    edited_mail = [item for item in demo_mail if edited_line and edited_line in item["body"]]
    rows = [
        row("T1", fx["T1"]["title"], [
            ("action", "created", first["T1"].get("action")), ("tier", "qualified", first["T1"].get("tier")),
            ("draft", "approved", first["T1"].get("draft")), ("edited", True, first["T1"].get("edited")),
        ]),
        row("T2", fx["T2"]["title"], [
            ("action", "updated", first["T2"].get("action")),
            ("same contact as T1", True, bool(first["T1"].get("contact_id")) and first["T2"].get("contact_id") == first["T1"].get("contact_id")),
            ("inquiry notes on contact", 2, dana.get("inquiry_notes")), ("final tier", "qualified", dana.get("lead_tier")),
            ("draft", "approved", first["T2"].get("draft")), ("edited", False, first["T2"].get("edited")),
        ]),
        row("T3", fx["T3"]["title"], [
            ("action", "duplicate_submission", log("T3", "duplicate_submission").get("action")),
            ("same contact as T1", True, bool(first["T1"].get("contact_id")) and log("T3", "duplicate_submission").get("contact_id") == first["T1"].get("contact_id")),
            ("drafts for this submission", 1, len([item for item in logs if item.get("source_id") == sid["T1"] and item.get("draft") != "none"])),
        ]),
        row("T4", fx["T4"]["title"], [
            ("action", "created", first["T4"].get("action")), ("tier", "not_qualified", first["T4"].get("tier")), ("draft", "none", first["T4"].get("draft")),
        ]),
        row("T5", fx["T5"]["title"], [
            ("action", "created", first["T5"].get("action")), ("tier", "needs_review", first["T5"].get("tier")),
            ("draft", "rejected", first["T5"].get("draft")), ("sent", None, first["T5"].get("sent_to")),
        ]),
        row("T6", fx["T6"]["title"], [
            ("HTTP status", 400, sends.get("T6", {}).get("http_status")),
            ("Slack alert", 1, len(log("T6", "rejected_input").get("alerts", []))),
            ("HubSpot contact", None, log("T6", "rejected_input").get("contact_id")),
        ]),
        row("T7", fx["T7"]["title"], [
            ("action", "created", first["T7"].get("action")), ("tier", "needs_review", first["T7"].get("tier")),
            ("llm_ok", False, first["T7"].get("llm_ok")), ("draft", "none", first["T7"].get("draft")), ("Slack alert", 1, len(first["T7"].get("alerts", []))),
        ]),
        row("T8", "Human review: edit + approve, approve, reject", [
            ("incomplete review refused", 422, t1_review.get("incomplete_review_status")),
            ("T1 reply sent to demo inbox", redirect_to, first["T1"].get("sent_to")),
            ("T1 reviewer", "Morgan Lee (Ops)", first["T1"].get("reviewer")),
            ("emails with the edited T1 text", 1, len(edited_mail)),
            ("T5 nothing sent", None, first["T5"].get("sent_to")),
            ("replies in demo inbox", 2, len(demo_mail)),
        ]),
    ]
    summary = {"passed": sum(item["pass"] for item in rows), "total": len(rows), "demo_contacts": len(contacts)}
    return rows, summary


def evaluate_replay(fixtures, logs, replay_log, contacts_before, contacts_after):
    sends = {item["id"]: item for item in replay_log.get("results", [])}
    rows = []
    for fixture in fixtures:
        sid = source_id(fixture, fixtures)
        if fixture["id"] == "T6":
            rows.append(row("T6", fixture["title"], [("HTTP status", 400, sends.get("T6", {}).get("http_status"))]))
            continue
        entry = find_log(logs, sid) or {}
        rows.append(row(fixture["id"], fixture["title"], [("action", "duplicate_submission", entry.get("action")), ("new draft", "none", entry.get("draft"))]))
    rows.append(row("ALL", "Contacts unchanged after replay", [("demo contacts", contacts_before, contacts_after)]))
    return rows, {"passed": sum(item["pass"] for item in rows), "total": len(rows), "demo_contacts": contacts_after}


def hubspot_state(client):
    contacts = []
    for item in client.search_demo_contacts():
        props = item["properties"]
        notes = client.get_notes(client.contact_note_ids(item["id"]))
        bodies = [note["properties"].get("hs_note_body") or "" for note in notes]
        contacts.append({
            "id": item["id"], "email": props.get("email"), "lead_tier": props.get("lead_tier"),
            "inquiry_notes": sum(body.startswith(("<p>[form]", "<p>[email]")) for body in bodies),
            "decision_notes": sum("Reply drafted by AI" in body for body in bodies),
        })
    return contacts


def demo_inbox_replies(values, since_iso):
    """Approved replies that landed in the demo inbox (the REPLY_REDIRECT_TO plus-address)."""
    gmail, password = require(values, "GMAIL_ADDRESS"), require(values, "GMAIL_APP_PASSWORD").replace(" ", "")
    started = datetime.fromisoformat(since_iso)
    since = started.strftime("%d-%b-%Y")
    found = []
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        imap.login(gmail, password)
        imap.select("INBOX", readonly=True)
        _, data = imap.search(None, "SINCE", since, "SUBJECT", "demo")
        for num in data[0].split():
            _, parts = imap.fetch(num, "(BODY.PEEK[])")
            message = email.message_from_bytes(parts[0][1])
            subject = str(make_header(decode_header(message.get("Subject", ""))))
            # IMAP SINCE only filters by day, so drop replies from earlier runs on the same day.
            if not subject.startswith("[demo") or parsedate_to_datetime(message.get("Date")) < started:
                continue
            body = message.get_payload(decode=True) if not message.is_multipart() else message.get_payload(0).get_payload(decode=True)
            found.append({"to": message.get("To", "").strip().lower(), "subject": subject, "body": (body or b"").decode("utf-8", "replace").replace("\r\n", "\n"), "date": message.get("Date")})
    finally:
        imap.logout()
    return found


def write_results(rows, summary, title, stem="results"):
    lines = [f"# {title}", "", f"Generated {datetime.now().isoformat(timespec='seconds')} by `scripts/collect_results.py` (counts come from code, not the AI).", "",
             f"**{summary['passed']} / {summary['total']} passed** · demo contacts in HubSpot: {summary['demo_contacts']}", "",
             "| # | Scenario | Result | Observed |", "|---|---|---|---|"]
    for item in rows:
        lines.append(f"| {item['id']} | {item['title']} | {'PASS' if item['pass'] else 'FAIL'} | {item['observed']} |")
    problems = [f"- {item['id']}: {problem}" for item in rows for problem in item["problems"]]
    if problems:
        lines += ["", "## Problems", *problems]
    OUTPUT.mkdir(exist_ok=True)
    (OUTPUT / f"{stem}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUTPUT / f"{stem}.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=2), encoding="utf-8")
    print("\n".join(lines))


def print_summary(rows, summary, redirect_to, width=104):
    """Short console view: one wrapped line per scenario, demo inbox address hidden."""
    for item in rows:
        observed = item["observed"].replace(redirect_to, "demo inbox") if redirect_to else item["observed"]
        words, line, out = observed.replace("; ", ", ").split(" "), "", []
        for word in words:
            if line and len(line) + 1 + len(word) > width - 10:
                out.append(line)
                line = word
            else:
                line = f"{line} {word}".strip()
        out.append(line)
        print(f"{item['id']:<4}{'PASS' if item['pass'] else 'FAIL'}  {out[0]}")
        for extra in out[1:]:
            print(f"{'':10}{extra}")
    print(f"{summary['passed']} / {summary['total']} passed, {summary['demo_contacts']} demo contacts in HubSpot")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--summary", action="store_true", help="print a short per-scenario view instead of the Markdown table")
    args = parser.parse_args()
    values = load_env()
    fixtures, reviews = load_fixtures()
    client = HubSpot(require(values, "HUBSPOT_TOKEN"))
    all_logs = read_json_files(RUN_LOG_DIR)
    if args.replay:
        replay = latest_send_log("replay")
        if not replay:
            raise SystemExit("No replay batch found; run send_fixtures.py --replay first.")
        before = json.loads((OUTPUT / "results.json").read_text(encoding="utf-8"))["summary"]["demo_contacts"] if (OUTPUT / "results.json").exists() else None
        rows, summary = evaluate_replay(fixtures, logs_since(all_logs, replay["run_started"]), replay, before, len(client.search_demo_contacts()))
        write_results(rows, summary, "Replay check (same batch sent twice)", stem="results-replay")
        return
    batch = latest_send_log("initial")
    if not batch:
        raise SystemExit("No initial batch found; run send_fixtures.py first.")
    logs = logs_since(all_logs, batch["run_started"])
    rows, summary = evaluate(fixtures, reviews, logs, batch, latest_send_log("review"), hubspot_state(client),
                             demo_inbox_replies(values, batch["run_started"]), values.get("REPLY_REDIRECT_TO", "").lower())
    if args.summary:
        print_summary(rows, summary, values.get("REPLY_REDIRECT_TO", "").lower())
        return
    write_results(rows, summary, "Lead intake test results (T1–T8)")


if __name__ == "__main__":
    main()
