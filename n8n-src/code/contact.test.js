const test = require('node:test');
const assert = require('node:assert/strict');
const { readLookup, checkDuplicate, mergeTier, buildContactProperties, parseConflictId } = require('./contact');

const inquiry = { source: 'email', source_id: 'email:<t2@example.com>', email: 'dana@example.com', phone: '+14155550142', name: 'Dana Whitfield', company: null };
const qualification = { tier: 'needs_review', topic: 'regular_cleaning', urgency: 'high', reason: 'Asks about start date.' };

test('lookup reads direct GET and search responses', () => {
  assert.equal(readLookup({ statusCode: 404, body: {} }), null);
  assert.equal(readLookup({ statusCode: 200, body: { id: '11' } }).id, '11');
  assert.equal(readLookup({ statusCode: 200, body: { results: [{ id: '12' }] } }).id, '12');
  assert.equal(readLookup({ statusCode: 200, body: { results: [] } }), null);
});

test('a source id already on the contact is a duplicate submission', () => {
  const contact = { id: '1', properties: { inquiry_source_ids: 'form:tw-1;email:<t2@example.com>' } };
  assert.equal(checkDuplicate(inquiry, contact), true);
  assert.equal(checkDuplicate({ ...inquiry, source_id: 'form:tw-9' }, contact), false);
  assert.equal(checkDuplicate(inquiry, null), false);
});

test('qualified leads are never downgraded', () => {
  assert.equal(mergeTier('qualified', 'needs_review'), 'qualified');
  assert.equal(mergeTier('needs_review', 'qualified'), 'qualified');
  assert.equal(mergeTier('not_qualified', 'needs_review'), 'needs_review');
  assert.equal(mergeTier(null, 'not_qualified'), 'not_qualified');
});

test('new contact gets identity fields and the first source id', () => {
  const properties = buildContactProperties(inquiry, qualification);
  assert.equal(properties.email, 'dana@example.com');
  assert.equal(properties.firstname, 'Dana');
  assert.equal(properties.lastname, 'Whitfield');
  assert.equal(properties.inquiry_source_ids, 'email:<t2@example.com>');
});

test('existing contact keeps its tier, identity and earlier source ids', () => {
  const existing = { id: '1', properties: { email: 'dana@example.com', firstname: 'Dana', phone: '+14155550000', lead_tier: 'qualified', inquiry_source_ids: 'form:tw-1' } };
  const properties = buildContactProperties(inquiry, qualification, existing);
  assert.equal(properties.lead_tier, 'qualified');
  assert.equal(properties.inquiry_source_ids, 'form:tw-1;email:<t2@example.com>');
  assert.equal(properties.inquiry_source, 'email');
  assert.equal('email' in properties, false);
  assert.equal('phone' in properties, false);
  assert.equal(properties.lastname, 'Whitfield');
});

test('409 conflict exposes the existing contact id', () => {
  assert.equal(parseConflictId({ statusCode: 409, body: { message: 'Contact already exists. Existing ID: 4021' } }), '4021');
  assert.equal(parseConflictId({ statusCode: 201, body: {} }), null);
});
