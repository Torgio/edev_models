import test from 'node:test';
import assert from 'node:assert/strict';
import { proxyBatteryStudy } from './battery-study-proxy.ts';

const options = { upstream: 'https://auth.example.test', studyUpstream: 'https://auth.example.test/api/bat-test', development: false, enabled: true, allowedRuns: '' };
const upload = () => {
  const body = new FormData();
  body.set('consumo', new Blob(['fecha,hora,consumo_kwh\n2027-01-01,1,1']), 'consumo.csv');
  return new Request('https://public.test/api/battery-study/curvas', { method: 'POST', headers: { Origin: 'https://public.test', Cookie: 'pulso_session=signed.token' }, body });
};
test('public uploads preserve isolated prefix and verify authentication', async () => {
  for (const authenticated of [false, true]) {
    let uploads = 0;
    const response = await proxyBatteryStudy(upload(), 'curvas', { ...options, fetcher: async (url, init) => {
      if (url.pathname === '/session') return Response.json({ authenticated, auth_required: true });
      uploads++;
      assert.equal(url.pathname, '/api/bat-test/curvas');
      assert.equal(init.headers.get('Cookie'), 'pulso_session=signed.token');
      return Response.json([{ ok: true }]);
    } });
    assert.equal(response.status, authenticated ? 200 : 401);
    assert.equal(uploads, authenticated ? 1 : 0);
  }
});
test('public mode cannot fall back to production or an insecure upstream', async () => {
  for (const studyUpstream of ['', 'https://auth.example.test/api/bat', 'http://127.0.0.1:8011']) {
    assert.equal((await proxyBatteryStudy(upload(), 'curvas', { ...options, studyUpstream })).status, 503);
  }
});
