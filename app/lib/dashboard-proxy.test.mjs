import { test } from 'node:test';
import assert from 'node:assert/strict';
import { proxyDashboardRequest } from './dashboard-proxy.ts';

const origin = 'https://site.example';
const upstream = 'https://api.example';
const session = (authenticated = true, required = true) => Response.json({ authenticated, auth_required: required });
function request(path, init) { return new Request(`${origin}/api/dashboard/${path}`, init); }

test('peak accuracy uses protected read route and forwards the chosen window', async () => {
  const visited = [];
  const path = 'peak-accuracy';
  const response = await proxyDashboardRequest(request(path + '?model=ensemble&days=30&end_date=2026-08-30&source=production'), path, {
    upstream, fetcher: async url => {
      visited.push(String(url));
      return visited.length === 1 ? session() : Response.json({ hits: 17, evaluated_days: 28 });
    },
  });
  assert.equal(response.status, 200);
  assert.deepEqual(visited, ['https://api.example/session', 'https://api.example/peak-accuracy?model=ensemble&days=30&end_date=2026-08-30&source=production']);
  const denied = await proxyDashboardRequest(request(path), path, { upstream, fetcher: async url => {
    assert.equal(new URL(url).pathname, '/session');
    return session(false);
  } });
  assert.equal(denied.status, 401);
});

test('performance history forwards only the stored-series filters', async () => {
  const visited = [];
  const path = 'performance-history';
  const response = await proxyDashboardRequest(request(`${path}?model=gru&seed=44&days=30&source=production`), path, {
    upstream, fetcher: async url => {
      visited.push(String(url));
      return visited.length === 1 ? session() : Response.json({ origin: 'model_metrics_daily', series: [] });
    },
  });
  assert.equal(response.status, 200);
  assert.deepEqual(visited, ['https://api.example/session', 'https://api.example/performance-history?model=gru&seed=44&days=30&source=production']);
});

test('anonymous data requests never reach a data endpoint', async () => {
  let calls = 0;
  const response = await proxyDashboardRequest(request('health'), 'health', { upstream, fetcher: async (url) => { calls++; assert.equal(new URL(url).pathname, '/session'); return session(false); } });
  assert.equal(response.status, 401);
  assert.equal(calls, 1);
});

test('production refuses a backend with authentication disabled', async () => {
  for (const path of ['session', 'health', 'days']) {
    const response = await proxyDashboardRequest(request(path), path, { upstream, fetcher: async () => session(true, false) });
    assert.equal(response.status, 503);
  }
});

test('only explicitly local development accepts an unprotected backend', async () => {
  const response = await proxyDashboardRequest(request('session'), 'session', { upstream: 'http://127.0.0.1:8000', development: true, fetcher: async () => session(true, false) });
  assert.equal(response.status, 200);
  const prod = await proxyDashboardRequest(request('session'), 'session', { upstream: 'http://127.0.0.1:8000', fetcher: async () => { throw new Error('must not fetch'); } });
  assert.equal(prod.status, 503);
});

test('forwards only the session cookie and approved routes', async () => {
  const visited = [];
  const response = await proxyDashboardRequest(request('predictions/2026-08-31?source=production', { headers: { Cookie: 'platform_secret=private; pulso_session=signed.token; other=private' } }), 'predictions/2026-08-31', {
    upstream, fetcher: async (url, init) => {
      visited.push(String(url));
      assert.equal(init.headers.get('Cookie'), 'pulso_session=signed.token');
      assert.equal(init.cache, 'no-store');
      assert.equal(init.redirect, 'manual');
      return visited.length === 1 ? session() : Response.json({ hours: [] });
    },
  });
  assert.equal(response.status, 200);
  assert.deepEqual(visited, ['https://api.example/session', 'https://api.example/predictions/2026-08-31?source=production']);
  assert.match(response.headers.get('cache-control'), /no-store/);
});

test('rejects cross-origin and origin-less writes', async () => {
  for (const originValue of [undefined, 'https://evil.example']) {
    const response = await proxyDashboardRequest(request('login', { method: 'POST', headers: originValue ? { Origin: originValue } : {} }), 'login', { upstream, fetcher: async () => { throw new Error('must not fetch'); } });
    assert.equal(response.status, 403);
  }
});

test('login forwards HttpOnly cookie without exposing platform cookies', async () => {
  let count = 0;
  const response = await proxyDashboardRequest(request('login', { method: 'POST', headers: { Origin: origin, 'Content-Type': 'application/json' }, body: JSON.stringify({ password: 'test-only' }) }), 'login', { upstream, fetcher: async (url, init) => {
    count++;
    if (count === 1) return session(false);
    assert.equal(String(url), 'https://api.example/login');
    assert.equal(init.method, 'POST');
    assert.deepEqual(JSON.parse(init.body), { password: 'test-only' });
    return Response.json({ authenticated: true }, { headers: { 'Set-Cookie': 'pulso_session=signed.token; Secure; HttpOnly; SameSite=strict; Path=/' } });
  } });
  assert.equal(response.status, 200);
  assert.match(response.headers.get('set-cookie'), /HttpOnly/);
  assert.doesNotMatch(await response.text(), /test-only|signed.token/);
});

test('rejects path traversal, absolute destinations and unsupported query keys', async () => {
  for (const path of ['../health', 'https://evil.example', 'session/extra', 'docs']) {
    assert.equal((await proxyDashboardRequest(request('health'), path, { upstream })).status, 404);
  }
  assert.equal((await proxyDashboardRequest(request('health?url=https://evil.example'), 'health', { upstream })).status, 400);
});

test('rejects oversized login body', async () => {
  const response = await proxyDashboardRequest(request('login', { method: 'POST', headers: { Origin: origin, 'Content-Type': 'application/json' }, body: 'x'.repeat(2049) }), 'login', { upstream, fetcher: async () => session(false) });
  assert.equal(response.status, 413);
});

test('upstream errors do not expose internal response details', async () => {
  let count = 0;
  const response = await proxyDashboardRequest(request('health'), 'health', { upstream, fetcher: async () => ++count === 1 ? session() : new Response('private database details', { status: 500 }) });
  assert.equal(response.status, 502);
  assert.doesNotMatch(await response.text(), /database details/);
});

test('does not follow upstream redirects carrying session cookies', async () => {
  let calls = 0;
  const response = await proxyDashboardRequest(request('health'), 'health', { upstream, fetcher: async (url, init) => {
    assert.equal(init.redirect, 'manual');
    return ++calls === 1 ? session() : new Response(null, { status: 302, headers: { Location: 'https://evil.example' } });
  } });
  assert.equal(response.status, 502);
  assert.equal(calls, 2);
  assert.equal(response.headers.get('location'), null);
});

test('legacy session endpoint is accepted only on local development', async () => {
  const fetcher = async () => new Response(null, { status: 404 });
  const response = await proxyDashboardRequest(request('session'), 'session', { upstream: 'http://127.0.0.1:8000', development: true, fetcher });
  assert.equal(response.status, 200);
  assert.equal((await response.json()).authenticated, true);
  const remote = await proxyDashboardRequest(request('session'), 'session', { upstream, development: true, fetcher });
  assert.equal(remote.status, 503);
});
