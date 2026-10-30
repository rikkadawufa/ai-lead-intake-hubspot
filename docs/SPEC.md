# Implementation spec: AI Lead Intake → HubSpot (n8n + OpenAI)

> The reasoning behind this project lives in internal planning notes that are not part of this repo. This file is the **spec to build from**. If something isn't covered here, or the real API behaves differently, log it under "Open decisions" in `docs/VERIFICATION.md`, then make the smallest change that keeps the original intent. Don't widen the scope.
> Code, comments, README, commit messages, and every file under `docs/` are in English.

---

## 0. Prerequisites (done by the user)
- `demo/.env` has `HUBSPOT_TOKEN`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `SLACK_WEBHOOK_URL`, `OPENAI_API_KEY`, and `REPLY_REDIRECT_TO` filled in.
- Docker Desktop is running, and n8n is at http://localhost:5679.
- If any value is missing, only build the offline parts (mock mode, `--dry-run`, unit tests) and note in VERIFICATION.md what went untested.

## 1. Hard rules
1. **No package installs.** Python uses only the standard library (`urllib`, `json`, `unittest`, `smtplib`, `email`, `argparse`, `hashlib`). Node uses only built-in modules (`node:test`, `node:assert`, `node:fs`, `node:path`, `node:crypto`). Node v24.
2. **Secrets** are never printed, logged, or written to any file that goes into git. Tokens, keys, and passwords in error messages are replaced with `***`.
3. **No paid resources.** HubSpot stays on Free with no trial, and Slack stays on Free.
4. **No `git push`** and no GitHub repo creation; the user decides both. Local commits are fine.
5. **OpenAI usage:** development always runs with `LLM_MODE=mock`. Real calls happen only in the §9.3 E2E run, at most 2 full rounds.
6. **HubSpot write scope:** only touch contacts whose email ends in `@example.com` or `@example.org` (all fixtures use these two reserved domains). The reset script deletes only those.
7. **Outgoing email:** with `DEMO_MODE=1`, every approved reply goes to `REPLY_REDIRECT_TO` with `[demo → <original recipient>]` prefixed to the subject. Nothing is ever sent to a fixture address.

## 2. File layout (final)
```
demo/
  README.md  docker-compose.yml  .env.example  .gitignore
  scripts/
    envfile.py            # reads .env (copied from UAT)
    hubspot_client.py     # shared HubSpot REST: get by email, search by phone, create, patch, note, limits
    hubspot_setup.py      # checks the custom property limit → creates the property group and 6 properties (skips existing ones)
    n8n_setup.py          # imports credentials and workflows, then publishes them (same approach as UAT)
    demo_common.py        # shared fixtures, paths, safe_id
    send_fixtures.py      # sends form POSTs or SMTP test emails from the fixtures; --only T1,T2; --dry-run
    reset_demo.py         # deletes @example.com/.org contacts; --dry-run on by default
    collect_results.py    # reads output/run-log/ + HubSpot → output/results.md (numbers computed by code)
  tests_scripts/          # unittest for the scripts above (mocked HTTP)
  n8n-src/
    code/*.js  code/*.test.js   # pure-function logic for the Code nodes
    build-workflows.mjs         # generates workflows/*.json
  prompts/  qualify.md  draft-reply.md
  sample-data/
    fixtures.json         # 8 fictional inquiries T1–T8 with expected results
    mock-llm/             # LLM responses for mock mode (named by fixture id)
  workflows/              # generated (committed)
  output/                 # run output (gitignored): run-log/*.json, review/*.html, results.md
  docs/  SPEC.md  SETUP.md  OPERATIONS.md  VERIFICATION.md  screenshots/
```

## 3. Shared data formats
### 3.1 Inquiry (both sources convert to this first)
```json
{
  "source": "form | email",
  "source_id": "form:<submission_id> | email:<Message-ID>",
  "received_at": "ISO-8601",
  "name": "string|null", "email": "lowercased|null", "phone": "E.164|null",
  "company": "string|null", "subject": "string|null", "message": "string",
  "demo_flags": {"break_llm": false}
}
```
- `normalize-form.js` reads the webhook body (`name`, `email`, `phone`, `company`, `message`, `submission_id`). Without a `submission_id`, it uses the first 16 characters of `sha256(email|message)`.
- `normalize-email.js` takes the sender from `Reply-To` first and falls back to `From` (many website form notifications are sent that way). `message` drops quoted replies and everything after the `-- ` signature separator. `phone` is pulled from the body with a regex.
- `normalize-phone.js` converts to E.164 with `+1` as the default country code. If it can't tell, it returns `null` instead of guessing.
- `validate-inquiry.js`: `email` and `phone` both null → `{valid:false, reason:"no_contact"}`; `message` under 5 characters after trimming → `empty_message`; malformed email → `bad_email`; with `DEMO_MODE=1`, an email outside `@example.com`/`@example.org` → `non_demo_address` (email-sourced ones are skipped without an alert, e.g. Google's welcome email).
- In the implementation, normalize and validate are combined in `n8n-src/code/normalize.js`. The other Code node logic is in `contact.js`, `llm.js`, `review.js`, and `records.js`.
- `demo_flags.break_llm` is true only when `DEMO_MODE=1` and the form request carries an `x-demo-break-llm: 1` header.

### 3.2 HubSpot contact custom properties (group: `lead_intake`)
| name | type | Description |
|---|---|---|
| `lead_tier` | enumeration | `qualified` / `needs_review` / `not_qualified` |
| `inquiry_topic` | enumeration | `regular_cleaning` / `deep_cleaning` / `quote_request` / `complaint` / `job_application` / `sales_pitch` / `other` |
| `inquiry_urgency` | enumeration | `high` / `normal` / `low` |
| `inquiry_source` | enumeration | `form` / `email` (the most recent source) |
| `ai_reason` | textarea | The AI's reason for the tier; must include a verbatim quote |
| `inquiry_source_ids` | textarea | `source_id`s already processed, separated by `;` (for idempotency) |

Contacts use 6 custom properties. (Tested 2026-10-28: on a free account `overallLimit` is 10, shared across all objects, with 0 in use. A service key with only the 4 contacts scopes can create a note and associate it with a contact, associationTypeId 202.) `hubspot_setup.py` calls `GET /crm/v3/limits/custom-properties` first and stops with a report if there isn't enough room left.

## 4. Dedup and idempotency (core logic: `n8n-src/code/match-contact.js` plus HTTP nodes)
1. With an email → `GET /crm/v3/objects/contacts/{email}?idProperty=email&properties=...` (a direct read, immediately consistent; don't use search, which has indexing lag).
2. Not found and there's a phone → `POST /crm/v3/objects/contacts/search` with `phone EQ <E.164>`. Search lags, so `send_fixtures.py` waits at least `--gap 8` seconds between items.
3. Found → check whether `inquiry_source_ids` already holds this `source_id`. If it does, **stop the whole item**: the run log records `duplicate_submission`, with no tiering, no note, and no draft.
4. Not found → create. If create returns 409 (the email already exists, possibly from a simultaneous submission), take the existing id from the error message and switch to the update path.
5. On update: tiers go through `merge-tier.js`, so a contact that is already `qualified` is never downgraded. Other fields take the latest value. `inquiry_source_ids` is appended to, never overwritten.
6. Every inquiry creates one note associated with the contact: `[<source>] <received_at>\n<subject>\n\n<message>\n\nAI: <tier> / <topic> / <urgency> — <reason>`.

## 5. LLM spec
### 5.1 `prompts/qualify.md` (Chat Completions, `response_format: json_schema`, strict)
Output: `{tier, topic, urgency, reason, quote}`, with the enums from §3.2. `quote` must be a passage that appears word for word in `message` (≤ 120 characters).
The tier rules live in the prompt, so the business owner can change them by editing this section:
- qualified: mentions office or commercial cleaning needs plus at least one of square footage, location, frequency, or timing.
- needs_review: might be a real request, but details are missing or the intent is unclear.
- not_qualified: job applications, sales pitches, spam, or residential cleaning (outside the service area).
- urgency high: needed within a week, says it's urgent, or gives a specific date soon.

`validate-qualification.js` checks the enums and that `quote` really appears in the original text (whitespace normalized before comparing). Anything that fails counts as an LLM failure.

### 5.2 `prompts/draft-reply.md`
Only used for `qualified` and `needs_review`. Output is `{subject, body}`: English, under 120 words, signed `Tidewell Commercial Cleaning team`. A needs_review reply asks for 1–2 missing details. It can't promise a price or a date.

### 5.3 Failure handling
HTTP nodes retry twice (2 seconds apart). If it still fails, or validation doesn't pass → `tier=needs_review`, `topic=other`, `urgency=normal`, `reason="AI classification failed: <error class>; needs a human look."`. The contact is still written to HubSpot (leads can't disappear), **no draft is created**, and a Slack alert goes out.
When `demo_flags.break_llm=true`, the requested model becomes `does-not-exist-model` so OpenAI returns a real 404.

### 5.4 Mock mode
`LLM_MODE=mock` skips OpenAI and reads `/files/sample-data/mock-llm/<fixture_id>.<qualify|draft>.json`. The fixture_id comes from matching `source_id` against `fixtures.json`; no match counts as an LLM failure. `break_llm` also takes the failure path in mock mode.

## 6. Human approval (adapted from UAT's `build-review-page.js` / `parse-decisions.js`)
- `build-review-page.js`: one page per inquiry. The top half is read-only: original text, source, AI tier and reason, and a link to the HubSpot contact. The bottom half is editable: `subject`, `body` (textarea), an Approve/Reject radio (neither selected by default), and a reviewer name (required). `<form method="POST" action="{{$execution.resumeUrl}}">`, with all content escaped. The page is saved to `output/review/<sanitized source_id>.html`, and its link goes in the Slack message sent before the Wait node.
- Slack notification (`format-review-alert.js`): `New lead: <name> (<tier>, <urgency>) — review the draft reply: <link>`.
- Wait node: On webhook call, POST, 72-hour timeout. On timeout it writes the note "review timed out, nothing sent".
- `parse-decisions.js`: a blank reviewer or no choice → return an error page and keep waiting (the same resumeUrl accepts a resubmission). Approve → send the **edited** subject/body over SMTP (following §1.7) → note `Reply drafted by AI, edited: <yes|no>, approved by <reviewer> on <date>, sent to <address>`. Reject → note `Reply drafted by AI, rejected by <reviewer> on <date>, nothing sent`.
- Response page: `Review received: approved and sent` / `Review received: rejected, nothing sent`.

## 7. n8n environment
- Image pinned to `2.44.7` (`N8N_VERSION` in `.env`), port 5679.
- Credentials (imported by `n8n_setup.py` with fixed ids): `hubspotTokenCred01` (httpHeaderAuth, `Authorization: Bearer <service key>`; free accounts can't create a legacy private app, so this uses a Service key, which doesn't support HubSpot webhooks; this demo doesn't need them), `openAiCred01` (httpHeaderAuth), `gmailImapCred01` (imap, `imap.gmail.com:993` SSL), `gmailSmtpCred01` (smtp, `smtp.gmail.com:465` SSL).
- Code nodes read files only from `/files/...`.

## 8. Workflows (generated by `n8n-src/build-workflows.mjs`)
### WF1 `wfFormIntake`
Webhook `POST /webhook/inquiry` → normalize-form → validate. Invalid → return `400 {error, reason}` → Slack alert → run log `rejected_input`. Valid → return `202 {accepted:true, source_id}` → Execute Workflow `wfProcessInquiry` (without waiting for it to finish).
### WF2 `wfEmailIntake`
Email Trigger (IMAP), INBOX, unread only, marked read after processing → normalize-email → validate → invalid ones get a Slack alert and a run log entry; valid ones go to Execute Workflow `wfProcessInquiry`.
Mail sent from `GMAIL_ADDRESS` itself with no Reply-To is skipped, so the workflow doesn't treat its own replies as inquiries.
### WF3 `wfProcessInquiry` (core)
match contact (§4) → stop if duplicate → qualify (§5) → create/update contact + note → if `lead_tier != not_qualified` and the LLM didn't fail → draft reply → build review page + Slack → Wait → parse decisions → send or don't send + note → write run log.
Each step's result accumulates in a `runLog` object, which is written at the end to `output/run-log/<timestamp>-<sanitized source_id>.json`: `{source_id, fixture_id, contact_id, action: created|updated|duplicate_submission, tier, llm_ok, draft: none|pending|approved|rejected|timeout, sent_to, alerts[]}`.
### WF5 `wfReviewPage`
`GET /webhook/review?id=<safe id>` → reads and returns `output/review/<id>.html`; pages that were already reviewed get a notice at the top. The review link in Slack points here.

### WF4 `wfErrorHandler`
Error Trigger → `format-alert.js` (copied from UAT) → Slack. WF1–WF3 all set errorWorkflow to WF4.

## 9. Verification (results go in `docs/VERIFICATION.md`)
### 9.1 Offline
- O1 `node --test "n8n-src/code/*.test.js" "n8n-src/*.test.mjs"` all pass (on Windows you need the glob; passing a folder fails)
- O2 `python -m unittest discover tests_scripts` all pass
- O3 `node n8n-src/build-workflows.mjs` produces 5 JSON files, and a second run produces identical content
- O4 `send_fixtures.py --dry-run` prints the 8 requests or emails it would send, with no secrets

### 9.2 Local n8n (mock LLM, real HubSpot/Gmail/Slack)
- N1 `hubspot_setup.py` creates the 6 properties, and a second run skips all of them
- N2 after `reset_demo.py --apply`, there are 0 `@example.*` contacts
- N3 one round of T1–T8 from `fixtures.json` → `collect_results.py` reports 8/8 PASS
- N4 resend the whole batch (same source_ids) → contact count and draft count don't change, all 8 are logged as `duplicate_submission`, and T6 is still a 400

### 9.3 E2E (real OpenAI)
- E1 after a reset, run T1–T8 with `LLM_MODE=live` → 8/8 PASS (if a tier comes out differently, record it as is; don't change the fixture's expected value to make it match)
- E2 screenshots in the HubSpot UI confirming the contact list (tier column), the 2 notes under T2's contact, and T8's reviewer notes
- E3 the Gmail Sent folder holds only the approved emails, with `[demo → ...]` in the subject

### 9.4 Expected results for T1–T8 (the `expect` field in `fixtures.json`; `collect_results.py` decides PASS from it)
| # | Sent as | Expected |
|---|---|---|
| T1 | form: weekly office cleaning, with square footage and location | created, qualified, draft pending |
| T2 | email: same person as T1 (Reply-To has the same email), asking about timing | updated (same contact_id), 2 notes |
| T3 | form: T1 resent unchanged (same submission_id) | duplicate_submission, no new draft |
| T4 | email: pitch for SEO services | created, not_qualified, draft none |
| T5 | form: "how much does it cost?" with no details | created, needs_review, draft pending |
| T6 | form: no email and no phone | HTTP 400, Slack alert, no new HubSpot contact |
| T7 | form + `x-demo-break-llm: 1` | created, needs_review, llm_ok=false, Slack alert, draft none |
| T8 | first submit a review form with no reviewer; then edit and approve the T1 draft; approve the T2 draft as is; reject the T5 draft | the incomplete review returns 422 and nothing is sent; T1 approved, edited=yes, and the sent text is the edited version; T2 approved, edited=no; T5 rejected, nothing sent; the demo inbox has exactly 2 emails |

## 10. Documentation
- `README.md` (English, public): what it does, an architecture diagram (text is fine), how to run it, what was tested, **what it doesn't do**, and a clear statement that this is a self-initiated portfolio project with fictional data.
- `docs/SETUP.md` (English): from a clean install through a full T1–T8 run.
- `docs/OPERATIONS.md` (English, for handover): how to change the tier rules (`prompts/qualify.md`), add enum values, add a new source (convert it to the §3.1 format and call WF3), read the run log, handle Slack alerts, and rotate tokens.
- `docs/VERIFICATION.md` (English): every check ID marked PASS / FAIL / NOT RUN with the reason.
