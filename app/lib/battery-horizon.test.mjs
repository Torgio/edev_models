import test from 'node:test';
import assert from 'node:assert/strict';
import { studyHorizonEnd } from './battery-horizon.ts';
import { proxyBatteryStudy } from './battery-study-proxy.ts';
import { DEFAULT_BATTERY } from './battery-draft.ts';

test('calendar horizons include leap years and stop at published coverage', () => {
  assert.equal(studyHorizonEnd('2027-01-01', 1, '2046-12-31'), '2027-12-31');
  assert.equal(studyHorizonEnd('2027-01-01', 20, '2046-12-31'), '2046-12-31');
  assert.equal(studyHorizonEnd('2028-02-29', 1, '2046-12-31'), '2029-02-27');
  assert.equal(studyHorizonEnd('2045-01-01', 5, '2046-12-31'), '2046-12-31');
});

test('study launch accepts six months and twenty years but rejects excessive horizons', async () => {
  for (const [dateTo, status] of [['2027-06-30', 200], ['2046-12-31', 200], ['2047-12-31', 400]]) {
    let launches = 0;
    const request = new Request('http://localhost:3000/api/battery-study/estudio', {
      method: 'POST', headers: { Origin: 'http://localhost:3000', 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...DEFAULT_BATTERY, dateFrom: '2027-01-01', dateTo,
        studyName: '  Fábrica  ', consumptionCode: 'FABRICA', generationIncluded: false, scenarios: 5, policy: 'libre' }),
    });
    const response = await proxyBatteryStudy(request, 'estudio', {
      upstream: 'https://auth.example.test', studyUpstream: 'http://127.0.0.1:8011',
      development: true, enabled: true, allowedRuns: '',
      fetcher: async (url, init) => {
        if (url.pathname === '/session') return Response.json({ authenticated: true, auth_required: true });
        launches++;
        assert.equal(JSON.parse(init.body).hasta, dateTo);
        assert.equal(JSON.parse(init.body).nombre, 'Fábrica');
        return Response.json({ tarea: 'abcdef123456', estado: 'corriendo' });
      },
    });
    assert.equal(response.status, status);
    assert.equal(launches, status === 200 ? 1 : 0);
  }
});
