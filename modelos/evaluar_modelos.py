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
import json
import hashlib
import os
from pathlib import Path

import numpy as np
import pandas as pd

from bess_evaluation import DEFAULT, SIMULADOR, optimizar, valorar
from tiempo_mercado import serie_local, dia_completo, naive_local

REPO = Path(__file__).resolve().parent.parent
MATRIZ = REPO / "data" / "gold" / "matriz_nucleo.csv"

# Alias conservados para los scripts del equipo; la definicion vive en el motor.
POTENCIA_MW = DEFAULT.potencia_mw
CAPACIDAD_MWH = DEFAULT.capacidad_mwh
EFICIENCIA = DEFAULT.eficiencia
HORAS = int(CAPACIDAD_MWH / POTENCIA_MW)  # duracion nominal, no horas de despacho


# ---------------------------------------------------------------- lectura de precios
def precio_real():
    m = pd.read_csv(MATRIZ, usecols=["ts", "target_price", "split"])
    m["ts"] = pd.to_datetime(m["ts"])
    return serie_local(m.set_index("ts"))


def a_largo(ruta, modelo_id):
    """Ancho (dia x h00..h23) -> Serie horaria. El formato de las redes."""
    d = pd.read_csv(ruta, index_col=0, parse_dates=True)
    d.columns = range(24)
    s = d.stack()
    ts = s.index.get_level_values(0) + pd.to_timedelta(s.index.get_level_values(1), "h")
    return serie_local(pd.Series(s.values, index=ts, name=modelo_id))


def de_largo(ruta):
    """Formato UTC: conservar las dos horas de octubre como instantes diferentes."""
    d = pd.read_csv(ruta)
    d["ts"] = pd.to_datetime(d["datetime_utc"], utc=True)
    d = serie_local(d.set_index("ts"))
    cols = ["precio_pred"] + [c for c in ("p10", "p90") if c in d and d[c].notna().any()]
    return d[cols]


# ------------------------------------------------------------------------ arbitraje
def arbitraje(pred, real):
    """Mismo motor fisico para modelo y oraculo; solo dias completos del calendario."""
    df = pd.DataFrame({"p": serie_local(pred), "y": serie_local(real)}).replace(
        [np.inf, -np.inf], np.nan).dropna().sort_index()
    ingresos = []
    for _, g in df.groupby(df.index.normalize()):
        if not dia_completo(g.index):
            continue
        ingresos.append((valorar(optimizar(g.p), g.y).sum(),
                         valorar(optimizar(g.y), g.y).sum()))
    if not ingresos:
        return np.nan, np.nan
    modelo, oraculo = np.sum(ingresos, axis=0)
    return (100 * modelo / oraculo if oraculo > 1e-8 else np.nan,
            modelo / len(ingresos))


def pico_1h(pred, real):
    """% de dias en que la hora mas cara predicha cae a <=1 h de la real."""
    df = pd.DataFrame({"p": serie_local(pred), "y": serie_local(real)}).replace([np.inf, -np.inf], np.nan).dropna().sort_index()
    df["dia"] = df.index.normalize()
    ok = tot = 0
    for _, g in df.groupby("dia"):
        if not dia_completo(g.index):
            continue
        tot += 1
        predicted_peak = g.index[int(g.p.values.argmax())]
        actual_peaks = g.index[g.y.eq(g.y.max())]
        ok += min(abs((predicted_peak - t).total_seconds()) for t in actual_peaks) <= 3600
    return 100 * ok / tot if tot else np.nan


# -------------------------------------------------------------------------- metricas
def metricas(pred, real, p10=None, p90=None, mae_ref=None):
    j = pd.DataFrame({"p": serie_local(pred), "y": serie_local(real)}).replace([np.inf, -np.inf], np.nan).dropna().sort_index()
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
    if mae_ref is not None and np.isfinite(mae_ref) and mae_ref > 0:
        m["skill_%"] = 100 * (1 - m["MAE"] / mae_ref)
    if p10 is not None and p90 is not None:
        q = pd.DataFrame({"lo": serie_local(p10), "hi": serie_local(p90), "y": serie_local(real)}).dropna()
        if len(q):
            m["cobertura_IC80_%"] = 100 * ((q.y >= q.lo) & (q.y <= q.hi)).mean()
    return m


# ------------------------------------------------------------------------ inventario
def inventario(tramo):
    """Todo lo que hay que evaluar, venga en el formato que venga."""
    sufijo = "val" if tramo == "validation" else "test"
    fuentes = []
    for r in sorted(glob.glob(str(REPO / "modelos/**/pred_val_2025.csv"), recursive=True)):
        if tramo == "validation":
            fuentes.append((os.path.basename(os.path.dirname(r)), "largo", r))
    for r in sorted(glob.glob(str(REPO / f"data/gold/finales_*/pred_{sufijo}_*.csv"))):
        mid = os.path.basename(r).replace(f"pred_{sufijo}_", "").replace(".csv", "")
        fuentes.append((mid, "ancho", r))
    return fuentes


def comparar(pred, real, naive, p10=None, p90=None):
    """MAE y referencia usan la misma interseccion de instantes finitos."""
    pairs = pd.DataFrame({"p": serie_local(pred), "y": serie_local(real),
                          "nv": serie_local(naive)}).replace([np.inf, -np.inf], np.nan).dropna()
    if pairs.empty:
        return None
    return metricas(pairs.p, pairs.y, p10, p90, (pairs.nv - pairs.y).abs().mean())


def metadata_evaluacion(index):
    """La huella impide mezclar ventanas distintas aunque tengan el mismo numero de filas."""
    idx = pd.DatetimeIndex(index).tz_convert("UTC").sort_values()
    digest = hashlib.sha256("\n".join(idx.astype(str)).encode()).hexdigest()
    return {**SIMULADOR, "muestra_sha256": digest, "n_periodos": len(idx),
            "desde_utc": idx[0].isoformat(), "hasta_utc": idx[-1].isoformat(),
            "tipo": "simulacion_fuera_de_muestra"}


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
    refs = {
        "naive_D1": naive_local(todo, real.index),
        "media_movil_7d": pd.concat([
            naive_local(todo, real.index, dias=k)
            for k in range(1, 8)
        ], axis=1).mean(axis=1, skipna=False),
    }

    candidatos = []
    for mid, fmt, ruta in inventario(a.tramo):
        try:
            d = de_largo(ruta) if fmt == "largo" else a_largo(ruta, mid).to_frame("precio_pred")
            candidatos.append((mid, fmt, d))
        except (ValueError, OSError, KeyError) as exc:
            print(f"  !! {mid}: no se carga: {exc}")
    candidatos += [(mid, "referencia", series.to_frame("precio_pred")) for mid, series in refs.items()]
    comunes = real[np.isfinite(real)].index
    for _, _, d in candidatos:
        comunes = comunes.intersection(d.index[np.isfinite(d.precio_pred)])
    if comunes.empty:
        print("Sin instantes comunes: no se publica un ranking de coberturas distintas.")
        return
    real = real.reindex(comunes)
    spec = metadata_evaluacion(comunes)
    filas = []
    for mid, fmt, d in candidatos:
        d = d.reindex(comunes)
        r = comparar(d.precio_pred, real, refs["naive_D1"], d.get("p10"), d.get("p90"))
        if r:
            filas.append({"modelo": mid, "formato": fmt, **r, "simulador": json.dumps(spec)})

    t = pd.DataFrame(filas).sort_values("MAE").reset_index(drop=True)
    cols = ["modelo", "MAE", "skill_%", "captura_%", "eur_dia", "pico_1h_%",
            "RMSE", "sMAPE", "cobertura_IC80_%", "n_horas"]
    cols = [c for c in cols if c in t]
    print(t[cols].to_string(index=False, float_format=lambda x: f"{x:8.2f}"))

    out = REPO / "data_temp" / f"leaderboard_{a.tramo}.csv"
    out.parent.mkdir(exist_ok=True)
    t.to_csv(out, index=False)
    print(f"\nbateria: {POTENCIA_MW} MW / {CAPACIDAD_MWH} MWh · eficiencia {EFICIENCIA:.0%} "
          f"· hasta {DEFAULT.ciclos_max:g} ciclo equivalente/dia · SOC cerrado · {SIMULADOR['version']}")
    print(f"guardado en {out.relative_to(REPO)}")


if __name__ == "__main__":
    main()
