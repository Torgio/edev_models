"""Forma del precio a partir de la METEOROLOGIA, no de un factor de escala.

POR QUE
`curva_precios.simular` remuestrea dias reales y ensancha su forma con un factor que sale de
la capacidad solar instalada. Funciona, pero tiene un techo medido: en el backtest sobre
junio de 2025, con molde de 2023-24, el valle simulado se queda en 29 EUR/MWh contra 17,9
reales incluso con el factor al maximo. Escalar una forma la hunde en proporcion a lo que ya
era; entre 2023 y 2025 la forma CAMBIO, no solo crecio.

LA IDEA
El valle de mediodia no lo abre la capacidad instalada: lo abre la ENERGIA solar que entra,
que es radiacion x capacidad. Un dia nublado con 76 GW se parece a uno soleado con 40. Asi
que en vez de escalar, se modela:

    forma_del_dia  ~  radiacion x capacidad_solar  +  viento x capacidad_eolica  +  mes

y para el futuro se sortea un dia de METEO historico y se evalua con la capacidad prevista.
La variabilidad deja de ser un remuestreo ciego y pasa a ser meteorologica: un año de poco
viento sale caro y uno soleado tiene el valle hundido, como en la realidad.

Correlaciones medidas sobre 968 dias desde 2024:

    ssrd    -> valle  -0,445      t2m  -> valle  -0,545
    wind100 -> nivel  -0,490      tcc  -> valle  +0,431

LIMITE HONESTO
Seis años de meteorologia son seis "años climaticos". El sector usa treinta o mas para que la
distribucion de años secos, ventosos y frios este bien representada. Con seis, la cola de
años extremos esta infra-muestreada y la banda sale mas estrecha de lo que deberia.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO / "ingesta"))
sys.path.append(str(REPO / "scripts"))

VARS = ["ssrd_mean", "wind100_mean", "t2m_mean", "tcc_mean"]


def meteo_diaria() -> pd.DataFrame:
    """Meteo agregada por dia, cosiendo ERA5 con la prevision ECMWF donde ERA5 no llega."""
    from curva_precios import _con
    with _con() as con:
        e = pd.read_sql(f"SELECT ts, {', '.join(VARS)} FROM era5_weather_agg ORDER BY ts", con)
        f = pd.read_sql(f"SELECT ts, {', '.join(VARS)} FROM ecmwf_forecast_agg ORDER BY ts", con)
    for d in (e, f):
        d["dia"] = pd.to_datetime(d.ts).dt.normalize()
    e = e.groupby("dia")[VARS].mean()
    f = f.groupby("dia")[VARS].mean()
    # ERA5 manda donde llega; la prevision rellena la cola, que es lo que hace que esto
    # sirva en produccion -- el reanalisis va cinco dias por detras siempre.
    return e.combine_first(f).dropna()


def capacidad_diaria() -> pd.DataFrame:
    """GW de solar y eolica instalados, por dia."""
    from curva_precios import _con
    with _con() as con:
        c = pd.read_sql("""SELECT date, solar_pv_mw, COALESCE(autoconsume_solar_pv_mw,0) auto,
                                  wind_mw FROM esios_capacity_installed ORDER BY date""", con)
    c["dia"] = pd.to_datetime(c.date)
    # `.to_numpy()` a proposito: construir el DataFrame con Series que llevan el indice
    # numerico original y pasarle `index=c.dia` hace que pandas REINDEXE por fecha contra
    # 0,1,2... No casa nada, sale todo NaN y no avisa.
    return pd.DataFrame({"solar_gw": ((c.solar_pv_mw + c.auto) / 1000).to_numpy(),
                         "eolica_gw": (c.wind_mw / 1000).to_numpy()}, index=c.dia)


def tabla(desde="2024-01-01") -> pd.DataFrame:
    """Una fila por dia: forma del precio, meteo y capacidad.

    Arranca en 2024 por defecto: antes de eso el parque solar era tan distinto que la
    relacion entre radiacion y forma del precio era otra.
    """
    from curva_precios import historico
    h = historico()
    h = h[h.dia >= desde]
    h["rel"] = h.precio - h.groupby("dia").precio.transform("mean")
    d = pd.DataFrame({
        "nivel": h.groupby("dia").precio.mean(),
        "valle": h[h.hora.between(12, 15)].groupby("dia").rel.mean(),
        "pico": h[h.hora.between(19, 21)].groupby("dia").rel.mean()})
    d["spread"] = d.pico - d.valle
    d = d.join(meteo_diaria(), how="inner").join(capacidad_diaria(), how="inner").dropna()
    # LA VARIABLE QUE IMPORTA: energia, no potencia. Radiacion por capacidad.
    d["solar_efectiva"] = d.ssrd_mean * d.solar_gw / 1000
    d["eolica_efectiva"] = d.wind100_mean ** 3 * d.eolica_gw / 1000   # potencia ~ v^3
    d["mes"] = d.index.month
    return d


def ajustar(d: pd.DataFrame | None = None):
    """Regresion lineal de valle y pico sobre la energia renovable efectiva.

    Devuelve (predecir, info). `predecir(solar_ef, eolica_ef)` da (valle, pico) en EUR/MWh
    sobre la media del dia.
    """
    d = tabla() if d is None else d
    X = np.column_stack([d.solar_efectiva, d.eolica_efectiva, np.ones(len(d))])
    cv, *_ = np.linalg.lstsq(X, d.valle.to_numpy(), rcond=None)
    cp, *_ = np.linalg.lstsq(X, d.pico.to_numpy(), rcond=None)

    def r2(c, y):
        return 1 - ((y - X @ c) ** 2).sum() / ((y - y.mean()) ** 2).sum()

    return (lambda s, e: (float(cv[0] * s + cv[1] * e + cv[2]),
                          float(cp[0] * s + cp[1] * e + cp[2])),
            {"n_dias": len(d),
             "valle_R2": round(float(r2(cv, d.valle.to_numpy())), 3),
             "pico_R2": round(float(r2(cp, d.pico.to_numpy())), 3),
             "valle_por_solar": round(float(cv[0]), 3),
             "pico_por_solar": round(float(cp[0]), 3),
             "valle_por_eolica": round(float(cv[1]), 5),
             "solar_ef_media": round(float(d.solar_efectiva.mean()), 1),
             "eolica_ef_media": round(float(d.eolica_efectiva.mean()), 1)})
