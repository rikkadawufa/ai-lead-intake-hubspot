# Verification log

Environment: local Docker n8n 2.44.7 (port 5679), HubSpot Free (service key), a demo Gmail account (IMAP/SMTP + app password), and Slack (the incoming webhook from the AI UAT demo workspace, channel `#uat-reports`). Run on 2026-10-29 (Taiwan time).

## 9.1 Offline
| ID | Command | Result |
|---|---|---|
| O1 | `node --test "n8n-src/code/*.test.js" "n8n-src/*.test.mjs"` | **PASS**: all 41 tests pass (normalize, contact, llm, review/records, fixtures, workflow builder) |
| O2 | `python -m unittest discover tests_scripts` | **PASS**: all 16 tests pass (HubSpot client retries and masking, property setup, result checks, reset deleting only demo domains) |
| O3 | `node n8n-src/build-workflows.mjs` run twice, then `diff` | **PASS**: 5 workflows generated, both runs identical |
| O4 | `python scripts/send_fixtures.py --dry-run` | **PASS**: lists the requests and emails for T1–T7, with no secrets |

## 9.2 Local n8n (mock LLM, real HubSpot/Gmail/Slack)
| ID | Result |
|---|---|
| N1 | **PASS**: `hubspot_setup.py` created 6 properties; a second run skipped all 6. The free account's limit is 10, and 6 are now in use |
| N2 | **PASS**: `reset_demo.py --apply` deleted 1 smoke-test contact and 2 notes; listing again shows 0 |
| N3 | **PASS 8/8**: `send_fixtures.py` → `send_fixtures.py --review` → `collect_results.py`. HubSpot has 4 demo contacts (Dana, Jordan, Sam, Riley) |
| N4 | **PASS 8/8**: `send_fixtures.py --replay` resent the same batch (including two emails with the same Message-ID) → all 6 were `duplicate_submission` with no new drafts, T6 was still a 400, and there were still 4 contacts |

N3 details (`output/results.md`):
- T1 created/qualified; the draft was edited on the review page and approved, and the edited version was sent
- T2 follow-up email → same contact_id, with 2 inquiry notes under the contact. The AI rated T2 itself needs_review, but the tier doesn't downgrade, so it stayed qualified. The draft was approved unedited
- T3 same form resent → duplicate_submission; T1 has only 1 draft
- T4 sales pitch → not_qualified, no draft
- T5 only asked about price → needs_review; the draft was rejected and nothing was sent
- T6 no email and no phone → HTTP 400, Slack alert, nothing written to HubSpot
- T7 deliberately wrong model name (404 simulated in mock mode) → needs_review, llm_ok=false, no draft, Slack alert
- T8 the first review form was submitted without a reviewer → 422 "Your name is required", and nothing was sent. Then T1 was edited and approved, T2 approved, and T5 rejected. The demo inbox got exactly 2 replies, with `[demo → dana.whitfield@example.com]` in the subject

## 9.3 E2E (real OpenAI)
| ID | Result |
|---|---|
| E1 | **PASS 8/8 (live round 2, 2026-10-29 00:35)**. The full sequence:<br>・Attempt 0: OpenAI was signed in to the wrong account, so every call returned 429 `credit_balance_exhausted`. No AI results, so it doesn't count as a round<br>・Round 1 (new key): **4/8**. T1 was rated needs_review with no draft, which also failed T2's final tier, T3, and T8<br>・Round 2 after the fix: **8/8** |
| E2 | **NOT RUN**: HubSpot UI screenshots not taken yet (left for the recording/portfolio step) |
| E3 | **PASS**: in round 2 the demo inbox got exactly 2 approved replies (T1 edited, T2 original), with `[demo → dana.whitfield@example.com]` in the subject. T5 was rejected and nothing was sent |

### Why live round 1 failed, and the fix
- The AI's call on T1 was actually right (qualified/regular_cleaning/normal), and its quote was word for word from the original text, but it was about 165 characters long. The prompt asks for 120 or fewer and the model ignored that. My check capped it at 160 and also logged "too long" as `quote_not_in_message`, a misleading error name.
- Fix (`n8n-src/code/llm.js`): the "must appear word for word in the original" check stays strict. A verbatim quote that's too long is now cut at a word boundary to 160 characters or fewer, ends with "…", and is accepted. I added 2 unit tests, one of which uses the real round-1 T1 response.
- Round 1 logs and results are in `output/archive-live-round-1/`, and round 2 is in `output/archive-live-round-2/`.

### A bug in the checker itself (found during live round 1)
- `collect_results.py` found reply emails with IMAP `SINCE`, but `SINCE` only works to the day, so it also counted emails from the mock round earlier that day. Round 1 briefly showed a false "edited T1 content = 1" because of it. It now uses the Date header to count only emails after the round started; recomputing round 1 gives the correct 0.

### How the real AI rated each fixture (round 2)
| # | AI rating | vs. expected |
|---|---|---|
| T1 | qualified/regular_cleaning/normal | Match |
| T2 | needs_review ("only asks to schedule a time, no cleaning details"); the contact's tier stays qualified under the no-downgrade rule | Match (final tier) |
| T4 | not_qualified/sales_pitch/low | Match |
| T5 | needs_review/quote_request/normal | Match |
| T7 | OpenAI really returned 404 → needs_review, alert | Match |

### Limits the live run exposed
- **The draft can't see the contact's earlier inquiries.** T2's AI draft asked Dana how big the office is and how often it needs cleaning, but she already said so in the T1 form. It was sent because the fixture says "approve without editing"; in practice a reviewer would fix it. Possible improvement: include the contact's earlier inquiry notes when drafting.
- **`ai_reason` follows the latest email, while tier keeps the higher rating.** Dana's tier is qualified, but `ai_reason` holds T2's "no cleaning details". Each field works as designed, but side by side they look a little contradictory.
- Switched back to `LLM_MODE=mock` after the run. HubSpot still has the 4 demo contacts from round 2 (useful for screenshots).

E1 "attempt 0" also confirmed what happens on real API errors (logs in `output/archive-live-attempt-1/`):
- T1, T2, T4, T5: OpenAI returned real 429s (n8n gave up after 3 retries) → all still written to HubSpot with tier=needs_review, llm_ok=false, no draft, and 1 Slack alert each. No lead was lost.
- T7: a nonexistent model name → OpenAI returned a real **404** (the model check runs before the credit check) → also moved to needs_review with an alert. So T7 has been verified with a real API error, not just mock.
- T3 was still flagged as a duplicate and T6 was still a 400; neither goes through the AI.
- Switched back to `LLM_MODE=mock` after the run. HubSpot kept this round's 4 demo contacts; a reset before rerunning clears them.

## Problems found and fixed during development
1. **HubSpot returns 404 with an empty body when a contact isn't found.** An n8n HTTP node set to JSON throws on that (the error workflow did send the right Slack alert). Lookup nodes now use `responseFormat: autodetect`.
2. **The IMAP trigger picked up Google's welcome emails to the new account.** In demo mode, any sender outside `@example.com`/`@example.org` is skipped, and the form returns `non_demo_address`, so real addresses never reach HubSpot. Those welcome emails were marked read as a result.
3. **Fixture domain:** T4 originally used the subdomain `seo-growth.example.org`, which reset would have missed. A unit test caught it, and it's now `@example.org`.
4. **A bug in the result check:** T8's "the sent text is the edited version" failed the first time because the T1 and T2 replies both had Dana's email in the subject, so the check picked the wrong email. The workflow was fine; the check now requires exactly one email containing the edited passage.

## Differences from SPEC (SPEC updated to match)
- One extra workflow, `wfReviewPage` (`GET /webhook/review?id=…`), so the review link in Slack opens directly in a browser. 5 workflows in total.
- T8 checks one more thing: T2's draft is approved unedited (edited=no), so the demo inbox should get 2 replies.
- In 2.44.7 the Wait node's resume URL carries `?signature=`; the review page uses `$execution.resumeUrl` as is and it works.
- n8n's webhook HTML responses have a CSP sandbox, but it includes `allow-forms`, so the review form submits fine without turning off the sandbox.

## Open decisions
- None.
