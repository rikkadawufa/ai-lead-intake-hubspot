# Operations guide

For the team running this day to day. Most changes are a text edit followed by two commands:

```powershell
node n8n-src/build-workflows.mjs
python scripts/n8n_setup.py --skip-credentials
```

The first rebuilds the workflow files, the second imports and publishes them. Don't edit the workflows in the n8n editor if you plan to rebuild, because the rebuild overwrites them. Make the change in the source files instead.

## Daily use

1. A new lead posts to Slack: `New lead: <name> (<tier>, <urgency> urgency) — review the draft reply: <link>`.
2. Open the link. The page shows the original message, the AI's tier and reason, and a link to the contact in HubSpot.
3. Edit the draft if needed, pick **Approve and send** or **Reject, send nothing**, type your name and submit.
4. The decision is saved as a note on the contact. Opening the link again shows "Already reviewed".

Some leads get no draft: Not qualified ones (pitches, job applications, home cleaning) and any the AI couldn't classify. They are still saved to HubSpot. Filter contacts by **Lead tier** to find them.

One known gap: the draft only sees the current message, so on a follow-up it may ask for details the person already gave in an earlier inquiry. Check drafts for repeat contacts against the contact's notes and edit them before approving.

## Changing the sorting rules

The rules are plain English in [../prompts/qualify.md](../prompts/qualify.md), under **System**. For example, to accept residential work, move "home cleaning" out of the not_qualified line and describe when it counts as qualified. Rebuild and re-import afterwards.

The reply style lives in [../prompts/draft-reply.md](../prompts/draft-reply.md) the same way.

Test a rule change in mock mode first, then do one live run (see SETUP §5). In mock mode the AI answers come from `sample-data/mock-llm/`, so mock runs only test the plumbing, not the new wording.

## Adding a topic or tier value

Values have to match in three places:

1. The JSON schema enum in `prompts/qualify.md`
2. The `TOPICS` / `TIERS` / `URGENCIES` list in `n8n-src/code/llm.js`
3. The HubSpot property options in `scripts/hubspot_setup.py`

`hubspot_setup.py` only creates missing properties; it doesn't add options to existing ones. Add the new option in HubSpot (Settings → Properties → the property → Options) or through the API, then rebuild and re-import. Run the unit tests before importing.

## Adding a new source

Any channel can feed the same pipeline. Turn the incoming message into the inquiry shape below, then call the **Lead intake: process inquiry** workflow with an *Execute Workflow* node, the way the form and email workflows do.

```json
{
  "source": "form",
  "source_id": "form:<unique id from the channel>",
  "received_at": "2026-10-29T09:00:00.000Z",
  "name": "Dana Whitfield",
  "email": "dana@example.com",
  "phone": "+15105550142",
  "company": "Harborview Dental Group",
  "subject": "Website contact form",
  "message": "The message text",
  "demo_flags": { "break_llm": false }
}
```

- `source_id` must be unique and stable for the same message. That's what stops duplicates, so use the channel's own message ID rather than a timestamp.
- At least one of `email` or `phone` is required. Use `normalize.js` (`toE164`, `validateInquiry`) so phone numbers match existing contacts.
- `inquiry_source` in HubSpot only allows `form` and `email`. Add the new source as an option there first.

## Reading the run log

Every inquiry writes one JSON file to `output/run-log/`:

| Field | Meaning |
|---|---|
| `action` | `created`, `updated`, `duplicate_submission` or `rejected_input` |
| `tier`, `llm_ok`, `llm_error` | The AI's result; `llm_ok: false` means the fallback was used |
| `draft` | `none`, `approved`, `rejected` or `timeout` |
| `reviewer`, `edited`, `sent_to` | Who decided, whether they changed the draft, where it went |
| `alerts` | Slack messages sent for this inquiry |

`errors.jsonl` in the same folder lists workflow failures caught by the error handler.

## Slack alerts

| Alert | What to do |
|---|---|
| `Inquiry not accepted (no_contact)` | The sender left no email or phone. Nothing was saved; follow up by hand if you can tell who it was. |
| `AI classification failed (...)` | The lead is in HubSpot as Needs review with no draft. Open it, decide the tier yourself and reply manually. `api_error_429` usually means the OpenAI account is out of credit. |
| `AI reply draft failed (...)` | The lead was classified, but there's no draft. Reply from HubSpot directly. |
| `Lead intake workflow error` | Something failed outside the expected paths. The alert links to the failed n8n execution. |

## Rotating keys

1. Create the new key (HubSpot service key, OpenAI key or Gmail app password) and put it in `.env`.
2. `python scripts/n8n_setup.py` re-imports the credentials with the new values.
3. Revoke the old key once a test inquiry goes through.

Keep `N8N_ENCRYPTION_KEY` unchanged through all of this.

## Leaving demo mode

`DEMO_MODE=1` blocks every sender outside `example.com` / `example.org` and sends approved replies to `REPLY_REDIRECT_TO`. Before setting `DEMO_MODE=0` for real use, put n8n on a host the team can reach, add access control in front of the review page, and run one live test with an address you own.
