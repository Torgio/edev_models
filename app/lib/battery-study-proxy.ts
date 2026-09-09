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

/** Local, read-only bridge for explicitly selected team studies, pending per-user ownership. */
export async function proxyBatteryStudy(request: Request, path: string, options: {
  upstream: string; studyUpstream?: string; development: boolean; allowedRuns: string; fetcher?: typeof fetch;
}) {
  const url = new URL(request.url);
  if (!options.development || !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)) {
    return reply('La consulta de estudios está disponible solo en la prueba local.', 404);
  }
  const upload = request.method === 'POST' && path === 'curvas';
  if (request.method !== 'GET' && !upload) return reply('Operación no permitida.', 405);
  const ids = [...new Set(options.allowedRuns.split(',').map(s => s.trim())
    .filter(s => /^[1-9]\d*$/.test(s)).map(Number).filter(Number.isSafeInteger))];
  const match = /^(resultado|despacho)\/([1-9]\d*)$/.exec(path);
  if (!upload && path !== 'opciones' && (!match || !ids.includes(Number(match[2])))) {
    return reply('Este estudio no está habilitado para la prueba local.', 404);
  }
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
  if (path === 'opciones') return Response.json({ runs: ids }, {
    headers: { 'Cache-Control': 'private, no-store', Vary: 'Cookie' },
  });
  let uploadBody: FormData | undefined;
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
      for (const file of [consumption, generation].filter(Boolean)) {
        if (typeof file === 'string' || file.size > 5 * 1024 * 1024 || !/\.(csv|txt)$/i.test(file.name)) return reply('Solo se admiten CSV o TXT de hasta 5 MB.', 400);
      }
      uploadBody = new FormData();
      uploadBody.set('consumo', consumption, consumption.name);
      if (generation && typeof generation !== 'string') uploadBody.set('generacion', generation, generation.name);
      uploadBody.set('code_consumo', 'WEB-CONSUMO');
      uploadBody.set('code_generacion', 'WEB-GENERACION');
      uploadBody.set('unidad', 'auto'); uploadBody.set('tecnologia', 'fv'); uploadBody.set('forzar', 'false');
    } catch (error) {
      return reply(error instanceof Error && error.message === 'too-large' ? 'La carga supera el límite de 11 MB.' : 'No se pudo leer el formulario.', error instanceof Error && error.message === 'too-large' ? 413 : 400);
    }
  }
  const headers = new Headers({ Accept: 'application/json' });
  const cookie = request.headers.get('cookie')?.split(';').map(c => c.trim())
    .find(c => /^pulso_session=[A-Za-z0-9_.-]{1,1024}$/.test(c));
  if (cookie) headers.set('Cookie', cookie);
  const destination = new URL(`/api/bat/${path}`, options.studyUpstream || options.upstream);
  destination.search = url.search;
  try {
    const response = await (options.fetcher ?? fetch)(destination, {
      method: upload ? 'POST' : 'GET', headers, body: uploadBody,
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
    const returnedId = match?.[1] === 'resultado' ? data?.run?.run_id : data?.run_id;
    if (match && returnedId !== Number(match[2])) return reply('El servidor devolvió otro estudio.', 502);
    return Response.json(data, { headers: { 'Cache-Control': 'private, no-store', Vary: 'Cookie' } });
  } catch {
    return reply('No se pudo conectar con el servicio de estudios.', 503);
  }
}
