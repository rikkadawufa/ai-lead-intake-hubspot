# Setup

How to get from an empty machine to the T1 to T8 results. The commands are for PowerShell on Windows and work in bash with small changes.

## Requirements

- Docker Desktop
- Python 3.12 and Node.js 24 (standard library and built-ins only, nothing to install)
- A HubSpot account (Free is enough)
- A Gmail address used only for this demo, with 2-Step Verification on
- An OpenAI API key with credit (only needed for live mode; the mock mode is free)
- A Slack workspace where you can add an incoming webhook

## 1. Accounts and `.env`

Copy `.env.example` to `.env` and fill it in. Never commit `.env`; it's in `.gitignore`.

| Variable | Where it comes from |
|---|---|
| `HUBSPOT_TOKEN` | HubSpot → Development → Keys → **Service keys**. Scopes: `crm.objects.contacts.read`, `crm.objects.contacts.write`, `crm.schemas.contacts.read`, `crm.schemas.contacts.write`. Free accounts can't create legacy private apps; a service key works the same way here. These four scopes were enough to create notes on contacts. |
| `GMAIL_ADDRESS` | The demo inbox |
| `GMAIL_APP_PASSWORD` | https://myaccount.google.com/apppasswords (needs 2-Step Verification). Spaces are fine. |
| `SLACK_WEBHOOK_URL` | https://api.slack.com/apps → your app → Incoming Webhooks → add one for the alerts channel |
| `OPENAI_API_KEY` | https://platform.openai.com/api-keys |
| `OPENAI_MODEL` | `gpt-4.1-mini` was used for testing |
| `LLM_MODE` | `mock` (default) or `live` |
| `DEMO_MODE` | `1` keeps everything inside the demo: only `@example.com` / `@example.org` senders reach HubSpot, and approved replies go to `REPLY_REDIRECT_TO` |
| `REPLY_REDIRECT_TO` | Where approved replies go in demo mode, for example `yourdemo+replies@gmail.com` |
| `N8N_ENCRYPTION_KEY` | Any long random string. Keep it: changing it later makes n8n unable to read its stored credentials. |
| `N8N_VERSION` | `2.44.7` |
| `HUBSPOT_PORTAL_ID` | Filled in by `hubspot_setup.py` |

## 2. HubSpot properties

```powershell
python scripts/hubspot_setup.py --dry-run
python scripts/hubspot_setup.py
```

This checks the custom property limit first (a Free account has 10 in total, shared across objects), then creates the `lead_intake` group with six contact properties and writes your portal ID to `.env`. Running it again skips what already exists.

## 3. Start n8n and import the workflows

```powershell
docker compose up -d
node n8n-src/build-workflows.mjs
python scripts/n8n_setup.py
```

`build-workflows.mjs` generates the five workflow files in `workflows/` from `n8n-src/code/*.js` and the prompts. `n8n_setup.py` imports four credentials (HubSpot, OpenAI, Gmail IMAP, Gmail SMTP) through a temp file that is deleted right after, imports the workflows, publishes them and restarts n8n. Give the webhooks a few seconds to register after the restart.

The editor runs at http://localhost:5679. Create the local owner account there the first time if you want to look at the workflows; the import doesn't need it.

Port 5679 is used so this can run next to another n8n on 5678.

## 4. Run the tests

```powershell
python scripts/reset_demo.py            # dry run: lists what would be deleted
python scripts/reset_demo.py --apply    # deletes @example.com/.org contacts and their notes, clears output/
python scripts/send_fixtures.py --dry-run
python scripts/send_fixtures.py         # T1–T7, one every 8 seconds
python scripts/send_fixtures.py --review   # T8: the review decisions
python scripts/collect_results.py       # writes output/results.md
```

Wait for the drafts to appear before `--review`; in mock mode that takes a few seconds, in live mode up to about a minute. `send_fixtures.py --review` reports any review page that isn't there yet.

To check idempotency, send the same batch again and compare:

```powershell
python scripts/send_fixtures.py --replay
python scripts/collect_results.py --replay   # writes output/results-replay.md
```

Every inquiry should come back as `duplicate_submission`, and the number of demo contacts should stay the same.

## 5. Live mode

Run everything in mock mode first. To use OpenAI, override the mode for the container instead of editing `.env`, and switch back afterwards:

```powershell
$env:LLM_MODE = 'live'; docker compose up -d
docker compose exec -T n8n sh -c 'printf "%s\n" "$LLM_MODE"'
# reset, send, review, collect as above
$env:LLM_MODE = 'mock'; docker compose up -d
```

A full T1 to T8 run makes about ten small OpenAI calls (four classifications, three drafts, and T7's failing call with its retries).

## Offline checks

```powershell
node --test "n8n-src/code/*.test.js" "n8n-src/*.test.mjs"
python -m unittest discover tests_scripts
```

On Windows, pass the test files as a glob; giving `node --test` a folder fails.

## Troubleshooting

- **401/403 from HubSpot:** check the service key's four scopes, then run `python scripts/n8n_setup.py` again if you created a new key.
- **429 `insufficient_quota` from OpenAI:** the account behind the key has no credit. The leads are still saved as Needs review with an alert, so nothing is lost, but there's no classification until you add credit.
- **Webhook 404 right after a restart:** wait a few seconds and retry.
- **Review page says "Review not accepted":** the reviewer name is empty or no decision was picked. Go back, fix it and submit again; nothing was sent.
- **Credential decryption error:** `N8N_ENCRYPTION_KEY` changed. Put the original back.
- **An email isn't picked up:** the trigger only reads unread mail in INBOX. In demo mode, senders outside `example.com` / `example.org` are skipped on purpose.
