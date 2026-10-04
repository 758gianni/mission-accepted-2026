import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createTerraSignalApp } from './terrasignal-chat.js';

const evidence = {
	groundedAnswer: 'Region 2 is ranked #1 by the stored priority score (82.1), which is a heuristic, not confidence.',
	selectedCandidate: { id: 2, temporal_class: 'persistent', area_ha: 149, priority_score: 82.1 },
	metrics: { raw_bytes: 80300000000, derived_bytes: 493000000, reduction: 162.9 },
};

async function request(t, create, body = { question: 'Why is Region 2 ranked first?', evidence }) {
	const server = createTerraSignalApp({ chat: { completions: { create } } }, 'test/model').listen(0, '127.0.0.1');
	await new Promise((resolve) => server.once('listening', resolve));
	t.after(() => new Promise((resolve) => server.close(resolve)));
	return fetch(`http://127.0.0.1:${server.address().port}/api/terrasignal-chat`, {
		method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
	});
}

test('answers using only the supplied TerraSignal evidence through the configured model', async (t) => {
	const response = await request(t, async (params) => {
		assert.equal(params.model, 'test/model');
		assert.equal(params.messages[0].role, 'system');
		assert.match(params.messages[0].content, /Do not invent/);
		assert.match(params.messages[1].content, /Region 2 is ranked #1/);
		assert.match(params.messages[1].content, /Why is Region 2 ranked first/);
		assert.equal(params.temperature, 0.2);
		return { choices: [{ message: { content: 'The stored heuristic score is 82.1; it is not a confidence estimate.' } }] };
	});
	assert.equal(response.status, 200);
	assert.deepEqual(await response.json(), { reply: 'The stored heuristic score is 82.1; it is not a confidence estimate.' });
});

test('rejects malformed or oversized question/evidence before calling the model', async (t) => {
	const response = await request(t, () => assert.fail('Model must not be called'), { question: 'x'.repeat(4001), evidence });
	assert.equal(response.status, 400);
	assert.match((await response.json()).error, /Invalid chat request/);
});

test('does not expose upstream error details', async (t) => {
	const response = await request(t, async () => { throw Object.assign(new Error('secret token should stay private'), { status: 429 }); });
	assert.equal(response.status, 429);
	const body = await response.json();
	assert.match(body.error, /busy/i);
	assert.doesNotMatch(body.error, /secret token/);
});
