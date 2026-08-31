r"""
TFM Energia UCM - Ver el historial de preguntas al asistente, desde terminal (31-ago-2026)

Lee `historial.jsonl` (una linea JSON por pregunta, escrita automaticamente por `chat.py` en
cada llamada a `preguntar`/`preguntar_con_imagenes`) y lo imprime de forma legible. Es local a
esta maquina, no esta en git -- cada persona ve solo lo que ha preguntado ella misma.

Por que no la consola de Anthropic: la pestaña "Usage" de Claude Console muestra tokens y coste
agregado por clave de API y modelo, pero NO el contenido de las preguntas ni respuestas -- por
diseño de privacidad, Anthropic no expone el texto de las llamadas API ahi. Si se quiere ver
QUE se preguntó, tiene que venir de un registro propio como este.

Uso:
    .venv\Scripts\python.exe modelos/asistente/ver_historial.py           # ultimas 10
    .venv\Scripts\python.exe modelos/asistente/ver_historial.py -n 30     # ultimas 30
    .venv\Scripts\python.exe modelos/asistente/ver_historial.py --todo    # todo el historial
"""
import argparse
import json
import sys
from pathlib import Path

HISTORIAL_PATH = Path(__file__).parent / "historial.jsonl"

# precio de referencia, EUR / 1M tokens (entrada, salida) -- 30-ago-2026, solo para hacerse una
# idea del gasto en el terminal; el coste real y exacto se ve en Claude Console > Usage.
PRECIOS = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def _coste_estimado(modelo: str, tokens_entrada: int, tokens_salida: int) -> float | None:
    precio = PRECIOS.get(modelo)
    if precio is None:
        return None
    entrada, salida = precio
    return tokens_entrada / 1_000_000 * entrada + tokens_salida / 1_000_000 * salida


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("-n", type=int, default=10, help="cuantas preguntas mostrar (mas recientes)")
    ap.add_argument("--todo", action="store_true", help="mostrar el historial completo")
    a = ap.parse_args()

    if not HISTORIAL_PATH.exists():
        print(f"Todavia no hay historial ({HISTORIAL_PATH} no existe) -- haz una pregunta primero.")
        return

    lineas = [json.loads(l) for l in HISTORIAL_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not a.todo:
        lineas = lineas[-a.n:]

    coste_total = 0.0
    for r in lineas:
        coste = _coste_estimado(r["modelo"], r["tokens_entrada"], r["tokens_salida"])
        if coste is not None:
            coste_total += coste
        print(f"\n[{r['fecha']}] modelo={r['modelo']}  "
              f"tokens={r['tokens_entrada']}in/{r['tokens_salida']}out"
              + (f"  ~{coste:.4f} EUR" if coste is not None else "")
              + (f"  ({r['n_imagenes']} imagen(es))" if r.get("n_imagenes") else ""))
        print(f"  P: {r['pregunta']}")
        respuesta = r["respuesta"].replace("\n", " ")
        print(f"  R: {respuesta[:300]}{'...' if len(respuesta) > 300 else ''}")

    print(f"\n{len(lineas)} pregunta(s) mostrada(s) -- coste estimado de este tramo: "
          f"~{coste_total:.4f} EUR (aproximado, ver Claude Console > Usage para el coste real).")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
