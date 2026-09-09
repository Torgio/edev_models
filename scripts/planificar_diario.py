"""Solo el paso 5 de `run_diario.py`: planificar la bateria con lo que ya predijo el cron de las 11:30.

QUE PROBLEMA RESUELVE
`bess_plan` esta vacia porque nadie la llena. `planificar()` existe desde hace dias, pero
vive dentro de `run_diario.py`, que ademas reconstruye la matriz y vuelve a predecir --
cosas que las 11:15 y las 11:30 ya hacen cada una en su propio cron. Colgar `run_diario.py`
entero como un cuarto paso repetiria ese trabajo solo para llegar al paso 5. Este script
hace UNICAMENTE eso: lee lo que `predictions` ya tiene para mañana y escribe el plan.

NO REIMPLEMENTA `planificar()` NI `campeon()`. Los importa de `run_diario`, para que un
cambio en como se decide el campeon o en como se reparte carga/descarga no haya que
mantenerlo en dos sitios -- el mismo motivo por el que `construir()` en `run_diario`
delega en `construir_matriz_produccion.py` en vez de reescribirlo.

CUANDO CORRE
    11:30  guardar_predicciones --equipo   escribe `predictions` de mañana
    ---------------------------------------------------------------------
    11:35  este script                      lee esas filas y planifica `bess_plan`

Cinco minutos de margen: lo mismo que separa la curva (11:45) de la prediccion (11:30),
para no llegar antes de que la fila exista.

    python scripts/planificar_diario.py --revisar           # que hay ya en predictions/bess_plan
    python scripts/planificar_diario.py --simulacro          # mira si hay prediccion suficiente, no escribe
    python scripts/planificar_diario.py                      # planifica con el campeon de `models`
    python scripts/planificar_diario.py --modelo lightgbm    # forzar otro modelo (pruebas)
    python scripts/planificar_diario.py --dia 2026-09-02     # rehacer un dia concreto
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
for p in ("scripts", "modelos", "ingesta"):
    sys.path.insert(0, str(REPO / p))

import run_diario as rd  # noqa: E402  -- de aqui salen campeon(), planificar() y revisar()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dia", help="el dia D de la pasada (por defecto hoy); se planifica D+1")
    ap.add_argument("--modelo", help="forzar un modelo en vez del campeon declarado en `models`")
    ap.add_argument("--simulacro", action="store_true",
                    help="mira si hay prediccion suficiente para planificar; no escribe nada")
    ap.add_argument("--revisar", action="store_true",
                    help="solo mira que hay ya en predictions y bess_plan; no escribe nada")
    a = ap.parse_args()

    hoy = pd.Timestamp(a.dia).date() if a.dia else date.today()
    objetivo = hoy + timedelta(days=1)

    from guardar_predicciones import conexion
    con = conexion()
    try:
        if a.revisar:
            rd.revisar(con, objetivo)
            return

        # `objetivo` no es decorativo: sin el, el respaldo devuelve un nombre fijo que
        # puede no estar escribiendose. Con el, comprueba contra la tabla y devuelve None
        # si ningun candidato tiene el dia completo.
        modelo = a.modelo or rd.campeon(con, objetivo)
        if modelo is None:
            print(f"  Ningun candidato tiene {objetivo} completo en `predictions`.")
            print("  Declara un campeon:  UPDATE models SET estado='campeon' WHERE model='...';")
            print("  o fuerza uno:        --modelo <nombre>")
            raise SystemExit(1)

        if a.simulacro:
            with con.cursor() as cur:
                cur.execute("""
                    SELECT count(*) FROM predictions
                     WHERE model = %s AND source = 'production'
                       AND (datetime AT TIME ZONE 'Europe/Madrid')::date = %s""",
                    (modelo, objetivo))
                n = cur.fetchone()[0]
            aviso = "  -- faltan para planificar (<23 horas)" if n < 23 else "  -- suficiente"
            rd._log("5 bateria", f"(simulacro) {modelo}: {n} horas de {objetivo} en predictions{aviso}")
            return

        rd.planificar(con, objetivo, modelo)
    finally:
        con.close()


if __name__ == "__main__":
    main()
