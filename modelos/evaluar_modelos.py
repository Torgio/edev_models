"""Leaderboard unico: todos los modelos, las mismas metricas, el mismo codigo.

POR QUE EXISTE
Cada bloque del equipo guarda sus predicciones a su manera y calcula la captura de
arbitraje con sus propios supuestos. El resultado es que dos personas obtienen numeros
distintos para el MISMO baseline (la persistencia sale 86.5% en un notebook y 81% en
otro). Mientras eso pase, las tablas no se pueden comparar entre si.

Aqui las predicciones entran en cualquiera de los dos formatos y las metricas las calcula
UNA sola funcion para todos.

    python modelos/evaluar_modelos.py                 # validacion
    python modelos/evaluar_modelos.py --tramo test

Salida: data_temp/leaderboard_<tramo>.csv y el mismo cuadro por pantalla.
"""
from __future__ import annotations

import argparse
import glob
import os
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
MATRIZ = REPO / "data" / "gold" / "matriz_nucleo.csv"

# --- SUPUESTOS DE LA BATERIA -------------------------------------------------------
# Fijos y escritos aqui a proposito: son los de la radiografia del LightGBM afinado.
# Cambiarlos cambia TODAS las capturas a la vez, que es justo lo que se quiere.
POTENCIA_MW = 1.0
CAPACIDAD_MWH = 2.0
EFICIENCIA = 0.90          # ida y vuelta
HORAS = int(CAPACIDAD_MWH / POTENCIA_MW)   # 2 h de carga y 2 h de descarga


# ---------------------------------------------------------------- lectura de precios
def precio_real():
    m = pd.read_csv(MATRIZ, usecols=["ts", "target_price", "split"])
    m["ts"] = pd.to_datetime(m["ts"])
    return m.set_index("ts")


def a_largo(ruta, modelo_id):
    """Ancho (dia x h00..h23) -> Serie horaria. El formato de las redes."""
    d = pd.read_csv(ruta, index_col=0, parse_dates=True)
    d.columns = range(24)
    s = d.stack()
    ts = s.index.get_level_values(0) + pd.to_timedelta(s.index.get_level_values(1), "h")
    return pd.Series(s.values, index=ts, name=modelo_id)


def de_largo(ruta):
    """Largo (modelo_id, datetime_utc, precio_pred, p10, p90). El formato de la directriz."""
    d = pd.read_csv(ruta)
    d["ts"] = pd.to_datetime(d["datetime_utc"]).dt.tz_localize(None)
    d = d.set_index("ts")
    cols = ["precio_pred"] + [c for c in ("p10", "p90") if c in d and d[c].notna().any()]
    return d[cols]


# ------------------------------------------------------------------------ arbitraje
def arbitraje(pred, real):
    """Ingreso diario de la bateria decidiendo con `pred` y cobrando a precio `real`.

    Un ciclo al dia: se carga en las HORAS mas baratas PREDICHAS y se descarga en las
    HORAS mas caras PREDICHAS. El dinero se liquida siempre a precio real -- el modelo
    elige el cuando, el mercado pone el cuanto.
    """
    df = pd.DataFrame({"p": pred, "y": real}).dropna()
    df["dia"] = df.index.normalize()
    ing = []
    for dia, g in df.groupby("dia"):
        if len(g) < 24:
            continue
        orden = g.p.values.argsort()
        carga, descarga = orden[:HORAS], orden[-HORAS:]
        y = g.y.values
        ing.append({
            "dia": dia,
            "modelo": EFICIENCIA * y[descarga].sum() - y[carga].sum(),
            "oraculo": EFICIENCIA * np.sort(y)[-HORAS:].sum() - np.sort(y)[:HORAS].sum(),
        })
    d = pd.DataFrame(ing)
    if d.empty or d.oraculo.sum() == 0:
        return np.nan, np.nan
    return 100 * d.modelo.sum() / d.oraculo.sum(), d.modelo.sum() / len(d)


def pico_1h(pred, real):
    """% de dias en que la hora mas cara predicha cae a <=1 h de la real."""
    df = pd.DataFrame({"p": pred, "y": real}).dropna()
    df["dia"] = df.index.normalize()
    ok = tot = 0
    for _, g in df.groupby("dia"):
        if len(g) < 24:
            continue
        tot += 1
        ok += abs(int(g.p.values.argmax()) - int(g.y.values.argmax())) <= 1
    return 100 * ok / tot if tot else np.nan


# -------------------------------------------------------------------------- metricas
def metricas(pred, real, p10=None, p90=None, mae_ref=None):
    j = pd.DataFrame({"p": pred, "y": real}).dropna()
    if j.empty:
        return None
    e = j.p - j.y
    den = (j.p.abs() + j.y.abs()) / 2
    captura, eur_dia = arbitraje(j.p, j.y)
    m = {
        "n_horas": len(j),
        "MAE": np.abs(e).mean(),
        "RMSE": np.sqrt((e ** 2).mean()),
        "sMAPE": 100 * (np.abs(e) / den.replace(0, np.nan)).mean(),
        "captura_%": captura,
        "eur_dia": eur_dia,
        "pico_1h_%": pico_1h(j.p, j.y),
    }
    if mae_ref:
        m["skill_%"] = 100 * (1 - m["MAE"] / mae_ref)
    if p10 is not None and p90 is not None:
        q = pd.DataFrame({"lo": p10, "hi": p90, "y": real}).dropna()
        if len(q):
            m["cobertura_IC80_%"] = 100 * ((q.y >= q.lo) & (q.y <= q.hi)).mean()
    return m


# ------------------------------------------------------------------------ inventario
def inventario(tramo):
    """Todo lo que hay que evaluar, venga en el formato que venga."""
    sufijo = "val" if tramo == "validation" else "test"
    fuentes = []
    for r in sorted(glob.glob(str(REPO / "modelos/*/entregables/*/pred_val_2025.csv"))):
        if tramo == "validation":
            fuentes.append((os.path.basename(os.path.dirname(r)), "largo", r))
    for r in sorted(glob.glob(str(REPO / f"data/gold/finales_*/pred_{sufijo}_*.csv"))):
        mid = os.path.basename(r).replace(f"pred_{sufijo}_", "").replace(".csv", "")
        fuentes.append((mid, "ancho", r))
    return fuentes


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tramo", default="validation", choices=["validation", "test"])
    a = ap.parse_args()

    m = precio_real()
    real = m.loc[m.split.eq(a.tramo), "target_price"]
    print(f"\ntramo {a.tramo}: {len(real):,} horas · "
          f"{real.index.min():%Y-%m-%d} -> {real.index.max():%Y-%m-%d}\n")

    # referencias, calculadas aqui para que nadie las traiga de su notebook
    todo = m["target_price"]
    f = pd.DataFrame({"y": todo})
    f["dia"], f["h"] = f.index.normalize(), f.index.hour
    piv = f.pivot_table(index="dia", columns="h", values="y")
    # media de los 7 dias anteriores HORA A HORA (no una media plana del periodo)
    mm = piv.rolling(7, min_periods=7).mean().shift(1).stack()
    refs = {
        "naive_D1": todo.shift(freq=pd.Timedelta(days=1)),
        "media_movil_7d": pd.Series(
            mm.values,
            index=mm.index.get_level_values(0)
            + pd.to_timedelta(mm.index.get_level_values(1), "h")),
    }
    mae_naive = metricas(refs["naive_D1"], real)["MAE"]

    filas = []
    for mid, fmt, ruta in inventario(a.tramo):
        try:
            if fmt == "largo":
                d = de_largo(ruta)
                r = metricas(d.precio_pred, real, d.get("p10"), d.get("p90"), mae_naive)
            else:
                r = metricas(a_largo(ruta, mid), real, mae_ref=mae_naive)
        except Exception as e:                       # un fichero roto no tumba el resto
            print(f"  !! {mid}: {type(e).__name__}: {e}")
            continue
        if r:
            filas.append({"modelo": mid, "formato": fmt, **r})
    for mid, s in refs.items():
        r = metricas(s, real, mae_ref=mae_naive)
        if r:
            filas.append({"modelo": mid, "formato": "referencia", **r})

    t = pd.DataFrame(filas).sort_values("MAE").reset_index(drop=True)
    cols = ["modelo", "MAE", "skill_%", "captura_%", "eur_dia", "pico_1h_%",
            "RMSE", "sMAPE", "cobertura_IC80_%", "n_horas"]
    cols = [c for c in cols if c in t]
    print(t[cols].to_string(index=False, float_format=lambda x: f"{x:8.2f}"))

    out = REPO / "data_temp" / f"leaderboard_{a.tramo}.csv"
    out.parent.mkdir(exist_ok=True)
    t.to_csv(out, index=False)
    print(f"\nbateria: {POTENCIA_MW} MW / {CAPACIDAD_MWH} MWh · eficiencia {EFICIENCIA:.0%} "
          f"· 1 ciclo/dia ({HORAS} h de carga y {HORAS} de descarga)")
    print(f"guardado en {out.relative_to(REPO)}")


if __name__ == "__main__":
    main()
