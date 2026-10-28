import unittest

from scripts.collect_results import evaluate, evaluate_replay, find_log, logs_since
from scripts.demo_common import load_fixtures, safe_id, source_id
from scripts.reset_demo import reset

FIXTURES, REVIEWS = load_fixtures()
SID = {fixture["id"]: source_id(fixture, FIXTURES) for fixture in FIXTURES}
REDIRECT = "leads+replies@gmail.com"
EDITED = next(line for line in REVIEWS[0]["edit_body"].split("\n") if len(line) > 60)


def log(fid, action, **extra):
    return {"ts": "2026-10-29T00:00:01", "source_id": SID[fid], "action": action, "alerts": [], "draft": "none", **extra}


def passing_inputs():
    logs = [
        log("T1", "created", tier="qualified", draft="approved", edited=True, contact_id="1", sent_to=REDIRECT, reviewer="Morgan Lee (Ops)"),
        log("T2", "updated", contact_id="1", draft="approved", edited=False),
        log("T3", "duplicate_submission", contact_id="1"),
        log("T4", "created", tier="not_qualified", contact_id="2"),
        log("T5", "created", tier="needs_review", draft="rejected", contact_id="3", sent_to=None),
        log("T6", "rejected_input", alerts=["x"]),
        log("T7", "created", tier="needs_review", llm_ok=False, contact_id="4", alerts=["x"]),
    ]
    send_log = {"results": [{"id": "T6", "http_status": 400}]}
    review_log = {"results": [{"fixture": "T1", "incomplete_review_status": 422}]}
    contacts = [{"email": "dana.whitfield@example.com", "inquiry_notes": 2, "lead_tier": "qualified"}]
    mail = [{"to": REDIRECT, "subject": "[demo → dana] a", "body": EDITED}, {"to": REDIRECT, "subject": "[demo → dana] b", "body": "other"}]
    return logs, send_log, review_log, contacts, mail


class ResultTests(unittest.TestCase):
    def test_all_pass_on_expected_state(self):
        logs, send_log, review_log, contacts, mail = passing_inputs()
        rows, summary = evaluate(FIXTURES, REVIEWS, logs, send_log, review_log, contacts, mail, REDIRECT)
        self.assertEqual(summary["passed"], 8, [row["problems"] for row in rows if not row["pass"]])

    def test_duplicate_contact_fails_t2(self):
        logs, send_log, review_log, contacts, mail = passing_inputs()
        logs[1]["contact_id"] = "99"
        rows, _ = evaluate(FIXTURES, REVIEWS, logs, send_log, review_log, contacts, mail, REDIRECT)
        self.assertFalse(next(row for row in rows if row["id"] == "T2")["pass"])

    def test_a_sent_rejected_reply_fails_t5_and_t8(self):
        logs, send_log, review_log, contacts, mail = passing_inputs()
        logs[4]["sent_to"] = REDIRECT
        rows, _ = evaluate(FIXTURES, REVIEWS, logs, send_log, review_log, contacts, mail, REDIRECT)
        failed = {row["id"] for row in rows if not row["pass"]}
        self.assertEqual(failed, {"T5", "T8"})

    def test_replay_requires_duplicates_and_same_contact_count(self):
        logs = [log(fid, "duplicate_submission") for fid in ("T1", "T2", "T3", "T4", "T5", "T7")]
        rows, summary = evaluate_replay(FIXTURES, logs, {"results": [{"id": "T6", "http_status": 400}]}, 4, 4)
        self.assertEqual(summary["passed"], summary["total"])
        _, changed = evaluate_replay(FIXTURES, logs, {"results": [{"id": "T6", "http_status": 400}]}, 4, 5)
        self.assertEqual(changed["passed"], changed["total"] - 1)

    def test_helpers(self):
        self.assertEqual(safe_id("email:<t2@example.com>"), "email_t2_example.com")
        self.assertEqual(SID["T3"], SID["T1"])
        self.assertEqual(len(logs_since([{"ts": "2026-10-28"}, {"ts": "2026-10-30"}], "2026-10-29")), 1)
        self.assertIsNone(find_log([], "x"))


class ResetTests(unittest.TestCase):
    def test_dry_run_deletes_nothing_and_skips_real_addresses(self):
        class Client:
            deleted = []
            def search_demo_contacts(self):
                return [{"id": "1", "properties": {"email": "a@example.com"}}, {"id": "2", "properties": {"email": "real@company.com"}}]
            def contact_note_ids(self, contact_id):
                return ["n1"]
            def delete_note(self, note_id):
                self.deleted.append(note_id)
            def delete_contact(self, contact_id):
                self.deleted.append(contact_id)
        from pathlib import Path
        import tempfile
        client = Client()
        result = reset(client, apply=False, output=Path(tempfile.mkdtemp()))
        self.assertEqual([item["id"] for item in result["contacts"]], ["1"])
        self.assertEqual(client.deleted, [])
        reset(client, apply=True, output=Path(tempfile.mkdtemp()))
        self.assertEqual(client.deleted, ["n1", "1"])


if __name__ == "__main__":
    unittest.main()
