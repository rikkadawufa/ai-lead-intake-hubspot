// HubSpot notes, Slack texts and the run log; every number here comes from code, not the AI.

function escapeNoteHtml(value) {
  return String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

const paragraph = (text) => `<p>${escapeNoteHtml(text).replace(/\n/g, '<br>')}</p>`;

function inquiryNoteHtml(inquiry, qualification) {
  return [
    paragraph(`[${inquiry.source}] ${inquiry.received_at}`),
    inquiry.subject ? paragraph(inquiry.subject) : '',
    paragraph(inquiry.message),
    paragraph(`AI: ${qualification.tier} / ${qualification.topic} / ${qualification.urgency} — ${qualification.reason}`),
  ].join('');
}

function decisionNoteHtml(result, { date, sentTo }) {
  if (result.timedOut) return paragraph('Reply drafted by AI; review timed out, nothing sent.');
  if (result.decision === 'approve') return paragraph(`Reply drafted by AI, edited: ${result.edited ? 'yes' : 'no'}, approved by ${result.reviewer} on ${date}, sent to ${sentTo}.\n\nSubject: ${result.subject}\n\n${result.body}`);
  return paragraph(`Reply drafted by AI, rejected by ${result.reviewer} on ${date}, nothing sent.`);
}

function noteRequest(contactId, html, timestamp = new Date().toISOString()) {
  return {
    properties: { hs_timestamp: timestamp, hs_note_body: html },
    associations: [{ to: { id: String(contactId) }, types: [{ associationCategory: 'HUBSPOT_DEFINED', associationTypeId: 202 }] }],
  };
}

function contactUrl(portalId, contactId) {
  return portalId ? `https://app.hubspot.com/contacts/${portalId}/record/0-1/${contactId}` : `https://app.hubspot.com/contacts/record/0-1/${contactId}`;
}

function maskSecrets(value) {
  return String(value ?? '')
    .replace(/Bearer\s+[^\s"']+/gi, 'Bearer ***')
    .replace(/\bsk-[A-Za-z0-9_-]+/g, '***')
    .replace(/\bpat-[A-Za-z0-9-]+/g, '***')
    .replace(/https:\/\/hooks\.slack\.com\/[^\s"']+/g, 'https://hooks.slack.com/***');
}

function formatAlert(payload = {}, baseUrl = 'http://localhost:5679') {
  const workflow = payload.workflow || {};
  const execution = payload.execution || {};
  const message = maskSecrets((execution.error && execution.error.message) || 'Unknown error');
  const url = `${baseUrl}/workflow/${workflow.id || ''}/executions/${execution.id || ''}`;
  return { text: `:rotating_light: Lead intake workflow error\nWorkflow: ${workflow.name || workflow.id || 'unknown'}\nNode: ${execution.lastNodeExecuted || 'unknown'}\nError: ${message}\n${url}`, url };
}

function rejectedInputAlert(inquiry, reason) {
  const who = inquiry.email || inquiry.phone || inquiry.name || 'unknown sender';
  return `:warning: Inquiry not accepted (${reason}) from ${who} via ${inquiry.source}. Nothing was written to HubSpot; please follow up by hand if needed.`;
}

function llmFailureAlert(inquiry, qualification, link) {
  return `:warning: AI classification failed for ${inquiry.email || inquiry.phone} (${qualification.llm_error}). The lead was saved as Needs review with no draft reply: ${link}`;
}

function reviewAlert(inquiry, qualification, reviewUrl) {
  return `:incoming_envelope: New lead: ${inquiry.name || inquiry.email} (${qualification.tier}, ${qualification.urgency} urgency) — review the draft reply: ${reviewUrl}`;
}

function buildRunLog({ inquiry, fixtureId = null, contactId = null, action, qualification = null, draft = 'none', sentTo = null, reviewer = null, edited = null, alerts = [], executionId = null }) {
  return {
    ts: new Date().toISOString(),
    execution_id: executionId,
    source_id: inquiry.source_id,
    source: inquiry.source,
    fixture_id: fixtureId,
    email: inquiry.email,
    contact_id: contactId,
    action,
    tier: qualification ? qualification.tier : null,
    llm_ok: qualification ? qualification.llm_ok : null,
    llm_error: qualification ? qualification.llm_error : null,
    draft,
    reviewer,
    edited,
    sent_to: sentTo,
    alerts,
  };
}

if (typeof module !== 'undefined') module.exports = { inquiryNoteHtml, decisionNoteHtml, noteRequest, contactUrl, maskSecrets, formatAlert, rejectedInputAlert, llmFailureAlert, reviewAlert, buildRunLog };
