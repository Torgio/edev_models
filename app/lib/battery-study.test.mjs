import test from 'node:test';
import assert from 'node:assert/strict';
import { proxyBatteryStudy } from './battery-study-proxy.ts';
import { studyPoints, nominalDayOffset, studyCoverage, studyInputs, toKilo } from './battery-study.ts';
import { previewCurve } from './battery-curve-preview.ts';
import { batteryIssues, batterySummary, DEFAULT_BATTERY } from './battery-draft.ts';

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

const options = { upstream: 'https://auth.example.test', studyUpstream: 'http://127.0.0.1:8011', enabled: true, development: true, allowedRuns: '16' };
const request = (path, params = '') => new Request(`http://localhost:3000/api/battery-study/${path}${params}`, {
  headers: { Cookie: 'pulso_session=signed.token; other=private' },
});
test('study bridge requires its explicit switch and denies unknown routes and writes', async () => {
  const fetcher = async () => { throw new Error('must not reach upstream'); };
  for (const [req, config, status] of [
    [request('resultado/16'), { ...options, enabled: false }, 404],
    [request('resultado/16', '?email=another'), options, 400],
    [new Request(request('resultado/16'), { method: 'POST' }), options, 405],
  ]) assert.equal((await proxyBatteryStudy(req, 'resultado/16', { ...config, fetcher })).status, status);
});
test('production bridge accepts an HTTPS path prefix and validates discovered runs without local memory', async () => {
  const production = { ...options, studyUpstream: 'https://api.example.test/api/bat-test/', development: false, allowedRuns: '' };
  const req = new Request('https://public.test/api/battery-study/resultado/23', {
    headers: { Cookie: 'pulso_session=signed.token' },
  });
  const paths = [];
  const fetcher = async url => {
    paths.push(url.pathname);
    if (url.origin === 'https://auth.example.test') return Response.json({ authenticated: true, auth_required: true });
    if (url.pathname === '/api/bat-test/ejecuciones') return Response.json({ runs: [{ run_id: 23, code: 'WEB-ABC' }] });
    return Response.json({ run: { run_id: 23 }, anual: [] });
  };
  assert.equal((await proxyBatteryStudy(req, 'resultado/23', { ...production, fetcher })).status, 200);
  assert.deepEqual(paths, ['/session', '/api/bat-test/ejecuciones', '/api/bat-test/resultado/23']);
  const unknown = new Request('https://public.test/api/battery-study/resultado/24', {
    headers: { Cookie: 'pulso_session=signed.token' },
  });
  assert.equal((await proxyBatteryStudy(unknown, 'resultado/24', { ...production, fetcher })).status, 404);
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
test('study launch fixes the uploaded curves, tracks its task and exposes only its resulting run', async () => {
  const configuration = {
    ...DEFAULT_BATTERY, generationIncluded: true, dateFrom: '2026-01-05', dateTo: '2026-01-06', scenarios: 1, policy: 'libre',
  };
  const launchRequest = new Request('http://localhost:3000/api/battery-study/estudio', {
    method: 'POST', headers: { Cookie: 'pulso_session=signed.token', Origin: 'http://localhost:3000', 'Content-Type': 'application/json' },
    body: JSON.stringify(configuration),
  });
  const fetcher = async (url, init) => {
    if (url.pathname === '/session') return Response.json({ authenticated: true, auth_required: true });
    assert.equal(url.pathname, '/api/bat/estudio');
    assert.equal(init.method, 'POST');
    const sent = JSON.parse(init.body);
    assert.equal(sent.consumo, 'WEB-CONSUMO');
    assert.equal(sent.generacion, 'WEB-GENERACION');
    assert.equal(sent.email, undefined);
    assert.equal(sent.eficiencia, .9);
    assert.equal(sent.desde, '2026-01-05');
    assert.match(sent.code, /^WEB-/);
    return Response.json({ tarea: 'abcdef123456', estado: 'corriendo' });
  };
  assert.equal((await proxyBatteryStudy(launchRequest, 'estudio', { ...options, fetcher })).status, 200);
  const taskFetcher = async url => {
    if (url.pathname === '/session') return Response.json({ authenticated: true, auth_required: true });
    if (url.pathname === '/api/bat/ejecuciones') return Response.json({ runs: [
      { run_id: 23, code: 'WEB-ABC' }, { run_id: 99, code: 'PRIVATE' },
    ] });
    return Response.json({ estado: 'hecho', progreso: 1, run_id: 23, paso: 'terminado' });
  };
  const taskResponse = await proxyBatteryStudy(request('estudio/abcdef123456'), 'estudio/abcdef123456', { ...options, fetcher: taskFetcher });
  assert.equal(taskResponse.status, 200);
  assert.equal((await taskResponse.json()).tarea, 'abcdef123456');
  const optionsResponse = await proxyBatteryStudy(request('opciones'), 'opciones', { ...options, fetcher: taskFetcher });
  assert.deepEqual((await optionsResponse.json()).runs, [23, 16]);
});
test('study launch rejects client identity and unsafe execution limits', async () => {
  const fetcher = async url => {
    if (url.pathname === '/session') return Response.json({ authenticated: true, auth_required: true });
    throw new Error('must not launch upstream');
  };
  for (const body of [
    { ...DEFAULT_BATTERY, generationIncluded: true, dateFrom: '2026-01-05', dateTo: '2026-01-06', scenarios: 1, policy: 'libre', email: 'otro@ejemplo.es' },
    { ...DEFAULT_BATTERY, generationIncluded: true, dateFrom: '2026-01-01', dateTo: '2026-02-15', scenarios: 20, policy: 'libre' },
  ]) {
    const req = new Request('http://localhost:3000/api/battery-study/estudio', {
      method: 'POST', headers: { Origin: 'http://localhost:3000', 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    assert.equal((await proxyBatteryStudy(req, 'estudio', { ...options, fetcher })).status, 400);
  }
});
test('battery summary uses installed MWh, useful SoC window and price per installed MWh', () => {
  const result = batterySummary(DEFAULT_BATTERY);
  assert.equal(result.capacityMwh, .4);
  assert.equal(result.usableMwh, .36);
  assert.equal(result.totalCostEur, 80000);
  assert.ok(Math.abs(result.cycleCostEurMwh - 37.037037) < 1e-6);
  assert.deepEqual(batteryIssues(DEFAULT_BATTERY), []);
  assert.match(batteryIssues({ ...DEFAULT_BATTERY, socMinPct: 95 }).join(' '), /ventana/);
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
