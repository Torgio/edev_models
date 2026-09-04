const COOKIE = 'pulso_session';
const SESSION_SECONDS = 8 * 60 * 60;
const READ_PATH = /^(session|health|days|leaderboard|performance-history|performance-options|peak-accuracy|(?:predictions|bess)\/\d{4}-\d{2}-\d{2})$/;

function reply(value: unknown, status = 200) {
  return Response.json(value, { status, headers: { 'Cache-Control': 'private, no-store', Vary: 'Cookie', 'X-Content-Type-Options': 'nosniff' } });
}

function sessionCookie(request: Request) {
  const entry = request.headers.get('cookie')?.split(';').map((item) => item.trim()).find((item) => item.startsWith(`${COOKIE}=`));
  const value = entry?.slice(COOKIE.length + 1);
  return value && /^[A-Za-z0-9_.-]{1,1024}$/.test(value) ? `${COOKIE}=${value}` : '';
}

function browserSessionCookie(raw: string | null, path: string, secure: boolean) {
  if (!raw?.startsWith(`${COOKIE}=`)) return '';
  if (path === 'logout') {
    return `${COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict${secure ? '; Secure' : ''}`;
  }
  const encoded = raw.slice(COOKIE.length + 1).split(';', 1)[0].replace(/^"|"$/g, '');
  if (!/^[A-Za-z0-9_.-]{1,1024}$/.test(encoded)) return '';
  return `${COOKIE}=${encoded}; Path=/; Max-Age=${SESSION_SECONDS}; HttpOnly; SameSite=Strict${secure ? '; Secure' : ''}`;
}

/** Proxy cerrado: solo rutas conocidas, nunca una URL proporcionada por el visitante. */
export async function proxyDashboardRequest(request: Request, path: string, options: {
  upstream: string; development?: boolean; fetcher?: typeof fetch;
}) {
  const fetcher = options.fetcher ?? fetch;
  const requestUrl = new URL(request.url);
  const isRead = request.method === 'GET' && READ_PATH.test(path);
  const isWrite = request.method === 'POST' && ['login', 'logout'].includes(path);
  if (!isRead && !isWrite) return reply({ detail: 'Ruta no disponible.' }, 404);
  if (isWrite && request.headers.get('origin') !== requestUrl.origin) return reply({ detail: 'Origen no permitido.' }, 403);
  if ([...requestUrl.searchParams.keys()].some((key) => !['source', 'days', 'model', 'seed', 'duration'].includes(key) && !(path === 'peak-accuracy' && key === 'end_date'))) {
    return reply({ detail: 'Parámetros no permitidos.' }, 400);
  }
  let upstream: URL;
  try {
    upstream = new URL(options.upstream);
  } catch {
    return reply({ detail: 'Conexión pendiente de configurar.' }, 503);
  }
  const local = options.development === true && ['127.0.0.1', 'localhost'].includes(upstream.hostname);
  if ((upstream.protocol !== 'https:' && !(local && upstream.protocol === 'http:')) || upstream.username || upstream.password || upstream.pathname !== '/' || upstream.search || upstream.hash) {
    return reply({ detail: 'Conexión no válida.' }, 503);
  }
  const headers = new Headers({ Accept: 'application/json' });
  const cookie = sessionCookie(request);
  if (cookie) headers.set('Cookie', cookie);
  try {
    // Incluso si el backend se configura accidentalmente sin autenticación,
    // producción no entrega datos. La única excepción es el desarrollo local.
    const sessionResponse = await fetcher(new URL('/session', upstream), { headers, cache: 'no-store', redirect: 'manual', signal: AbortSignal.timeout(8000) });
    if (!sessionResponse.ok && !(local && sessionResponse.status === 404)) return reply({ detail: 'Servicio de acceso no disponible.' }, 503);
    // Compatibilidad con la API local anterior; nunca se permite en producción.
    const session = local && sessionResponse.status === 404
      ? { authenticated: true, auth_required: false }
      : await sessionResponse.json();
    if (session.auth_required !== true && !local) return reply({ detail: 'El acceso seguro aún no está configurado.' }, 503);
    if (path === 'session') return reply({
      authenticated: session.authenticated === true,
      auth_required: session.auth_required === true,
      username: typeof session.username === 'string' ? session.username : null,
    });
    if (isRead && session.authenticated !== true) return reply({ detail: 'Inicia sesión para consultar los datos.' }, 401);

    let body: string | undefined;
    if (path === 'login') {
      if (request.headers.get('content-type')?.split(';')[0].trim() !== 'application/json') return reply({ detail: 'Se requiere JSON.' }, 415);
      const reader = request.body?.getReader();
      const chunks: Uint8Array[] = [];
      let length = 0;
      if (reader) {
        while (true) {
          const next = await reader.read();
          if (next.done) break;
          length += next.value.byteLength;
          if (length > 2048) { await reader.cancel(); return reply({ detail: 'Solicitud demasiado grande.' }, 413); }
          chunks.push(next.value);
        }
      }
      const bytes = new Uint8Array(length);
      let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
      body = new TextDecoder().decode(bytes);
      headers.set('Content-Type', 'application/json');
    }
    const destination = new URL(`/${path}`, upstream);
    destination.search = requestUrl.search;
    const result = await fetcher(destination, { method: request.method, headers, body, redirect: 'manual', cache: 'no-store', signal: AbortSignal.timeout(20000) });
    if (result.status >= 300 && result.status < 400) return reply({ detail: 'Redirección inesperada de la API.' }, 502);
    if (result.status >= 500) return reply({ detail: 'La API no está disponible. Inténtalo de nuevo.' }, 502);
    if (!result.headers.get('content-type')?.includes('application/json')) return reply({ detail: 'Respuesta no válida de la API.' }, 502);
    const response = reply(await result.json(), result.status);
    const setCookie = isWrite
      ? browserSessionCookie(result.headers.get('set-cookie'), path, requestUrl.protocol === 'https:')
      : '';
    if (setCookie) response.headers.set('Set-Cookie', setCookie);
    const retry = result.headers.get('retry-after');
    if (retry && /^\d+$/.test(retry)) response.headers.set('Retry-After', retry);
    return response;
  } catch (error) {
    console.error('Dashboard API connection failed', { upstream: upstream.origin, path, error: error instanceof Error ? error.message : 'unknown' });
    return reply({ detail: 'No se pudo conectar con el servicio. Inténtalo de nuevo.' }, 503);
  }
}
