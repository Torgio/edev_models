"""Registro de lo que cada modelo predijo, hora a hora.

POR QUE UNA TABLA Y NO CSVs
Sin esto no hay forma de decir cuanto acierta el modelo EN PRODUCCION: solo tendriamos el
MAE del tramo de test, que es una foto congelada de enero-julio de 2026. Con la tabla cada
prediccion queda fechada y se contrasta contra el PMD real segun se publica, que es un
argumento bastante mas fuerte que un split.

La misma tabla sirve para el backfill del test y para el cron diario, asi que la web no
distingue entre una fila de hace seis meses y una de esta manana.

    python scripts/guardar_predicciones.py --crear-tabla
    python scripts/guardar_predicciones.py --backfill        # los 211 dias de test
    python scripts/guardar_predicciones.py --resumen

POR QUE `datetime` Y NO (fecha, hora)
Es la convencion de las otras 14 tablas del esquema, y ademas resuelve el cambio de hora:
en marzo el dia tiene 23 horas y en octubre 25, con la 02:00 repetida. Un timestamptz
distingue la 02:00+02:00 de la 02:00+01:00; un par (fecha, hora) no puede. La hora del dia
se saca con EXTRACT cuando hace falta, para no tener dos fuentes de verdad que puedan
discrepar.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO / "ingesta"))
sys.path.append(str(REPO / "scripts"))

SEMILLA = 42
TZ = "Europe/Madrid"

DDL = """
CREATE TABLE IF NOT EXISTS predictions (
    datetime     TIMESTAMPTZ NOT NULL,          -- la hora objetivo, como en spot_price
    pred_date    DATE        NOT NULL,          -- el dia D desde el que se predijo
    model        TEXT        NOT NULL,
    prediction   REAL        NOT NULL,
    seed         SMALLINT,                      -- NULL para el ensemble
    matrix       TEXT,
    matrix_hash  TEXT,
    -- 'test' = backfill del tramo de evaluacion; 'production' = prediccion del dia.
    -- Sin esto se mezclarian los dos MAE y el numero resultante no seria ninguno.
    source       TEXT        NOT NULL DEFAULT 'production',
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (datetime, model)
);
-- La PK (datetime, model) ya es un btree que sirve para filtrar por rango de fechas,
-- porque datetime es su primera columna: un indice suelto sobre datetime seria una copia
-- que solo cuesta escrituras. El que falta es el reciproco, para "dame todo lo de gru".
CREATE INDEX IF NOT EXISTS ix_predictions_model ON predictions (model, datetime);
"""


def conexion():
    from config import load_config
    import psycopg2
    _, db = load_config()
    return psycopg2.connect(**db)


def _carpeta():
    p = REPO / "production" / "models"
    return p if (p / "por_semilla.csv").exists() else REPO / "data" / "gold" / "finales_nucleo"


def representantes(carpeta):
    d = pd.read_csv(carpeta / "por_semilla.csv")
    return d.loc[d.groupby("familia").MAE_val.idxmin()].sort_values("MAE_val")


def filas_de_csv(ruta, model, seed, matrix, matrix_hash, source):
    """Un CSV de (dias x 24 h) -> filas largas con `datetime` en hora local.

    Devuelve (filas, descartadas). Las descartadas son horas que NO EXISTEN: el domingo
    de marzo el tensor sigue teniendo 24 posiciones porque es rectangular, pero la 02:00
    de ese dia no ocurrio. Insertarla desplazada la haria chocar con la 03:00 real.
    """
    d = pd.read_csv(ruta, index_col=0, parse_dates=True)
    largo = d.stack().reset_index()
    largo.columns = ["dia", "hora", "prediction"]
    largo["hora"] = largo.hora.str.lstrip("h").astype(int)

    naive = largo.dia + pd.to_timedelta(largo.hora, unit="h")
    largo["datetime"] = naive.dt.tz_localize(TZ, nonexistent="NaT", ambiguous="NaT")
    descartadas = int(largo.datetime.isna().sum())
    largo = largo.dropna(subset=["datetime"])

    # pred_date es el dia D, no el objetivo: el mercado cierra a las 12:00 de D para D+1 y
    # se predice a las 11:00 de D. Poner aqui la fecha objetivo la duplicaria con
    # `datetime` y perderia el unico dato que esta columna aporta -- desde cuando se sabia.
    un_dia = pd.Timedelta(days=1)
    return [(r.datetime.to_pydatetime(), (r.dia - un_dia).date(), model,
             float(r.prediction), seed, matrix, matrix_hash, source)
            for r in largo.itertuples()], descartadas


def insertar(con, filas):
    from psycopg2.extras import execute_values
    with con.cursor() as cur:
        execute_values(cur, """
            INSERT INTO predictions (datetime, pred_date, model, prediction,
                                     seed, matrix, matrix_hash, source)
            VALUES %s
            ON CONFLICT (datetime, model) DO UPDATE SET
                prediction  = EXCLUDED.prediction,
                pred_date   = EXCLUDED.pred_date,
                seed        = EXCLUDED.seed,
                matrix      = EXCLUDED.matrix,
                matrix_hash = EXCLUDED.matrix_hash,
                source      = EXCLUDED.source,
                updated_at  = now()
        """, filas, page_size=5000)
    con.commit()
    return len(filas)


def backfill(con, matrix="nucleo"):
    carpeta = _carpeta()
    meta = json.loads((carpeta / "meta.json").read_text(encoding="utf-8"))
    h = meta.get("hash")
    print(f"leyendo de {carpeta} · matriz {matrix} · hash {h}\n")

    # Los pred_test_*.csv NO estan en production/models a proposito: alli solo van los
    # modelos servibles, y esos CSV son material de evaluacion. Viven en la carpeta de
    # entrenamiento, asi que se busca en las dos.
    donde = [carpeta, REPO / "data" / "gold" / f"finales_{matrix}"]

    def buscar(nombre):
        return next((d / nombre for d in donde if (d / nombre).exists()), None)

    total = fuera = 0
    piezas = [(r.familia, int(r.semilla),
               buscar(f"pred_test_{r.familia}__s{int(r.semilla) - SEMILLA}.csv"))
              for r in representantes(carpeta).itertuples()]
    piezas.append(("ensemble", None, buscar("pred_test_ensemble.csv")))

    for model, seed, p in piezas:
        if p is None:
            # Callarse aqui daria una web con un modelo menos y nadie lo notaria.
            print(f"  {model:18s} sin pred_test en {' ni '.join(str(d) for d in donde)}")
            continue
        filas, desc = filas_de_csv(p, model, seed, matrix, h, "test")
        n = insertar(con, filas)
        total += n
        fuera += desc
        marca = f"  ({desc} horas inexistentes descartadas)" if desc else ""
        print(f"  {model:18s} {n:6,} filas{marca}")

    print(f"\n{total:,} filas insertadas o actualizadas")
    if fuera:
        print(f"{fuera} descartadas por caer en el hueco del cambio de hora de marzo")


def produccion(con, desde, hasta, matriz="produccion", verbose=True):
    """Predice con los modelos ya guardados y lo registra como `source='production'`.

    Una sola pasada: se cargan los 8 miembros una vez, se predice el rango entero y el
    ensemble sale de promediar lo mismo que ya esta en memoria. Cargar cada modelo por
    separado costaria ocho veces mas por el mismo resultado.

    El `source` los separa del backfill de test a proposito. Mezclarlos daria un MAE que no
    es ni el de evaluacion ni el real: en test los dias los eligio el reparto, aqui son
    simplemente los que han pasado.
    """
    from predecir import cargar
    p = cargar("ensemble", matriz)
    if verbose:
        print(f"{p}\n")

    det = p.predecir(desde=desde, hasta=hasta, tramo="todo", detalle=True)
    nombre = {str(m): m.familia for m in p.miembros}
    semilla = {str(m): m.semilla for m in p.miembros}

    hash_m = p.tensores().meta.get("hash")

    total = 0
    for clave, d in det.items():
        fam = nombre[clave]
        filas = _filas_de_frame(d, fam, semilla[clave], matriz, hash_m, "production")
        total += insertar(con, filas)
        if verbose:
            print(f"  {fam:18s} {len(filas):5,} filas")

    media = sum(d.values for d in det.values()) / len(det)
    ens = pd.DataFrame(media, index=next(iter(det.values())).index,
                       columns=next(iter(det.values())).columns)
    filas = _filas_de_frame(ens, "ensemble", None, matriz, hash_m, "production")
    total += insertar(con, filas)
    if verbose:
        print(f"  {'ensemble':18s} {len(filas):5,} filas")
        print(f"\n{total:,} filas de produccion insertadas o actualizadas")


def _filas_de_frame(d, model, seed, matrix, matrix_hash, source):
    """Mismo formato largo que `filas_de_csv`, pero desde un DataFrame ya en memoria."""
    largo = d.stack().reset_index()
    largo.columns = ["dia", "hora", "prediction"]
    largo["hora"] = largo.hora.str.lstrip("h").astype(int)
    naive = pd.to_datetime(largo.dia) + pd.to_timedelta(largo.hora, unit="h")
    largo["datetime"] = naive.dt.tz_localize(TZ, nonexistent="NaT", ambiguous="NaT")
    largo = largo.dropna(subset=["datetime"])
    un_dia = pd.Timedelta(days=1)
    return [(r.datetime.to_pydatetime(), (pd.Timestamp(r.dia) - un_dia).date(), model,
             float(r.prediction), seed, matrix, matrix_hash, source)
            for r in largo.itertuples()]


def resumen(con):
    with con.cursor() as cur:
        cur.execute("""
          SELECT p.model, p.source,
                 COUNT(*)                                     AS filas,
                 COUNT(DISTINCT p.datetime::date)             AS dias,
                 MIN(p.datetime)::date, MAX(p.datetime)::date,
                 ROUND(AVG(ABS(p.prediction - s.es_esios))::numeric, 3) AS mae,
                 COUNT(s.es_esios)                            AS con_real
          FROM predictions p
          LEFT JOIN spot_price s ON s.datetime = p.datetime
          GROUP BY 1, 2 ORDER BY mae NULLS LAST
        """)
        filas = cur.fetchall()
    if not filas:
        print("La tabla esta vacia. Lanza --backfill.")
        return
    print(f"\n  {'model':18s} {'source':11s} {'filas':>7s} {'dias':>5s}  "
          f"{'desde':10s} {'hasta':10s} {'MAE':>7s} {'con PMD':>8s}")
    print("  " + "-" * 88)
    for m, o, n, d, a, b, mae, cr in filas:
        print(f"  {m:18s} {o:11s} {n:7,} {d:5,}  {a}  {b}  "
              f"{mae if mae is not None else '--':>7} {cr:8,}")
    print("\n  El MAE cruza contra spot_price.es_esios por `datetime`, asi que solo cuenta")
    print("  las horas cuyo PMD real ya esta publicado.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--crear-tabla", action="store_true")
    ap.add_argument("--backfill", action="store_true",
                    help="carga los 211 dias de test desde los pred_test_*.csv")
    ap.add_argument("--resumen", action="store_true")
    ap.add_argument("--produccion", action="store_true",
                    help="predice con los modelos guardados y lo registra como production")
    ap.add_argument("--desde", help="primer dia objetivo (con --produccion)")
    ap.add_argument("--hasta", help="ultimo dia objetivo (con --produccion)")
    ap.add_argument("--matriz", default="nucleo")
    a = ap.parse_args()

    con = conexion()
    try:
        if a.crear_tabla or a.backfill:
            with con.cursor() as cur:
                cur.execute(DDL)
            con.commit()
            print("tabla `predictions` lista")
        if a.backfill:
            backfill(con, a.matriz)
        if a.produccion:
            with con.cursor() as cur:
                cur.execute(DDL)
            con.commit()
            produccion(con, a.desde, a.hasta,
                       matriz="produccion" if a.matriz == "nucleo" else a.matriz)
        if a.resumen or not (a.crear_tabla or a.backfill or a.produccion):
            resumen(con)
    finally:
        con.close()


if __name__ == "__main__":
    main()
