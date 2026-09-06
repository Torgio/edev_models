"""Sirve el boosting de nucleo (LightGBM / XGBoost) y lo registra en `predictions`.

POR QUE UN SCRIPT APARTE Y NO production/models/
Esa carpeta esta construida alrededor del tensor: sus modelos comen `vista_plana(T)`,
predicen el RESIDUO contra el naive y se destipifican con `preprocesado.json`. Estos
modelos son otra cosa -- una fila de la matriz por hora, `hora` como feature, precio
directo, sin estandarizar -- asi que meterlos alli con un preprocesado inventado seria
mentirle a un contrato que hoy nos ha salvado de servir numeros equivocados.

VENTAJA COLATERAL: al no estandarizar, aqui no hay escalador que pueda derivar cuando la
matriz gana dias. El contrato que se comprueba es el que de verdad importa para estos
modelos: que las columnas de entrada sean las mismas y en el mismo orden.

    python modelos/ML_Magui/servir.py --desde 2026-08-01 --hasta 2026-08-31
    python modelos/ML_Magui/servir.py --desde ... --hasta ... --guardar --source production
"""
from __future__ import annotations

import argparse, json, sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
AQUI = REPO / "modelos" / "ML_Magui"
sys.path.append(str(REPO / "ingesta"))
TZ = "Europe/Madrid"
CONTROL = ["fecha_pred", "fecha_objetivo", "ts", "split", "hora"]
TARGET = "target_price"


class ContratoRoto(RuntimeError):
    """La matriz de hoy no es aquella con la que se entreno el modelo."""


def cargar_matriz(matriz):
    f = REPO / "data" / "gold" / f"matriz_{matriz}.parquet"
    if not f.exists():
        f = f.with_suffix(".csv")
    d = pd.read_parquet(f) if f.suffix == ".parquet" else pd.read_csv(
        f, parse_dates=["fecha_pred", "fecha_objetivo", "ts"])
    for c in ("fecha_objetivo", "ts"):
        d[c] = pd.to_datetime(d[c])
    return d, json.loads((REPO / "data" / "gold" / f"matriz_{matriz}.meta.json")
                         .read_text(encoding="utf-8"))


def comprobar(meta_modelo, columnas, meta_matriz, quien):
    """Las columnas de entrada tienen que ser las mismas Y en el mismo orden."""
    esperado, actual = list(meta_modelo["features"]), list(columnas)
    if esperado != actual:
        if len(esperado) != len(actual):
            det = f"esperaba {len(esperado)} columnas, la matriz trae {len(actual)}"
        else:
            i = next(i for i, (a, b) in enumerate(zip(esperado, actual)) if a != b)
            det = f"columna {i} cambiada de sitio ({esperado[i]} -> {actual[i]})"
        raise ContratoRoto(
            f"{quien} no casa con esta matriz: {det}.\n"
            "  Reentrena, o usa la matriz con la que se entreno. Predecir igualmente "
            "devolveria numeros plausibles y equivocados.")
    h_mod, h_hoy = meta_modelo.get("hash_matriz"), meta_matriz.get("hash")
    if h_mod != h_hoy:
        print(f"  aviso · {quien}: la matriz no es la del entrenamiento "
              f"({h_mod} -> {h_hoy}); si solo se le han anadido dias, es lo esperado",
              file=sys.stderr)


def cargar_modelo(algoritmo, s):
    if algoritmo == "lightgbm":
        import lightgbm as lgb
        return lgb.Booster(model_file=str(AQUI / "artefactos" / f"lightgbm__s{s}.txt"))
    import xgboost as xgb
    m = xgb.XGBRegressor()
    m.load_model(str(AQUI / "artefactos" / f"xgboost__s{s}.json"))
    return m


def predecir(algoritmo, semilla, df, meta_modelo, meta_matriz):
    s = meta_modelo["semillas"].index(semilla)
    modelo = cargar_modelo(algoritmo, s)
    cols = [c for c in df.columns if c not in CONTROL + [TARGET]]
    comprobar(meta_modelo, cols, meta_matriz, f"{algoritmo}__s{s}")
    p = modelo.predict(df[cols])
    return pd.DataFrame({"fecha_objetivo": df.fecha_objetivo.values,
                         "hora": df.hora.values, "prediction": np.asarray(p)})


def a_filas(pred, model, seed, matrix, matrix_hash, source):
    """Mismo formato largo que scripts/guardar_predicciones.py."""
    naive = pred.fecha_objetivo + pd.to_timedelta(pred.hora, unit="h")
    dt = naive.dt.tz_localize(TZ, nonexistent="NaT", ambiguous="NaT")
    fuera = int(dt.isna().sum())
    ok = dt.notna()
    un_dia = pd.Timedelta(days=1)
    filas = [(t.to_pydatetime(), (f - un_dia).date(), model, float(v),
              seed, matrix, matrix_hash, source)
             for t, f, v in zip(dt[ok], pred.fecha_objetivo[ok], pred.prediction[ok])]
    return filas, fuera


def insertar(filas):
    from config import load_config
    import psycopg2
    from psycopg2.extras import execute_values
    _, db = load_config()
    con = psycopg2.connect(**db)
    try:
        with con.cursor() as cur:
            execute_values(cur, """
                INSERT INTO predictions (datetime, pred_date, model, prediction,
                                         seed, matrix, matrix_hash, source)
                VALUES %s
                ON CONFLICT (datetime, model) DO UPDATE SET
                    prediction=EXCLUDED.prediction, pred_date=EXCLUDED.pred_date,
                    seed=EXCLUDED.seed, matrix=EXCLUDED.matrix,
                    matrix_hash=EXCLUDED.matrix_hash, source=EXCLUDED.source,
                    updated_at=now()
            """, filas, page_size=5000)
        con.commit()
    finally:
        con.close()
    return len(filas)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--desde", required=True)
    ap.add_argument("--hasta", required=True)
    ap.add_argument("--matriz", default="produccion")
    ap.add_argument("--algoritmos", default="lightgbm,xgboost")
    ap.add_argument("--source", default="production", choices=["production", "test"])
    ap.add_argument("--guardar", action="store_true", help="sin esto no toca la base")
    a = ap.parse_args()

    if a.guardar:
        # La PK de `predictions` es (datetime, model) y NO incluye `seed`. Este bucle
        # escribe las tres semillas con el mismo `model`, asi que colisionan entre si:
        # cada INSERT dispara el DO UPDATE y en la tabla solo queda la ULTIMA semilla,
        # sin un solo error por pantalla. `scripts/run_diario.py` no usa este main: sirve
        # un representante por algoritmo, elegido por MAE de validacion.
        print("OJO: se van a escribir todas las semillas con el mismo `model`.\n"
              "     Como la PK no incluye `seed`, solo sobrevivira la ultima.\n"
              "     Para produccion usa `python scripts/run_diario.py`, que sirve\n"
              "     un representante por algoritmo.\n")

    if a.source == "test":
        print("OJO: la PK de predictions es (datetime, model) y no incluye source.\n"
              "     Escribir 'test' sobre dias que ya tienen backfill los PISA.\n")

    df, meta_matriz = cargar_matriz(a.matriz)
    sel = df[(df.fecha_objetivo >= a.desde) & (df.fecha_objetivo <= a.hasta)].copy()
    if sel.empty:
        print(f"La matriz '{a.matriz}' no tiene dias entre {a.desde} y {a.hasta} "
              f"(llega hasta {df.fecha_objetivo.max():%Y-%m-%d})."); return
    print(f"matriz {a.matriz} · hash {meta_matriz.get('hash')} · "
          f"{sel.fecha_objetivo.nunique()} dias · {len(sel):,} horas\n")

    total = 0
    for algo in a.algoritmos.split(","):
        meta = json.loads((AQUI / f"metadata_{algo}.json").read_text(encoding="utf-8"))
        for semilla in meta["semillas"]:
            pred = predecir(algo, semilla, sel, meta, meta_matriz)
            filas, fuera = a_filas(pred, algo, semilla, a.matriz,
                                   meta_matriz.get("hash"), a.source)
            marca = f"  ({fuera} horas inexistentes descartadas)" if fuera else ""
            if a.guardar:
                n = insertar(filas); total += n
                print(f"  {algo:10s} s{semilla}  {n:6,} filas escritas{marca}")
            else:
                print(f"  {algo:10s} s{semilla}  {len(filas):6,} filas "
                      f"(media {pred.prediction.mean():6.2f} EUR/MWh){marca}")

    if not a.guardar:
        print("\nEnsayo: no se ha escrito nada. Anade --guardar cuando lo veas bien.")
    else:
        print(f"\n{total:,} filas en `predictions` con source='{a.source}'")


if __name__ == "__main__":
    main()
