import test from 'node:test';
import assert from 'node:assert/strict';
import { proxyBatteryStudy } from './battery-study-proxy.ts';
import { studyPoints, nominalDayOffset, studyCoverage, studyInputs, toKilo } from './battery-study.ts';
import { previewCurve } from './battery-curve-preview.ts';

test('snapshot context supports legacy, rejects other cases and preserves absent vs zero', () => {
  const base = { run: { case_id: 13 }, anual: [] };
  const inputs = { schema_version: 1, case: { case_id: 13 },
    period: { date_from: '2027-03-28', date_to: '2027-12-31', calendar: 'nominal_24h' },
    battery: { power_mw: .05, capacity_mwh: .2 }, consumption: null,
    generation: { capacity_mwp: 0 } };
  assert.equal(studyInputs(base), null);
  assert.equal(studyInputs({ ...base, inputs: { ...inputs, schema_version: 2 } }), null);
  assert.equal(studyInputs({ ...base, inputs: { ...inputs, case: { case_id: 99 } } }), null);
  const saved = studyInputs({ ...base, inputs });
  assert.equal(saved.consumption, null);
  assert.equal(toKilo(saved.battery.power_mw), 50);
  assert.equal(toKilo(saved.battery.capacity_mwh), 200);
  assert.equal(toKilo(saved.generation.capacity_mwp), 0);
  assert.equal(toKilo(undefined), null);
  assert.equal(studyInputs({ ...base, inputs: { ...inputs, period: { ...inputs.period, date_from: '2027-02-30' } } }), null);
});

test('coverage preserves missing years and does not invent exact period dates', () => {
  assert.equal(studyCoverage([{ ano: 2030, dias: 2 }, { ano: 2027, dias: 10 }, { ano: 2027, dias: 10 }]), '2027 · 2030');
  assert.equal(studyCoverage([]), 'No disponibles');
});

const options = { upstream: 'https://auth.example.test', studyUpstream: 'http://127.0.0.1:8011', development: true, allowedRuns: '16' };
const request = (path, params = '') => new Request(`http://localhost:3000/api/battery-study/${path}${params}`, {
  headers: { Cookie: 'pulso_session=signed.token; other=private' },
});
test('study bridge is unavailable outside local development and denies unlisted studies and writes', async () => {
  const fetcher = async () => { throw new Error('must not reach upstream'); };
  for (const [req, config, status] of [
    [request('resultado/16'), { ...options, development: false }, 404],
    [new Request('https://public.test/api/battery-study/resultado/16'), options, 404],
    [request('resultado/17'), options, 404],
    [request('resultado/16', '?email=another'), options, 400],
    [new Request(request('resultado/16'), { method: 'POST' }), options, 405],
  ]) assert.equal((await proxyBatteryStudy(req, req.url.includes('/17') ? 'resultado/17' : 'resultado/16', { ...config, fetcher })).status, status);
});
test('study bridge verifies session before reading and forwards only the Pulso cookie', async () => {
  let count = 0;
  const fetcher = async (url, init) => {
    count++;
    if (url.pathname === '/session') return Response.json({ authenticated: false, auth_required: true });
    throw new Error('must not read');
  };
  assert.equal((await proxyBatteryStudy(request('resultado/16'), 'resultado/16', { ...options, fetcher })).status, 401);
  assert.equal(count, 1);
  const resultFetcher = async (url, init) => {
    if (url.pathname === '/session') {
      assert.equal(url.origin, 'https://auth.example.test');
      return Response.json({ authenticated: true, auth_required: true });
    }
    assert.equal(url.origin, 'http://127.0.0.1:8011');
    assert.equal(url.pathname, '/api/bat/resultado/16');
    assert.equal(init.headers.get('Cookie'), 'pulso_session=signed.token');
    return Response.json({ run: { run_id: 16, npv_p50: 0 }, anual: [] });
  };
  const response = await proxyBatteryStudy(request('resultado/16'), 'resultado/16', { ...options, fetcher: resultFetcher });
  assert.equal(response.status, 200);
  assert.equal((await response.json()).run.npv_p50, 0);
});
test('curve preview validates a nominal year and detects photovoltaic generation at night', () => {
  const rows = ['fecha,hora,consumo_kwh'];
  for (let day = 0; day < 365; day++) {
    const date = new Date(Date.UTC(2027, 0, 1 + day)).toISOString().slice(0, 10);
    const hours = date === '2027-03-28' ? 23 : date === '2027-10-31' ? 25 : 24;
    for (let hour = 1; hour <= hours; hour++) rows.push(`${date},${hour},${hour === 1 ? 2 : 1}`);
  }
  const consumption = previewCurve(rows.join('\n'), 'consumo');
  assert.equal(consumption.days, 365);
  assert.equal(consumption.hours, 8760);
  assert.equal(consumption.issues.length, 0);
  assert.equal(consumption.meanKw[0], 2);
  const generation = previewCurve(rows.join('\n').replace('consumo_kwh', 'generacion_kwh'), 'generacion');
  assert.match(generation.issues.join(' '), /fotovoltaica/);
});
test('curve upload is local, authenticated, bounded and reconstructed without identity fields', async () => {
  const form = new FormData();
  form.set('consumo', new Blob(['fecha,hora,consumo_kwh\n2027-01-01,1,1']), 'consumo.csv');
  const uploadRequest = new Request('http://localhost:3000/api/battery-study/curvas', {
    method: 'POST', headers: { Cookie: 'pulso_session=signed.token', Origin: 'http://localhost:3000' }, body: form,
  });
  const fetcher = async (url, init) => {
    if (url.pathname === '/session') return Response.json({ authenticated: true, auth_required: true });
    assert.equal(url.origin, 'http://127.0.0.1:8011');
    assert.equal(url.pathname, '/api/bat/curvas');
    assert.equal(init.method, 'POST');
    assert.equal(init.body.get('code_consumo'), 'WEB-CONSUMO');
    assert.equal(init.body.get('email'), null);
    return Response.json([{ tipo: 'consumo', ok: true, code: 'WEB-CONSUMO', problemas: [], curva: {} }]);
  };
  assert.equal((await proxyBatteryStudy(uploadRequest, 'curvas', { ...options, fetcher })).status, 200);
  const bad = new FormData(); bad.set('consumo', new Blob(['x']), 'consumo.csv'); bad.set('email', 'otro@ejemplo.es');
  const badRequest = new Request('http://localhost:3000/api/battery-study/curvas', { method: 'POST', headers: { Origin: 'http://localhost:3000' }, body: bad });
  assert.equal((await proxyBatteryStudy(badRequest, 'curvas', { ...options, fetcher })).status, 400);
});
test('nominal DST hours retain h3 and h4 and units convert without inventing nulls', () => {
  const data = { run_id: 16, escenario: 4, horas: 2, t: ['2027-03-28 02:00:00', '2027-03-28 03:00:00'],
    precio: [0, -5], carga: [.05, null], descarga: [0, .01], soc: [.2, .19],
    consumo: [.1, .12], generacion: [0, 0], importado: [.15, .11], exportado: [0, 0] };
  const points = studyPoints(data);
  assert.equal(points[0].label, '28/03 · h3');
  assert.equal(points[1].label, '28/03 · h4');
  assert.equal(points[0].charge, 50);
  assert.equal(points[0].soc, 200);
  assert.equal(points[1].charge, null);
  assert.equal(nominalDayOffset('2027-03-28', 1), '2027-03-29');
  assert.throws(() => studyPoints({ ...data, soc: [] }), /incompletas/);
});
