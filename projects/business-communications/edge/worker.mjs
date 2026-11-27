const utf8 = new TextEncoder();
const maxBytes = 512 * 1024;

function base64(bytes) {
  let binary = '';
  for (let offset = 0; offset < bytes.length; offset += 8192) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
  }
  return btoa(binary);
}

async function key(secret, usage) {
  return crypto.subtle.importKey('raw', utf8.encode(secret), {name: 'HMAC', hash: 'SHA-256'}, false, [usage]);
}

async function readBody(body) {
  if (!body) return new Uint8Array();
  const reader = body.getReader();
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > maxBytes) {
        await reader.cancel();
        return null;
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  return bytes;
}

const empty = status => new Response(null, {status});

export async function handle(request, env, forward = fetch) {
  const url = new URL(request.url);
  const configured = env.LINE_TEST_CHANNEL_SECRET && env.LINE_TEST_BOT_ID &&
    env.GAS_WORKER_SECRET && env.GAS_WORKER_SECRET.length >= 32 &&
    /^https:\/\/script\.google\.com\/macros\/s\/[A-Za-z0-9_-]+\/exec$/.test(env.GAS_WORKER_URL || '');
  if (request.method === 'GET' && url.pathname === '/healthz' && !url.search) return empty(configured ? 200 : 503);
  if (request.method !== 'POST' || url.pathname !== '/line/webhook' || url.search) return empty(404);
  if (!configured) return empty(503);
  const supplied = request.headers.get('x-line-signature') || '';
  if (!/^[A-Za-z0-9+/]{43}=$/.test(supplied)) return empty(401);
  if (Number(request.headers.get('content-length')) > maxBytes) return empty(413);
  const raw = await readBody(request.body);
  if (!raw) return empty(413);
  const signature = Uint8Array.from(atob(supplied), char => char.charCodeAt(0));
  if (!await crypto.subtle.verify('HMAC', await key(env.LINE_TEST_CHANNEL_SECRET, 'verify'), signature, raw)) return empty(401);

  let parsed;
  try {
    parsed = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(raw));
  } catch {
    return empty(400);
  }
  if (parsed?.destination !== env.LINE_TEST_BOT_ID) return empty(403);
  if (!Array.isArray(parsed.events)) return empty(400);
  if (!parsed.events.length) return empty(200);

  const timestamp = String(Math.floor(Date.now() / 1000));
  const payload = base64(raw);
  const proof = await crypto.subtle.sign('HMAC', await key(env.GAS_WORKER_SECRET, 'sign'),
    utf8.encode(timestamp + '.' + payload));
  try {
    const signal = AbortSignal.timeout(7000);
    let response = await forward(env.GAS_WORKER_URL, {method: 'POST', redirect: 'manual', signal,
      headers: {'content-type': 'application/json'}, body: JSON.stringify({timestamp, payload,
        signature: base64(new Uint8Array(proof))})});
    if (response.status === 302 || response.status === 303) {
      const location = new URL(response.headers.get('location'));
      if (location.origin !== 'https://script.googleusercontent.com') return empty(503);
      response = await forward(location.href, {redirect: 'error', signal});
    }
    if (!response.ok) return empty(503);
    const result = await response.json();
    return empty(result?.ok === true ? 200 : 503);
  } catch {
    return empty(503);
  }
}

export default {fetch: (request, env) => handle(request, env)};
