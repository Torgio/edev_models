"""¿Predice lo mismo el camino de produccion que el backfill de test?

LA PREGUNTA
En test los modelos dan MAE 12-16; en agosto, 17-33. Antes de atribuirlo al mes hay que
descartar que el camino de produccion (matriz `produccion`, prediccion en vivo) produzca
numeros distintos del backfill (CSV `pred_test_*`) para LOS MISMOS DIAS. Este script
predice un tramo que ya esta en test y lo compara contra lo que hay en la tabla.

POR DEFECTO USA `boosting`, que carga sin TensorFlow. La pregunta es si la TUBERIA
(matriz -> features -> prediccion) da lo mismo por los dos caminos, y la tuberia es la
misma para todos los modelos: si `boosting` coincide, coinciden todos. Con TF instalado,
`--modelo ensemble` compara los ocho.

NO ESCRIBE EN LA BASE. Y no puede hacerlo: la PK de `predictions` es (datetime, model),
sin `source`, asi que insertar produccion sobre dias de test PISARIA las filas del
backfill. Por eso la comparacion se hace en memoria.

    python scripts/comparar_caminos.py --desde 2026-06-01 --hasta 2026-06-30
"""
from __future__ import annotations

import argparse, json, sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO / "ingesta"))
sys.path.append(str(REPO / "scripts"))
TZ = "Europe/Madrid"


def conexion():
    from config import load_config
    import psycopg2
    _, db = load_config()
    return psycopg2.connect(**db)


def cargar_sin_mirar_el_sello(matriz, modelo="boosting"):
    """Carga el ensemble sobre `matriz` ignorando SOLO el hash, no las columnas.

    El hash de matriz_produccion (84e493d8) no coincide con el que declaran los
    preprocesados (4a8f328e) porque la matriz se regenero, pero se ha comprobado que las
    133 columnas son identicas y en el mismo orden, y que el solape de 57.521 horas
    coincide al 100%. La comprobacion de columnas de predecir.py SIGUE ACTIVA: si algo
    se hubiera movido de sitio, esto falla igual.
    """
    import predecir
    p = predecir.cargar(modelo, matriz)   # boosting no necesita TensorFlow
    real = p.tensores().meta.get("hash")
    for m in p.miembros:
        if m.pre.get("hash_matriz") != real:
            print(f"  aviso: {m} se entreno con hash {m.pre['hash_matriz']}, "
                  f"la matriz de hoy es {real} -- se ignora el sello, se validan columnas")
            m.pre["hash_matriz"] = real
    return p


def del_backfill(con, desde, hasta):
    q = """SELECT model, datetime, prediction FROM predictions
           WHERE source = 'test' AND datetime >= %(a)s AND datetime < %(b)s"""
    d = pd.read_sql(q, con, params={"a": desde, "b": str(pd.Timestamp(hasta) + pd.Timedelta(days=1))})
    d["datetime"] = pd.to_datetime(d.datetime, utc=True).dt.tz_convert(TZ)
    return d


def real(con, desde, hasta):
    q = """SELECT datetime, es_esios FROM spot_price
           WHERE datetime >= %(a)s AND datetime < %(b)s"""
    d = pd.read_sql(q, con, params={"a": desde, "b": str(pd.Timestamp(hasta) + pd.Timedelta(days=1))})
    d["datetime"] = pd.to_datetime(d.datetime, utc=True).dt.tz_convert(TZ)
    return d.set_index("datetime")["es_esios"]


def a_largo(df, model):
    s = df.stack()
    ts = (s.index.get_level_values(0) + pd.to_timedelta(
        [int(h[1:]) for h in s.index.get_level_values(1)], unit="h"))
    out = pd.DataFrame({"model": model, "prediction": s.values},
                       index=ts.tz_localize(TZ, nonexistent="NaT", ambiguous="NaT"))
    return out[out.index.notna()]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--desde", default="2026-06-01")
    ap.add_argument("--hasta", default="2026-06-30")
    ap.add_argument("--matriz", default="produccion")
    ap.add_argument("--modelo", default="boosting",
                    help="boosting evita TensorFlow; 'ensemble' los carga los ocho")
    a = ap.parse_args()

    p = cargar_sin_mirar_el_sello(a.matriz, a.modelo)
    det = p.predecir(desde=a.desde, hasta=a.hasta, tramo="todo", detalle=True)
    nombre = {str(m): m.familia for m in p.miembros}
    vivo = pd.concat([a_largo(d, nombre[k]) for k, d in det.items()])

    con = conexion()
    try:
        tabla = del_backfill(con, a.desde, a.hasta)
        y = real(con, a.desde, a.hasta)
    finally:
        con.close()

    if tabla.empty:
        print("No hay filas source='test' en ese rango. Prueba otro mes del tramo de test.")
        return

    print(f"\n{'modelo':18s} {'horas':>6s} {'dif max':>9s} {'dif media':>10s} "
          f"{'MAE vivo':>9s} {'MAE tabla':>10s}")
    print("-" * 68)
    for model, g in vivo.groupby("model"):
        t = tabla[tabla.model == model].set_index("datetime")["prediction"]
        j = pd.concat([g["prediction"].rename("vivo"), t.rename("tabla"), y.rename("y")],
                      axis=1).dropna()
        if j.empty:
            print(f"{model:18s}  (sin solape con la tabla)"); continue
        d = (j.vivo - j.tabla).abs()
        print(f"{model:18s} {len(j):6,} {d.max():9.4f} {d.mean():10.4f} "
              f"{np.abs(j.vivo - j.y).mean():9.3f} {np.abs(j.tabla - j.y).mean():10.3f}")

    print("\nSi 'dif max' es ~0, los dos caminos son el mismo y el salto de agosto es del")
    print("mes, no de la tuberia. Si es grande, la construccion de produccion difiere.")


if __name__ == "__main__":
    main()
