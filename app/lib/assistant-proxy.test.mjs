import { test } from 'node:test';
import assert from 'node:assert/strict';
import { proxyAssistantRequest } from './assistant-proxy.ts';

const origin = 'https://site.example';
const authUpstream = 'https://auth.example';
const assistantUpstream = 'https://assistant.example';
function request(body = { pregunta: '¿Cuál es el precio de mañana?' }, init = {}) {
  return new Request(`${origin}/api/asistente`, {
    method: 'POST',
    headers: { Origin: origin, 'Content-Type': 'application/json', Cookie: 'private=x; pulso_session=signed.token', ...init.headers },
    body: JSON.stringify(body),
    ...init,
  });
}

test('validates the Pulso session and forwards only the question and session cookie', async () => {
  const calls = [];
  const response = await proxyAssistantRequest(request(), { authUpstream, assistantUpstream, fetcher: async (url, init) => {
    calls.push(String(url));
    assert.equal(init.headers.get('Cookie'), 'pulso_session=signed.token');
    if (calls.length === 1) return Response.json({ authenticated: true, auth_required: true, username: 'magui' });
    assert.deepEqual(JSON.parse(init.body), { pregunta: '¿Cuál es el precio de mañana?' });
    return Response.json({ respuesta: 'Respuesta verificada.', imagenes_base64: [] });
  } });
  assert.equal(response.status, 200);
  assert.deepEqual(calls, ['https://auth.example/session', 'https://assistant.example/api/asistente']);
  assert.deepEqual(await response.json(), { respuesta: 'Respuesta verificada.', imagenes_base64: [] });
});

test('an unauthenticated request never reaches the assistant', async () => {
  let calls = 0;
  const response = await proxyAssistantRequest(request(), { authUpstream, assistantUpstream, fetcher: async () => {
    calls++;
    return Response.json({ authenticated: false, auth_required: true });
  } });
  assert.equal(response.status, 401);
  assert.equal(calls, 1);
});

test('rejects cross-origin, malformed and oversized requests before upstream access', async () => {
  const never = async () => { throw new Error('must not fetch'); };
  const crossOrigin = request(undefined, { headers: { Origin: 'https://evil.example', 'Content-Type': 'application/json' } });
  assert.equal((await proxyAssistantRequest(crossOrigin, { authUpstream, assistantUpstream, fetcher: never })).status, 403);
  assert.equal((await proxyAssistantRequest(request({ pregunta: '' }), { authUpstream, assistantUpstream, fetcher: never })).status, 400);
  const large = new Request(`${origin}/api/asistente`, { method: 'POST', headers: { Origin: origin, 'Content-Type': 'application/json' }, body: 'x'.repeat(4097) });
  assert.equal((await proxyAssistantRequest(large, { authUpstream, assistantUpstream, fetcher: never })).status, 413);
});

test('sanitizes upstream errors and validates the response contract', async () => {
  let calls = 0;
  const response = await proxyAssistantRequest(request(), { authUpstream, assistantUpstream, fetcher: async () => {
    calls++;
    return calls === 1
      ? Response.json({ authenticated: true, auth_required: true })
      : Response.json({ detail: 'private stack and SQL' }, { status: 500 });
  } });
  assert.equal(response.status, 502);
  assert.doesNotMatch(await response.text(), /private stack|SQL/);
});
