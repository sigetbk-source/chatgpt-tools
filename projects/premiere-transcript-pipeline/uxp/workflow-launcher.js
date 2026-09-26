// A narrow file-IPC client for the trusted local workflow runner.
const OPERATIONS = new Set(['status', 'start_or_resume']);

function assertLoopbackUrl(value) {
  if (typeof value !== 'string') throw Error('Review URL missing');
  const parsed = new URL(value);
  if (parsed.protocol !== 'http:' || parsed.hostname !== '127.0.0.1' ||
      parsed.username || parsed.password || !parsed.port ||
      !['/', '/setup'].includes(parsed.pathname) || parsed.search || parsed.hash)
    throw Error('Review URL is not a local workflow URL');
  return parsed.href;
}

function makeNonce() {
  const bytes = new Uint8Array(16);
  if (!globalThis.crypto || typeof globalThis.crypto.getRandomValues !== 'function')
    throw Error('Secure random source unavailable');
  globalThis.crypto.getRandomValues(bytes);
  return Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
}

async function runnerRequest(fs, root, operation, payload, {
  now = Date.now, nonce = makeNonce, delay = ms => new Promise(resolve => setTimeout(resolve, ms)),
  timeoutMs = 5000,
} = {}) {
  if (!OPERATIONS.has(operation)) throw Error('Unsupported runner operation');
  if (!root || typeof payload?.workspace !== 'string' || !payload.workspace.startsWith('/'))
    throw Error('Select an exact workspace folder');
  if (operation === 'start_or_resume' &&
      (!['multilingual', 'ja-jp'].includes(payload.mode) ||
       !payload.target || typeof payload.target.mediaPath !== 'string'))
    throw Error('Select exactly one Premiere source clip');
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 30000)
    throw Error('Invalid runner timeout');
  const id = nonce();
  if (!/^[a-f0-9]{32}$/.test(id)) throw Error('Invalid nonce');
  const until = now() + timeoutMs;
  const request = { version: 1, nonce: id, operation, deadlineMs: until, payload };
  const pending = `${root}/launcher-inbox/${id}.tmp`;
  const ready = `${root}/launcher-inbox/${id}.json`;
  const responsePath = `${root}/launcher-outbox/${id}.json`;
  await fs.writeFile(pending, JSON.stringify(request), { encoding: 'utf-8', flag: 'wx' });
  await fs.rename(pending, ready);
  try {
    while (now() < until) {
      const files = await fs.readdir(`${root}/launcher-outbox`);
      if (files.includes(`${id}.json`)) {
        const response = JSON.parse(await fs.readFile(responsePath, 'utf-8'));
        if (response.nonce !== id) throw Error('Runner response nonce mismatch');
        if (!response.ok) throw Error(response.error || 'Runner request failed');
        if (!response.result || typeof response.result !== 'object') throw Error('Invalid runner response');
        const result = response.result;
        if (result.url) assertLoopbackUrl(result.url);
        if (result.workspace !== payload.workspace) throw Error('Runner workspace mismatch');
        try { await fs.unlink(responsePath); } catch (_) {}
        return result;
      }
      await delay(Math.min(100, Math.max(1, until - now())));
    }
    throw Error('Local runner response timed out');
  } finally {
    try { await fs.unlink(ready); } catch (_) {}
    try { await fs.unlink(pending); } catch (_) {}
  }
}

module.exports = { assertLoopbackUrl, runnerRequest, makeNonce };
