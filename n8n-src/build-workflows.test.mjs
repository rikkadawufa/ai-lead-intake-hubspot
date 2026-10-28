import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { buildWorkflows } from './build-workflows.mjs';

const workflows = buildWorkflows(mkdtempSync(join(tmpdir(), 'wf-')));

test('builds the five workflows and every connection points at a real node', () => {
  assert.deepEqual(workflows.map((item) => item.id), ['wfFormIntake', 'wfEmailIntake', 'wfProcessInquiry', 'wfReviewPage', 'wfErrorHandler']);
  for (const workflow of workflows) {
    const names = new Set(workflow.nodes.map((node) => node.name));
    assert.equal(names.size, workflow.nodes.length, `${workflow.id} has duplicate node names`);
    for (const [from, value] of Object.entries(workflow.connections)) {
      assert.ok(names.has(from), `${workflow.id}: unknown source ${from}`);
      for (const branch of value.main) for (const target of branch) assert.ok(names.has(target.node), `${workflow.id}: unknown target ${target.node}`);
    }
  }
});

test('output is deterministic', () => {
  const again = buildWorkflows(mkdtempSync(join(tmpdir(), 'wf-')));
  assert.equal(JSON.stringify(again), JSON.stringify(workflows));
});

test('code nodes never use $json in run-once-for-all-items mode', () => {
  for (const workflow of workflows) {
    for (const node of workflow.nodes.filter((item) => item.type === 'n8n-nodes-base.code')) {
      assert.ok(!/(^|[^.\w])\$json\b/.test(node.parameters.jsCode), `${workflow.id} / ${node.name} uses $json`);
    }
  }
});

test('no secret values are baked into the workflows', () => {
  const text = JSON.stringify(workflows);
  assert.ok(!/sk-[A-Za-z0-9]{10,}|pat-na|hooks\.slack\.com\/services/.test(text));
});

test('only the reply node sends email, and it uses the redirect-aware fields', () => {
  const process = workflows.find((item) => item.id === 'wfProcessInquiry');
  const senders = process.nodes.filter((node) => node.type === 'n8n-nodes-base.emailSend');
  assert.equal(senders.length, 1);
  assert.equal(senders[0].parameters.toEmail, '={{ $json.email.to }}');
  assert.equal(process.connections['Prepare email'].main[0][0].node, 'Send reply');
});
