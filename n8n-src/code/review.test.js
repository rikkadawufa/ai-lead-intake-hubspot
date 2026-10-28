const test = require('node:test');
const assert = require('node:assert/strict');
const { buildReviewPage, parseDecision, outgoingEmail, safeId } = require('./review');
const { decisionNoteHtml, noteRequest, maskSecrets, formatAlert, buildRunLog } = require('./records');

const draft = { subject: 'Re: office cleaning', body: 'Hi Dana,\nThanks for reaching out.' };

test('review page escapes inquiry text and posts to the resume URL', () => {
  const page = buildReviewPage({
    inquiry: { name: '<script>x</script>', email: 'a@example.com', source: 'form', received_at: 'now', message: 'Hi & hello' },
    qualification: { tier: 'qualified', topic: 'regular_cleaning', urgency: 'normal', reason: 'r' },
    draft, contactUrl: 'https://app.hubspot.com/x', resumeUrl: 'http://localhost:5679/webhook-waiting/42',
  });
  assert.ok(!page.includes('<script>x</script>'));
  assert.ok(page.includes('&lt;script&gt;'));
  assert.ok(page.includes('action="http://localhost:5679/webhook-waiting/42"'));
  assert.ok(page.includes('name="reviewer"'));
});

test('missing reviewer or decision is not accepted', () => {
  const result = parseDecision({ subject: 's', body: 'b' }, draft);
  assert.deepEqual(result.errors, ['Your name is required', 'Choose Approve or Reject']);
});

test('approve with edits reports edited=true and keeps the edited text', () => {
  const result = parseDecision({ reviewer: 'Morgan (Ops)', decision: 'approve', subject: 'Re: office cleaning', body: 'Hi Dana,\r\nWe can walk the site Tuesday.' }, draft);
  assert.deepEqual(result.errors, []);
  assert.equal(result.edited, true);
  assert.equal(result.body, 'Hi Dana,\nWe can walk the site Tuesday.');
});

test('unchanged approve reports edited=false; no body means timeout', () => {
  assert.equal(parseDecision({ reviewer: 'M', decision: 'approve', subject: draft.subject, body: draft.body }, draft).edited, false);
  assert.equal(parseDecision(undefined, draft).timedOut, true);
});

test('demo mode always redirects the reply and refuses without a redirect inbox', () => {
  assert.deepEqual(outgoingEmail({ to: 'dana@example.com', subject: 'Hi', body: 'b', demoMode: true, redirectTo: 'leads+replies@gmail.com' }), { to: 'leads+replies@gmail.com', subject: '[demo → dana@example.com] Hi', body: 'b' });
  assert.throws(() => outgoingEmail({ to: 'dana@example.com', subject: 'Hi', body: 'b', demoMode: true, redirectTo: '' }));
});

test('decision notes name the reviewer', () => {
  assert.match(decisionNoteHtml({ decision: 'approve', reviewer: 'Morgan', edited: true, subject: 's', body: 'b' }, { date: '2026-10-29', sentTo: 'x@gmail.com' }), /edited: yes, approved by Morgan on 2026-10-29, sent to x@gmail.com/);
  assert.match(decisionNoteHtml({ decision: 'reject', reviewer: 'Morgan' }, { date: '2026-10-29' }), /rejected by Morgan .* nothing sent/);
  assert.match(decisionNoteHtml({ timedOut: true }, {}), /timed out, nothing sent/);
});

test('note request associates the note with the contact', () => {
  const request = noteRequest('77', '<p>x</p>', '2026-10-29T00:00:00Z');
  assert.equal(request.associations[0].to.id, '77');
  assert.equal(request.associations[0].types[0].associationTypeId, 202);
});

test('alerts mask tokens and webhook URLs', () => {
  assert.equal(maskSecrets('Bearer pat-na1-abc and https://hooks.slack.com/services/T/B/C'), 'Bearer *** and https://hooks.slack.com/***');
  assert.match(formatAlert({ workflow: { id: 'wf', name: 'Process inquiry' }, execution: { id: '9', lastNodeExecuted: 'Create contact', error: { message: 'sk-secret failed' } } }).text, /Error: \*\*\* failed/);
});

test('run log and safe ids', () => {
  assert.equal(safeId('email:<t2@example.com>'), 'email_t2_example.com');
  const log = buildRunLog({ inquiry: { source_id: 'form:tw-1', source: 'form', email: 'a@example.com' }, action: 'created', qualification: { tier: 'qualified', llm_ok: true, llm_error: null }, draft: 'approved' });
  assert.equal(log.action, 'created');
  assert.equal(log.tier, 'qualified');
});
