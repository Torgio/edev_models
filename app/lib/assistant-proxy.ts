const COOKIE = 'pulso_session';
const MAX_REQUEST_BYTES = 4096;
const MAX_RESPONSE_BYTES = 8 * 1024 * 1024;

function reply(value: unknown, status = 200, extraHeaders?: HeadersInit) {
  return Response.json(value, {
    status,
    headers: {
      'Cache-Control': 'private, no-store',
      Vary: 'Cookie',
      'X-Content-Type-Options': 'nosniff',
      ...extraHeaders,
    },
  });
}

function sessionCookie(request: Request) {
  const entry = request.headers.get('cookie')?.split(';').map(item => item.trim())
    .find(item => item.startsWith(`${COOKIE}=`));
  const value = entry?.slice(COOKIE.length + 1);
  return value && /^[A-Za-z0-9_.-]{1,1024}$/.test(value) ? `${COOKIE}=${value}` : '';
}

function safeUpstream(value: string, development: boolean) {
  const upstream = new URL(value);
  const local = development && ['127.0.0.1', 'localhost'].includes(upstream.hostname);
  if ((upstream.protocol !== 'https:' && !(local && upstream.protocol === 'http:'))
      || upstream.username || upstream.password || upstream.pathname !== '/'
      || upstream.search || upstream.hash) {
    throw new Error('Invalid upstream');
  }
  return { upstream, local };
}

async function readLimited(response: Response, limit: number) {
  const declared = Number(response.headers.get('content-length'));
  if (Number.isFinite(declared) && declared > limit) throw new Error('Response too large');
  const reader = response.body?.getReader();
  if (!reader) return new Uint8Array();
  const chunks: Uint8Array[] = [];
  let length = 0;
  while (true) {
    const next = await reader.read();
    if (next.done) break;
    length += next.value.byteLength;
    if (length > limit) {
      await reader.cancel();
      throw new Error('Response too large');
    }
    chunks.push(next.value);
  }
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  return bytes;
}

export async function proxyAssistantRequest(request: Request, options: {
  authUpstream: string;
  assistantUpstream: string;
  development?: boolean;
  fetcher?: typeof fetch;
}) {
  if (request.method !== 'POST') return reply({ detail: 'Ruta no disponible.' }, 405, { Allow: 'POST' });
  if (request.headers.get('origin') !== new URL(request.url).origin) {
    return reply({ detail: 'Origen no permitido.' }, 403);
  }
  if (request.headers.get('content-type')?.split(';')[0].trim() !== 'application/json') {
    return reply({ detail: 'Se requiere JSON.' }, 415);
  }

  let auth;
  let assistant;
  try {
    auth = safeUpstream(options.authUpstream, options.development === true);
    assistant = safeUpstream(options.assistantUpstream, options.development === true);
  } catch {
    return reply({ detail: 'Conexión pendiente de configurar.' }, 503);
  }

  let bytes: Uint8Array;
  try {
    bytes = await readLimited(new Response(request.body), MAX_REQUEST_BYTES);
  } catch {
    return reply({ detail: 'La pregunta es demasiado larga.' }, 413);
  }
  let pregunta: string;
  try {
    const body = JSON.parse(new TextDecoder().decode(bytes));
    pregunta = typeof body?.pregunta === 'string' ? body.pregunta.trim() : '';
  } catch {
    pregunta = '';
  }
  if (!pregunta || pregunta.length > 2000) {
    return reply({ detail: 'Escribe una pregunta de entre 1 y 2000 caracteres.' }, 400);
  }

  const fetcher = options.fetcher ?? fetch;
  const cookie = sessionCookie(request);
  const headers = new Headers({ Accept: 'application/json' });
  if (cookie) headers.set('Cookie', cookie);
  try {
    const sessionResponse = await fetcher(new URL('/session', auth.upstream), {
      headers,
      cache: 'no-store',
      redirect: 'manual',
      signal: AbortSignal.timeout(8000),
    });
    if (!sessionResponse.ok) return reply({ detail: 'Inicia sesión para utilizar el asistente.' }, sessionResponse.status === 401 ? 401 : 503);
    const session = await sessionResponse.json();
    if (session.auth_required !== true && !auth.local) {
      return reply({ detail: 'El acceso seguro aún no está configurado.' }, 503);
    }
    if (session.authenticated !== true) {
      return reply({ detail: 'Inicia sesión para utilizar el asistente.' }, 401);
    }

    headers.set('Content-Type', 'application/json');
    const result = await fetcher(new URL('/api/asistente', assistant.upstream), {
      method: 'POST',
      headers,
      body: JSON.stringify({ pregunta }),
      cache: 'no-store',
      redirect: 'manual',
      signal: AbortSignal.timeout(90000),
    });
    if (result.status >= 300 && result.status < 400) return reply({ detail: 'La sesión ha caducado.' }, 401);
    if (result.status === 401 || result.status === 403) return reply({ detail: 'La sesión ha caducado.' }, 401);
    if (result.status === 429) return reply({ detail: 'Se alcanzó el límite de consultas. Espera un momento.' }, 429, { 'Retry-After': result.headers.get('retry-after') ?? '60' });
    if (!result.ok) return reply({ detail: 'El asistente no está disponible en este momento.' }, 502);
    if (!result.headers.get('content-type')?.includes('application/json')) {
      return reply({ detail: 'Respuesta no válida del asistente.' }, 502);
    }

    const responseBytes = await readLimited(result, MAX_RESPONSE_BYTES);
    const payload = JSON.parse(new TextDecoder().decode(responseBytes));
    if (typeof payload?.respuesta !== 'string' || !Array.isArray(payload?.imagenes_base64)
        || payload.imagenes_base64.length > 4
        || !payload.imagenes_base64.every((item: unknown) => typeof item === 'string' && /^[A-Za-z0-9+/=]*$/.test(item))) {
      return reply({ detail: 'Respuesta no válida del asistente.' }, 502);
    }
    return reply({ respuesta: payload.respuesta, imagenes_base64: payload.imagenes_base64 });
  } catch {
    return reply({ detail: 'No se pudo conectar con el asistente.' }, 503);
  }
}
