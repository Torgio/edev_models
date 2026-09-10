import { proxyDashboardRequest } from './dashboard-proxy.ts';

const reply = (detail: string, status: number) => Response.json({ detail }, {
  status, headers: { 'Cache-Control': 'private, no-store', Vary: 'Cookie' },
});
const validDate = (value: string) => /^\d{4}-\d{2}-\d{2}$/.test(value)
  && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
const MAX_UPLOAD_BYTES = 11 * 1024 * 1024;

async function boundedBody(request: Request) {
  const declared = Number(request.headers.get('content-length'));
  if (Number.isFinite(declared) && declared > MAX_UPLOAD_BYTES) throw new Error('too-large');
  const reader = request.body?.getReader();
  if (!reader) return new Uint8Array();
  const chunks: Uint8Array[] = []; let size = 0;
  while (true) {
    const item = await reader.read();
    if (item.done) break;
    size += item.value.length;
    if (size > MAX_UPLOAD_BYTES) { await reader.cancel(); throw new Error('too-large'); }
    chunks.push(item.value);
  }
  const bytes = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  return bytes;
}

function studyDestination(base: string, path: string, development: boolean) {
  const upstream = new URL(base);
  const local = development && ['localhost', '127.0.0.1', '[::1]'].includes(upstream.hostname);
  if ((upstream.protocol !== 'https:' && !(local && upstream.protocol === 'http:'))
      || upstream.username || upstream.password || upstream.search || upstream.hash) throw new Error('invalid-upstream');
  const configuredPrefix = upstream.pathname.replace(/\/+$/, '');
  const prefix = configuredPrefix || '/api/bat';
  upstream.pathname = `${prefix}/${path === 'opciones' ? 'ejecuciones' : path}`.replace(/\/{2,}/g, '/');
  return upstream;
}

function webRunIds(data: unknown) {
  if (!data || typeof data !== 'object' || !Array.isArray((data as { runs?: unknown }).runs)) return null;
  return (data as { runs: unknown[] }).runs.filter((item): item is { run_id: number; code: string } => Boolean(item)
    && typeof item === 'object' && Number.isSafeInteger((item as { run_id?: unknown }).run_id)
    && (item as { run_id: number }).run_id > 0 && typeof (item as { code?: unknown }).code === 'string'
    && (item as { code: string }).code.startsWith('WEB-')).map(item => item.run_id);
}

/** Authenticated bridge to the isolated team-study API. It never accepts client identity. */
export async function proxyBatteryStudy(request: Request, path: string, options: {
  upstream: string; studyUpstream?: string; enabled: boolean; development: boolean; allowedRuns: string; fetcher?: typeof fetch;
}) {
  const url = new URL(request.url);
  if (!options.enabled) return reply('El estudio de instalación no está habilitado.', 404);
  const upload = request.method === 'POST' && path === 'curvas';
  const launch = request.method === 'POST' && path === 'estudio';
  if (request.method !== 'GET' && !upload && !launch) return reply('Operación no permitida.', 405);
  const ids = [...new Set(options.allowedRuns.split(',').map(s => s.trim())
    .filter(s => /^[1-9]\d*$/.test(s)).map(Number).filter(Number.isSafeInteger))];
  const match = /^(resultado|despacho)\/([1-9]\d*)$/.exec(path);
  const taskMatch = /^estudio\/([a-f0-9]{12})$/.exec(path);
  if (!upload && !launch && path !== 'opciones' && !match && !taskMatch) return reply('Ruta de estudio no disponible.', 404);
  const allowedKeys = match?.[1] === 'despacho' ? ['desde', 'hasta', 'escenario'] : [];
  if ([...url.searchParams.keys()].some(k => !allowedKeys.includes(k) || url.searchParams.getAll(k).length !== 1)) {
    return reply('Parámetros no permitidos.', 400);
  }
  if (match?.[1] === 'despacho') {
    const desde = url.searchParams.get('desde') ?? '', hasta = url.searchParams.get('hasta') ?? '';
    const scenario = url.searchParams.get('escenario');
    if (!validDate(desde) || !validDate(hasta) || hasta < desde
        || (Date.parse(hasta) - Date.parse(desde)) / 86400000 > 30
        || (scenario !== null && !/^\d{1,6}$/.test(scenario))) {
      return reply('Elige un tramo de entre 1 y 31 días.', 400);
    }
  }
  // Reuse Pulso's session verification and upstream validation. No client-supplied identity.
  const sessionUrl = new URL('/api/dashboard/session', url.origin);
  const sessionResponse = await proxyDashboardRequest(new Request(sessionUrl, {
    headers: request.headers,
  }), 'session', { upstream: options.upstream, development: true, fetcher: options.fetcher });
  if (!sessionResponse.ok) return sessionResponse;
  const session = await sessionResponse.json() as { authenticated?: boolean } | null;
  if (session?.authenticated !== true) return reply('Inicia sesión para consultar el estudio.', 401);
  let destination: URL;
  try {
    destination = studyDestination(options.studyUpstream ?? '', path, options.development);
  } catch {
    return reply('La conexión con el entorno de prueba no está configurada.', 503);
  }
  let upstreamBody: BodyInit | undefined;
  if (upload) {
    if (request.headers.get('origin') !== url.origin) return reply('Origen no permitido.', 403);
    const type = request.headers.get('content-type') ?? '';
    if (!type.toLowerCase().startsWith('multipart/form-data;')) return reply('Se requiere un formulario de ficheros.', 415);
    try {
      const bytes = await boundedBody(request);
      const parsed = await new Request(url, { method: 'POST', headers: { 'Content-Type': type }, body: bytes }).formData();
      if ([...parsed.keys()].some(key => !['consumo', 'generacion'].includes(key))) return reply('El formulario contiene campos no permitidos.', 400);
      const consumption = parsed.get('consumo'), generation = parsed.get('generacion');
      if (!consumption || typeof consumption === 'string') return reply('La curva de consumo es obligatoria.', 400);
      for (const file of [consumption, generation]) {
        if (file === null) continue;
        if (typeof file === 'string' || file.size > 5 * 1024 * 1024 || !/\.(csv|txt)$/i.test(file.name)) return reply('Solo se admiten CSV o TXT de hasta 5 MB.', 400);
      }
      const uploadForm = new FormData();
      uploadForm.set('consumo', consumption, consumption.name);
      if (generation && typeof generation !== 'string') uploadForm.set('generacion', generation, generation.name);
      uploadForm.set('code_consumo', 'WEB-CONSUMO');
      uploadForm.set('code_generacion', 'WEB-GENERACION');
      uploadForm.set('unidad', 'auto'); uploadForm.set('tecnologia', 'fv'); uploadForm.set('forzar', 'false');
      upstreamBody = uploadForm;
    } catch (error) {
      return reply(error instanceof Error && error.message === 'too-large' ? 'La carga supera el límite de 11 MB.' : 'No se pudo leer el formulario.', error instanceof Error && error.message === 'too-large' ? 413 : 400);
    }
  }
  if (launch) {
    if (request.headers.get('origin') !== url.origin) return reply('Origen no permitido.', 403);
    if (!(request.headers.get('content-type') ?? '').toLowerCase().startsWith('application/json')) return reply('Se requiere una configuración JSON.', 415);
    try {
      const bytes = await boundedBody(request);
      const raw: unknown = JSON.parse(new TextDecoder().decode(bytes));
      if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return reply('La configuración no es válida.', 400);
      const input = raw as Record<string, unknown>;
      const allowed = ['powerKw', 'durationH', 'capexEurMwh', 'efficiencyPct', 'cycles', 'socMinPct', 'socMaxPct', 'chargeMaxPct', 'dischargeMaxPct', 'minimumPowerPct', 'generationIncluded', 'dateFrom', 'dateTo', 'scenarios', 'policy'];
      if (Object.keys(input).some(key => !allowed.includes(key))) return reply('La configuración contiene campos no permitidos.', 400);
      const numberIn = (key: string, min: number, max: number) => typeof input[key] === 'number' && Number.isFinite(input[key]) && input[key] >= min && input[key] <= max;
      const duration = input.durationH;
      const dateFrom = typeof input.dateFrom === 'string' ? input.dateFrom : '';
      const dateTo = typeof input.dateTo === 'string' ? input.dateTo : '';
      const days = (Date.parse(dateTo) - Date.parse(dateFrom)) / 86400000 + 1;
      const valid = numberIn('powerKw', 5, 100000) && typeof duration === 'number' && [1, 2, 3, 4, 6, 8].includes(duration)
        && numberIn('capexEurMwh', 20000, 2000000) && numberIn('efficiencyPct', 70, 99)
        && numberIn('cycles', 1000, 15000) && Number.isInteger(input.cycles)
        && numberIn('socMinPct', 0, 30) && numberIn('socMaxPct', 70, 100) && Number(input.socMinPct) < Number(input.socMaxPct)
        && numberIn('chargeMaxPct', 10, 100) && numberIn('dischargeMaxPct', 10, 100) && numberIn('minimumPowerPct', 0, 50)
        && validDate(dateFrom) && validDate(dateTo) && Number.isInteger(days) && days >= 2 && days <= 31
        && typeof input.generationIncluded === 'boolean'
        && typeof input.scenarios === 'number' && [1, 3, 5].includes(input.scenarios)
        && typeof input.policy === 'string' && ['libre', 'prefiere_excedente', 'solo_excedente'].includes(input.policy);
      if (!valid) return reply('Revisa la batería, el período y los escenarios.', 400);
      const code = `WEB-${Date.now().toString(36).toUpperCase()}-${crypto.randomUUID().slice(0, 6).toUpperCase()}`;
      upstreamBody = JSON.stringify({
        code, consumo: 'WEB-CONSUMO', generacion: input.generationIncluded ? 'WEB-GENERACION' : null,
        potencia_kw: input.powerKw, duracion_h: input.durationH, capex_eur_mwh: input.capexEurMwh,
        eficiencia: Number(input.efficiencyPct) / 100, soc_min: Number(input.socMinPct) / 100,
        soc_max: Number(input.socMaxPct) / 100, ciclos: input.cycles,
        carga_max_pct: input.chargeMaxPct, descarga_max_pct: input.dischargeMaxPct, p_min_pct: input.minimumPowerPct,
        desde: dateFrom, hasta: dateTo, escenarios: input.scenarios, politica: input.policy,
        tasa: 0.07, opex: 0.015,
      });
    } catch (error) {
      return reply(error instanceof Error && error.message === 'too-large' ? 'La configuración supera el límite permitido.' : 'No se pudo leer la configuración.', error instanceof Error && error.message === 'too-large' ? 413 : 400);
    }
  }
  const headers = new Headers({ Accept: 'application/json' });
  if (launch) headers.set('Content-Type', 'application/json');
  const cookie = request.headers.get('cookie')?.split(';').map(c => c.trim())
    .find(c => /^pulso_session=[A-Za-z0-9_.-]{1,1024}$/.test(c));
  if (cookie) headers.set('Cookie', cookie);
  destination.search = url.search;
  try {
    if (match && !ids.includes(Number(match[2]))) {
      const catalogUrl = studyDestination(options.studyUpstream ?? '', 'opciones', options.development);
      const catalogResponse = await (options.fetcher ?? fetch)(catalogUrl, {
        headers, redirect: 'manual', cache: 'no-store', signal: AbortSignal.timeout(10000),
      });
      if (!catalogResponse.ok || !catalogResponse.headers.get('content-type')?.includes('application/json')) {
        return reply('No se pudo validar el catálogo de estudios.', 502);
      }
      const discovered = webRunIds(await catalogResponse.json());
      if (!discovered?.includes(Number(match[2]))) return reply('Este estudio no pertenece al entorno de prueba.', 404);
    }
    const response = await (options.fetcher ?? fetch)(destination, {
      method: upload || launch ? 'POST' : 'GET', headers, body: upstreamBody,
      redirect: 'manual', cache: 'no-store', signal: AbortSignal.timeout(upload ? 60000 : 20000),
    });
    if (response.status === 401 || response.status === 403 || (response.status >= 300 && response.status < 400)) {
      return reply('La sesión no permite consultar el estudio en el servidor.', 401);
    }
    if (response.status === 404) return reply('No hay datos guardados para este estudio o tramo.', 404);
    if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) {
      return reply('No se pudo consultar el estudio guardado.', 502);
    }
    const reader = response.body?.getReader();
    const chunks: Uint8Array[] = [];
    let size = 0;
    if (!reader) return reply('Respuesta vacía del estudio.', 502);
    while (true) {
      const item = await reader.read();
      if (item.done) break;
      size += item.value.length;
      if (size > 2 * 1024 * 1024) { await reader.cancel(); return reply('Reduce el tramo del estudio.', 502); }
      chunks.push(item.value);
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const c of chunks) { bytes.set(c, offset); offset += c.length; }
    const data = JSON.parse(new TextDecoder().decode(bytes));
    if (path === 'opciones') {
      const discovered = webRunIds(data);
      if (!discovered) return reply('El servidor devolvió un catálogo de estudios inválido.', 502);
      return Response.json({ runs: [...new Set([...discovered, ...ids])] }, {
        headers: { 'Cache-Control': 'private, no-store', Vary: 'Cookie' },
      });
    }
    if (launch) {
      if (!data || typeof data.tarea !== 'string' || !/^[a-f0-9]{12}$/.test(data.tarea) || data.estado !== 'corriendo') return reply('El servidor no confirmó la tarea.', 502);
    }
    if (taskMatch) {
      if (!data || (data.tarea !== undefined && data.tarea !== taskMatch[1]) || !['corriendo', 'hecho', 'error'].includes(data.estado)) return reply('El servidor devolvió otra tarea.', 502);
      data.tarea = taskMatch[1];
      if (data.estado === 'hecho') {
        if (!Number.isSafeInteger(data.run_id) || data.run_id < 1) return reply('El estudio terminó sin una ejecución válida.', 502);
      }
    }
    const returnedId = match?.[1] === 'resultado' ? data?.run?.run_id : data?.run_id;
    if (match && returnedId !== Number(match[2])) return reply('El servidor devolvió otro estudio.', 502);
    return Response.json(data, { headers: { 'Cache-Control': 'private, no-store', Vary: 'Cookie' } });
  } catch {
    return reply('No se pudo conectar con el servicio de estudios.', 503);
  }
}
