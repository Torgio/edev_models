/**
 * Widget del asistente del proyecto, listo para pegar en la app de Next.js de Pulso Energía.
 *
 * QUÉ ES
 * Un componente cliente autocontenido que llama a `POST /api/asistente` (el endpoint ya
 * desplegado en production/api/main.py, mismo servidor que /api/bat/*) y muestra la
 * respuesta en texto, con las gráficas que el asistente genere (si las genera).
 *
 * NO tiene ninguna marca de Claude/Anthropic visible -- solo dice "Asistente del proyecto",
 * igual que el widget que ya corre en el panel de predicciones (production/api/static/index.html).
 *
 * POR QUÉ TODAVÍA NO FUNCIONA EN CHATGPT.SITE
 * El endpoint reutiliza la sesión de Pulso (misma cookie, `auth_request` en nginx) -- y esa
 * cookie es `samesite=strict`: solo viaja si esta pantalla se sirve desde el MISMO dominio
 * que el resto del sitio (el VPS, no chatgpt.site). En cuanto esta app de Next.js se despliegue
 * ahí, el fetch de abajo funciona sin tocar nada más: es una llamada relativa (`/api/asistente`),
 * mismo origen, la cookie viaja sola.
 *
 * ESTILOS: usa solo las utilidades de Tailwind ya mapeadas a tus tokens en `@theme inline`
 * (bg-background, text-foreground, bg-primary, border-border, rounded-lg...) -- ningún color
 * a ojo, hereda tu paleta automáticamente. Ver docs/notas_memoria_tfm.md nota 44/45.
 *
 * USO
 *   import { AsistenteWidget } from "@/components/AsistenteWidget"  // o donde prefieras
 *   <AsistenteWidget />
 */
"use client";

import { useState } from "react";

type RespuestaAsistente = {
  respuesta: string;
  imagenes_base64: string[];
};

function escaparHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// Markdown minimo, sin dependencias -- el asistente devuelve texto con tablas, negritas y
// encabezados de markdown (ver modelos/asistente/chat.py). Se vio en producción que sin esto
// las tablas salían como pipes en crudo (nota 47/49 en notas_memoria_tfm.md): esto es el mismo
// renderizador que ya se aplicó en production/api/static/index.html, portado a TSX. No es un
// parser de markdown completo a propósito: es justo lo que el asistente genera, ni más ni menos.
function renderizarMarkdown(texto: string): string {
  const lineas = texto.split("\n");
  let html = "";
  let parrafo: string[] = [];
  let filaTabla: string[] = [];

  const negrita = (s: string) => s.replace(/\*\*(.+?)\*\*/g, "<strong class=\"text-foreground\">$1</strong>");

  const cerrarParrafo = () => {
    if (!parrafo.length) return;
    // La imagen ya se entrega aparte (imagenes_base64) -- si el modelo igual escribe una
    // referencia markdown de imagen, no apunta a nada real, se descarta.
    const sinImagenes = parrafo.join(" ").replace(/!\[[^\]]*\]\([^)]*\)/g, "").trim();
    parrafo = [];
    if (!sinImagenes) return;
    html += `<p class="mb-2 last:mb-0">${negrita(escaparHtml(sinImagenes))}</p>`;
  };
  const cerrarTabla = () => {
    if (!filaTabla.length) return;
    const sepRe = /^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?$/;
    const filas = filaTabla.filter((f) => !sepRe.test(f));
    const celdas = (l: string) => l.replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
    const [cab, ...resto] = filas.map(celdas);
    filaTabla = [];
    if (!cab) return;
    html +=
      '<div class="overflow-x-auto my-2"><table class="w-full text-sm border-collapse">' +
      '<thead><tr>' +
      cab.map((c) => `<th class="text-left px-2 py-1.5 border-b border-border text-muted-foreground font-semibold">${negrita(escaparHtml(c))}</th>`).join("") +
      "</tr></thead><tbody>" +
      resto.map((f) => "<tr>" + f.map((c) => `<td class="px-2 py-1.5 border-b border-border">${negrita(escaparHtml(c))}</td>`).join("") + "</tr>").join("") +
      "</tbody></table></div>";
  };

  for (const linea of lineas) {
    const l = linea.trim();
    if (l.startsWith("|")) { cerrarParrafo(); filaTabla.push(l); continue; }
    cerrarTabla();
    if (!l) { cerrarParrafo(); continue; }
    const encabezado = l.match(/^(#{1,3})\s+(.*)/);
    if (encabezado) {
      cerrarParrafo();
      const nivel = encabezado[1].length;
      const tam = nivel === 1 ? "text-base" : nivel === 2 ? "text-[15px]" : "text-sm";
      html += `<h${nivel} class="${tam} font-semibold text-foreground mt-3 mb-1.5 first:mt-0">${negrita(escaparHtml(encabezado[2]))}</h${nivel}>`;
      continue;
    }
    if (l.startsWith(">")) {
      cerrarParrafo();
      const txt = l.replace(/^>\s?/, "");
      html += `<blockquote class="border-l-2 border-border pl-3 my-2 text-muted-foreground text-sm">${negrita(escaparHtml(txt))}</blockquote>`;
      continue;
    }
    parrafo.push(l);
  }
  cerrarParrafo();
  cerrarTabla();
  return html || escaparHtml(texto);
}

const SUGERENCIAS = [
  "¿Cuántas horas de precio negativo ha habido este año?",
  "Los precios de hoy por hora, en tabla",
  "¿Cómo funciona la curva de precio a 20 años?",
];

export function AsistenteWidget() {
  const [pregunta, setPregunta] = useState("");
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resultado, setResultado] = useState<RespuestaAsistente | null>(null);

  async function preguntar(texto: string) {
    const q = texto.trim();
    if (!q || cargando) return;

    setCargando(true);
    setError(null);
    setResultado(null);

    try {
      const r = await fetch("/api/asistente", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // credentials: "same-origin" (el default) es lo correcto aquí -- la cookie de
        // sesión de Pulso viaja sola porque el fetch es del mismo origen.
        body: JSON.stringify({ pregunta: q }),
      });

      // nginx no devuelve un 401 limpio para una sesion caducada: redirige (302) a la
      // pantalla de login en HTML (ver /_auth_tfm en nginx-tfm.conf) -- fetch() sigue esa
      // redireccion sola y aqui llega un 200 con HTML, no JSON. Se detecta por el
      // content-type antes de intentar parsear, para no confundir "sesion caducada" con
      // "el asistente fallo".
      const esJson = r.headers.get("content-type")?.includes("application/json");
      if (!esJson) {
        setError("Tu sesión ha caducado. Recarga la página para volver a entrar.");
        return;
      }
      if (!r.ok) {
        const detalle = await r.json().catch(() => null);
        setError(detalle?.detail ?? `El asistente no pudo responder (error ${r.status}).`);
        return;
      }

      const datos: RespuestaAsistente = await r.json();
      setResultado(datos);
    } catch {
      setError("No se pudo contactar con el asistente. Inténtalo de nuevo en un momento.");
    } finally {
      setCargando(false);
    }
  }

  return (
    <div className="bg-background border border-border rounded-lg p-6 space-y-4">
      <div>
        <h3 className="text-foreground text-lg font-semibold tracking-tight">
          Asistente del proyecto
        </h3>
        <p className="text-muted-foreground text-sm mt-1">
          Pregunta sobre precios, baterías o cómo funciona el sistema — respuestas basadas
          siempre en los datos reales del proyecto, nunca inventadas.
        </p>
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          preguntar(pregunta);
        }}
        className="flex gap-2"
      >
        <input
          value={pregunta}
          onChange={(e) => setPregunta(e.target.value)}
          placeholder="Escribe tu pregunta..."
          disabled={cargando}
          className="flex-1 h-11 rounded-lg border border-border bg-white px-3 text-sm text-foreground
                     placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring
                     disabled:opacity-60"
        />
        <button
          type="submit"
          disabled={cargando || !pregunta.trim()}
          className="h-11 px-5 rounded-lg bg-primary text-primary-foreground text-sm font-semibold
                     disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {cargando ? "Pensando…" : "Preguntar"}
        </button>
      </form>

      {!resultado && !cargando && !error && (
        <div className="flex flex-wrap gap-2">
          {SUGERENCIAS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => {
                setPregunta(s);
                preguntar(s);
              }}
              className="text-xs px-3 py-1.5 rounded-full bg-secondary text-secondary-foreground
                         border border-border hover:bg-muted transition-colors"
            >
              {s}
            </button>
          ))}
        </div>
      )}

      {cargando && (
        <div className="flex items-center gap-2 text-muted-foreground text-sm py-2">
          <span
            className="inline-block h-4 w-4 rounded-full border-2 border-border border-t-primary animate-spin"
            aria-hidden="true"
          />
          Consultando los datos del proyecto…
        </div>
      )}

      {error && (
        <div className="text-sm rounded-lg border border-border bg-secondary text-foreground px-4 py-3">
          {error}
        </div>
      )}

      {resultado && (
        <div className="space-y-3">
          <div
            className="text-sm text-foreground leading-relaxed rounded-lg bg-white border border-border px-4 py-3"
            // El texto se escapa a mano dentro de renderizarMarkdown() antes de convertirlo a
            // HTML -- nunca se inyecta el texto del asistente sin pasar por escaparHtml() primero.
            dangerouslySetInnerHTML={{ __html: renderizarMarkdown(resultado.respuesta) }}
          />

          {resultado.imagenes_base64.map((b64, i) => (
            // eslint-disable-next-line @next/next/no-img-element -- base64 generada en tiempo
            // real por el asistente, no un asset estatico que Next deba optimizar.
            <img
              key={i}
              src={`data:image/png;base64,${b64}`}
              alt={`Gráfica generada por el asistente (${i + 1})`}
              className="rounded-lg border border-border max-w-full"
            />
          ))}
        </div>
      )}
    </div>
  );
}
