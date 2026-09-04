"""¿Que parte de la cadena diaria esta viva y que parte lleva dias parada?

POR QUE HACE FALTA
La cadena son cuatro procesos que escriben en tablas distintas, y ninguno avisa cuando deja
de correr. Un cron que no esta instalado no falla: simplemente no pasa nada, y lo unico que
se ve es una tabla que "no carga datos" -- sin error, sin log, sin pista de cual de los
cuatro es el que falta.

Esto mira las cuatro tablas y dice de que dia es la ultima fila de cada una. Con eso el
diagnostico es inmediato: si `predictions` esta fresca y `bess_result` no, falta la
evaluacion; si las dos estan paradas, falta la pasada diaria; si `spot_price` tambien esta
parada, el problema es de ingesta y no nuestro.

    python scripts/estado_cadena.py
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
for p in ("scripts", "ingesta"):
    sys.path.insert(0, str(REPO / p))

TZ = "Europe/Madrid"

# (etiqueta, tabla, columna de fecha, quien la escribe, cada cuanto deberia moverse)
FUENTES = [
    ("spot_price (la verdad)", "spot_price",
     "(datetime AT TIME ZONE 'Europe/Madrid')::date", "cron de ingesta", 0),
    ("predictions · test", "predictions WHERE source='test'",
     "(datetime AT TIME ZONE 'Europe/Madrid')::date", "backfill, no se mueve", None),
    ("predictions · production", "predictions WHERE source='production'",
     "(datetime AT TIME ZONE 'Europe/Madrid')::date", "guardar_predicciones 11:30", -1),
    ("bess_plan", "bess_plan",
     "(datetime AT TIME ZONE 'Europe/Madrid')::date", "run_diario paso 5", -1),
    ("bess_result", "bess_result", "fecha_objetivo", "evaluar_diario 13:30", 0),
]


def main():
    from guardar_predicciones import conexion
    con = conexion()
    hoy = date.today()
    print(f"\n  hoy es {hoy}\n")
    print(f"    {'tabla':26s} {'ultimo dia':>11s} {'retraso':>9s} {'filas':>9s}  lo escribe")
    print("    " + "-" * 86)
    estado = {}
    try:
        for etiqueta, tabla, col, quien, esperado in FUENTES:
            unido = "AND" if "WHERE" in tabla else "WHERE"
            with con.cursor() as cur:
                cur.execute(f"SELECT max({col}), count(*) FROM {tabla} {unido} {col} IS NOT NULL")
                ultimo, n = cur.fetchone()
            if ultimo is None:
                print(f"    {etiqueta:26s} {'VACIA':>11s} {'':>9s} {0:9,}  {quien}")
                estado[etiqueta] = None
                continue
            dias = (hoy - ultimo).days
            estado[etiqueta] = ultimo
            marca = ""
            if esperado is not None:
                # `esperado` es el desfase normal: 0 = hasta ayer/hoy, -1 = llega a manana
                if dias > esperado + 1:
                    marca = "  <-- PARADA"
            print(f"    {etiqueta:26s} {str(ultimo):>11s} {dias:>6d} d {n:9,}  {quien}{marca}")
    finally:
        con.close()

    pred = estado.get("predictions · production")
    plan, res = estado.get("bess_plan"), estado.get("bess_result")
    precio = estado.get("spot_price (la verdad)")
    manana = hoy + timedelta(days=1)
    print()

    # La prediccion esta al dia si llega a MAÑANA. Compararla contra `spot_price` no vale:
    # despues de las 13:00 el precio tambien llega a mañana, y entonces la comprobacion
    # daba por parada una cadena que estaba corriendo perfectamente.
    if pred is None or pred < manana:
        print(f"  La prediccion no llega a {manana}: la pasada de hoy no ha corrido.")
        print("      python scripts/run_diario.py")
    else:
        print(f"  La prediccion llega a {manana}: la cadena de la mañana esta viva.")

    if pred and (plan is None or plan < pred):
        print(f"  Hay prediccion hasta {pred} y plan de bateria solo hasta {plan}:")
        print("  nadie esta escribiendo `bess_plan`. Es el paso 5, que vive en esta pasada")
        print("  y no en la cadena instalada en el servidor.")
        print("      python scripts/run_diario.py --solo-bateria")

    # Solo se puede evaluar hasta donde llega el precio publicado.
    hasta_evaluable = min(x for x in (pred, precio) if x) if pred and precio else None
    if hasta_evaluable and (res is None or res < hasta_evaluable):
        print(f"  Se puede evaluar hasta {hasta_evaluable} y hay resultados solo hasta {res}:")
        print("  falta la evaluacion.")
        print("      python scripts/evaluar_diario.py")
    print()


if __name__ == "__main__":
    main()
