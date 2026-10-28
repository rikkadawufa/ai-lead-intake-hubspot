// Contact matching, de-duplication and the property patch written to HubSpot (SPEC §4).

const TIER_RANK = { not_qualified: 0, needs_review: 1, qualified: 2 };

function readLookup(response) {
  // HTTP node output with fullResponse + neverError: { statusCode, body }.
  if (!response || response.statusCode !== 200) return null;
  const body = response.body || {};
  if (Array.isArray(body.results)) return body.results[0] || null;
  return body.id ? body : null;
}

function sourceIds(properties = {}) {
  return String(properties.inquiry_source_ids || '').split(';').map((value) => value.trim()).filter(Boolean);
}

function checkDuplicate(inquiry, contact) {
  return Boolean(contact) && sourceIds(contact.properties).includes(inquiry.source_id);
}

function mergeTier(existing, incoming) {
  if (!existing || !(existing in TIER_RANK)) return incoming;
  return existing === 'qualified' ? 'qualified' : incoming;
}

function splitName(name) {
  const parts = String(name || '').trim().split(/\s+/).filter(Boolean);
  return { firstname: parts[0] || '', lastname: parts.slice(1).join(' ') };
}

function buildContactProperties(inquiry, qualification, existing = null) {
  const current = (existing && existing.properties) || {};
  const ids = sourceIds(current);
  if (!ids.includes(inquiry.source_id)) ids.push(inquiry.source_id);
  const properties = {
    lead_tier: mergeTier(current.lead_tier, qualification.tier),
    inquiry_topic: qualification.topic,
    inquiry_urgency: qualification.urgency,
    inquiry_source: inquiry.source,
    ai_reason: qualification.reason,
    inquiry_source_ids: ids.join(';'),
  };
  if (inquiry.email && !current.email) properties.email = inquiry.email;
  if (inquiry.phone && !current.phone) properties.phone = inquiry.phone;
  if (inquiry.company && !current.company) properties.company = inquiry.company;
  const name = splitName(inquiry.name);
  if (name.firstname && !current.firstname) properties.firstname = name.firstname;
  if (name.lastname && !current.lastname) properties.lastname = name.lastname;
  return properties;
}

function parseConflictId(response) {
  if (!response || response.statusCode !== 409) return null;
  const message = (response.body && response.body.message) || '';
  const match = message.match(/Existing ID:\s*(\d+)/i);
  return match ? match[1] : null;
}

if (typeof module !== 'undefined') module.exports = { readLookup, checkDuplicate, mergeTier, buildContactProperties, parseConflictId, sourceIds };
