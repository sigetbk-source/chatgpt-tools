import assert from 'node:assert/strict';
import {createHmac} from 'node:crypto';
import worker, {handle} from './worker.mjs';

const env = {
  LINE_TEST_CHANNEL_SECRET: 'fake-line-channel-secret',
  LINE_TEST_BOT_ID: 'fake-bot',
  GAS_WORKER_SECRET: 'mock-worker-key'.repeat(3),
  GAS_WORKER_URL: 'https://script.google.com/macros/s/test/exec',
};
const signature = (key, payload) => createHmac('sha256', key).update(payload).digest('base64');
const raw = '{ "destination": "fake-bot", "events": [{"webhookEventId":"test-event","message":"日本語"}] }';
const makeRequest = (body, proof = signature(env.LINE_TEST_CHANNEL_SECRET, body)) => new Request(
  'https://test.example/line/webhook', {method: 'POST', headers: {'X-Line-Signature': proof}, body});

const calls = [];
const store = async (url, options) => {
  calls.push([url, options]);
  if (url === env.GAS_WORKER_URL) return new Response(null, {status: 302,
    headers: {location: 'https://script.googleusercontent.com/macros/echo?sample=1'}});
  return Response.json({ok: true});
};

assert.equal((await handle(makeRequest(raw, signature('wrong', raw)), env, store)).status, 401);
assert.equal((await handle(makeRequest(raw.replace('fake-bot', 'other')), env, store)).status, 403);
assert.equal((await handle(makeRequest('{'), env, store)).status, 400);
assert.equal((await handle(makeRequest('x'.repeat(513 * 1024)), env, store)).status, 413);
assert.equal((await worker.fetch(makeRequest('{"destination":"fake-bot","events":[]}'), env, {})).status, 200);
assert.equal(calls.length, 0);
assert.equal((await handle(makeRequest(raw), env, store)).status, 200);
assert.equal(calls.length, 2);
const posted = JSON.parse(calls[0][1].body);
assert.equal(Buffer.from(posted.payload, 'base64').toString(), raw);
assert.equal(posted.signature, signature(env.GAS_WORKER_SECRET, posted.timestamp + '.' + posted.payload));
assert.equal(calls[1][1].body, undefined);
assert.equal((await handle(makeRequest(raw), env, async () => Response.json({ok: false}))).status, 503);
assert.equal((await handle(makeRequest(raw), env,
  async () => new Response(null, {status: 302, headers: {location: 'https://other.example/'}}))).status, 503);
console.log('Edge relay tests passed');
