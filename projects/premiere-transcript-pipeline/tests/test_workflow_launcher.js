const assert = require('assert');
const fs = require('fs').promises;
const os = require('os');
const path = require('path');
const { assertLoopbackUrl, runnerRequest } = require('../uxp/workflow-launcher');

const id = '1234567890abcdef1234567890abcdef';
const workspace = '/tmp/exact-workspace';

async function fixture() {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'workflow-launcher-'));
  await Promise.all(['launcher-inbox', 'launcher-outbox'].map(name => fs.mkdir(path.join(root, name))));
  return root;
}

async function test() {
  assert.strictEqual(assertLoopbackUrl('http://127.0.0.1:8893/'), 'http://127.0.0.1:8893/');
  assert.strictEqual(assertLoopbackUrl('http://127.0.0.1:8877/setup'), 'http://127.0.0.1:8877/setup');
  for (const url of ['https://127.0.0.1:8893/', 'http://localhost:8893/',
    'http://127.0.0.1.evil.test:8893/', 'http://user@127.0.0.1:8893/',
    'http://127.0.0.1:8893/other', 'http://127.0.0.1:8893/?x=1'])
    assert.throws(() => assertLoopbackUrl(url));
  const root = await fixture();
  try {
    const fakeFs = { ...fs, async rename(from, to) {
      await fs.rename(from, to);
      const request = JSON.parse(await fs.readFile(to, 'utf8'));
      assert.strictEqual(request.operation, 'status');
      await fs.writeFile(path.join(root, 'launcher-outbox', `${id}.json`), JSON.stringify({
        nonce: id, ok: true, result: { state: 'ready', workspace,
          url: 'http://127.0.0.1:8893/', serverRunning: true },
      }));
    } };
    const result = await runnerRequest(fakeFs, root, 'status', { workspace }, { nonce: () => id });
    assert.strictEqual(result.state, 'ready');
    await assert.rejects(fs.access(path.join(root, 'launcher-inbox', `${id}.json`)));
    await assert.rejects(fs.access(path.join(root, 'launcher-outbox', `${id}.json`)));

    await assert.rejects(runnerRequest(fs, root, 'start_or_resume', { workspace }, { nonce: () => id }), /source clip/);
    await assert.rejects(runnerRequest(fs, root, 'status', { workspace }, {
      nonce: () => id, timeoutMs: 2, delay: async () => {},
    }), /timed out/);
    await assert.rejects(fs.access(path.join(root, 'launcher-inbox', `${id}.json`)));

    const mismatchFs = { ...fakeFs, async rename(from, to) {
      await fs.rename(from, to);
      await fs.writeFile(path.join(root, 'launcher-outbox', `${id}.json`), JSON.stringify({
        nonce: id, ok: true, result: { state: 'ready', workspace: '/tmp/other',
          url: 'http://127.0.0.1:8893/', serverRunning: true },
      }));
    } };
    await assert.rejects(runnerRequest(mismatchFs, root, 'status', { workspace }, { nonce: () => id }), /workspace mismatch/);
  } finally { await fs.rm(root, { recursive: true, force: true }); }
  console.log('workflow launcher tests passed');
}

test().catch(error => { console.error(error); process.exitCode = 1; });
