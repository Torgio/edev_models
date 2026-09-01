"""La cadena diaria: reconstruir la matriz y republicar la curva. Para el cron.

POR QUE LOS DOS PASOS Y EN ESTE ORDEN
La curva lee `matriz_produccion`, y de ella saca dos cosas que caducan:

  LAS ANCLAS. El gas, la demanda y la capacidad del escenario salen del ULTIMO AÑO
  OBSERVADO en la matriz. Con la matriz parada, se quedan en la fecha en que se paro y la
  curva envejece sin que nada avise -- sigue generandose, sigue teniendo buena pinta, y cada
  dia se parece menos a la realidad.

  EL ARRANQUE. El tramo simulado empieza en `ultimo_dia + 1`. Con la matriz de ayer, la
  curva no cubre mañana, y un caso que pida mañana se queda sin precio.

Por eso la matriz va primero y siempre. Si falla, la curva NO se republica: mas vale una
curva de ayer, con su fecha bien puesta, que una de hoy construida sobre datos de la semana
pasada creyendo que son de hoy.

CADA CUANTO
A diario. No porque las anclas se muevan tanto -- son medias del año en curso y apenas se
mueven en un dia -- sino por el arranque: la curva tiene que cubrir mañana.

CUANDO
De madrugada, despues de que los crons de ingesta hayan traido el dia anterior y antes de la
prediccion de las 11:00, que usa la misma matriz.

    30 3 * * *  /home/ubuntu/tfm-env/bin/python -u \\
                /home/ubuntu/scripts/production/curva/cron_diario.py \\
                >> /home/ubuntu/scripts/logs/cron_curva.log 2>&1

El `-u` es imprescindible: sin el, Python acumula la salida al redirigir a fichero y el log
parece vacio durante minutos aunque el proceso este trabajando.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = sys.executable


def paso(nombre: str, orden: list[str], timeout=3600) -> bool:
    """Ejecuta un paso y cuenta lo que tardo. Devuelve si fue bien."""
    print(f"\n{'='*70}\n  {nombre}\n  {' '.join(orden[1:])}\n{'='*70}", flush=True)
    t0 = time.time()
    try:
        r = subprocess.run(orden, cwd=REPO, timeout=timeout,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
    except subprocess.TimeoutExpired:
        print(f"  ABORTADO por tiempo ({timeout}s)", flush=True)
        return False
    print(r.stdout[-4000:] if r.stdout else "", flush=True)
    if r.returncode:
        print(f"  FALLO (codigo {r.returncode})", flush=True)
        print((r.stderr or "")[-2500:], flush=True)
        return False
    print(f"  ok · {time.time()-t0:.0f}s", flush=True)
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--escenarios", type=int, default=50)
    ap.add_argument("--hasta-ano", type=int, default=2046)
    ap.add_argument("--solo-curva", action="store_true",
                    help="saltar la matriz; solo si se acaba de reconstruir a mano")
    ap.add_argument("--sin-registrar", action="store_true",
                    help="publicar el .npy pero no escribir en app_curve_run")
    a = ap.parse_args()

    manana = date.today() + timedelta(days=1)
    print(f"\ncadena diaria de la curva · {date.today():%Y-%m-%d}")

    if not a.solo_curva:
        ok = paso("1/2 · reconstruir la matriz de produccion",
                  [PY, "-u", "scripts/construir_matriz_produccion.py",
                   "--hasta", str(manana)])
        if not ok:
            # La curva NO se republica sobre una matriz vieja. Una curva de ayer con su
            # fecha bien puesta es mucho mejor que una de hoy construida sobre datos de la
            # semana pasada: la segunda miente y no lo parece.
            print("\n  la matriz ha fallado: NO se republica la curva.", flush=True)
            print("  Queda la anterior, con su fecha, que es lo correcto.", flush=True)
            raise SystemExit(1)

    orden = [PY, "-u", "production/curva/generar_curva.py",
             "--escenarios", str(a.escenarios), "--hasta", str(a.hasta_ano)]
    if not a.sin_registrar:
        orden.append("--registrar")
    if not paso("2/2 · republicar la curva a 20 años", orden):
        raise SystemExit(1)

    print(f"\n  cadena completa · {date.today():%Y-%m-%d}", flush=True)


if __name__ == "__main__":
    main()
