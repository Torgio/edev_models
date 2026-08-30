"""Adaptaciones de la metodologia de depuracion del modulo 06.1 a la serie horaria del TFM.

`scripts/funciones_mineria.py` es la libreria de clase, copiada sin tocar. Este
modulo NO la reemplaza: la envuelve y corrige los tres puntos donde la receta de
datos transversales no se sostiene sobre una serie temporal horaria.

  1. GRANO. El bronce no imputa a proposito (ver `01_union_bronze.ipynb`): lo
     diario se coloca solo en la hora 00 local y lo 3-horario solo en su paso.
     Eso produce 95,8 % y 66,9 % de "nulos" que NO son perdidos, son estructura.
     Aplicar la regla de clase de "eliminar variable con >50 % missing" sobre el
     bronce crudo borraria gas, CO2, meteo y potencia instalada de un plumazo.
     `reconstruir_grano` deshace esa estructura antes de mirar los perdidos.

  2. ATIPICOS. `atipicosAmissing` convierte extremos en missing para imputarlos
     despues. En el precio horario un pico de 300 EUR/MWh es exactamente el
     fenomeno a predecir, no ruido. Aqui se usa en modo DIAGNOSTICO
     (`diagnostico_atipicos`): mide y ordena, no borra. La conversion a missing
     queda reservada a las columnas con error de fuente ya documentado.

  3. IMPUTACION. La mediana global rompe la continuidad de una serie. Se
     sustituye por interpolacion temporal acotada (`imputacion_temporal`), que
     respeta la forma local y deja en NaN los huecos largos en vez de inventarlos.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from bronze_config import TABLES
from funciones_mineria import Vcramer, atipicosAmissing

# Columnas del calendario: no son variables de analisis.
COLS_CALENDARIO = ["ts_utc", "ts_local", "date_utc", "date_local", "hour_utc", "hour_local"]


def mapa_granos():
    """Grano de origen de cada columna del unificado, deducido de bronze_config.

    El nombre en el unificado es prefix + '_' + columna, misma convencion que usa
    extract_bronze.py al escribir cada parquet.
    """
    grano = {}
    for cfg in TABLES.values():
        g = cfg.get("grain", "hourly")
        prefijo = cfg["prefix"]
        for col in cfg["columns"]:
            grano[prefijo + "_" + col] = g
    return grano


def reconstruir_grano(df, limite_3h=2):
    """Devuelve el dataframe con el grano reconstruido y el informe de lo hecho.

    - daily: el valor de la hora 00 local se difunde a las 24 horas de ese dia
      local (no a un dia natural UTC: el mercado y los cierres son hora local).
    - 3h: interpolacion temporal acotada a `limite_3h` horas. Con limite 2 se
      rellenan las dos horas entre pasos de ERA5 y nada mas: un dia entero sin
      dato meteorologico sigue siendo un dia sin dato.
    - hourly: no se toca.
    """
    grano = mapa_granos()
    out = df.copy()
    filas = []

    diarias = [c for c in out.columns if grano.get(c) == "daily"]
    if diarias:
        antes = out[diarias].isna().mean()
        # Dentro de cada dia local el valor esta en la primera fila (hora 00),
        # asi que ffill+bfill por grupo lo reparte sin cruzar el borde del dia.
        out[diarias] = out.groupby("date_local", sort=False)[diarias].ffill()
        out[diarias] = out.groupby("date_local", sort=False)[diarias].bfill()
        for c in diarias:
            filas.append(("daily", c, antes[c], out[c].isna().mean()))

    tres_h = [c for c in out.columns if grano.get(c) == "3h"]
    if tres_h:
        antes = out[tres_h].isna().mean()
        aux = out.set_index("ts_utc")[tres_h].interpolate(
            method="time", limit=limite_3h, limit_direction="both", limit_area="inside"
        )
        out[tres_h] = aux.to_numpy()
        for c in tres_h:
            filas.append(("3h", c, antes[c], out[c].isna().mean()))

    informe = pd.DataFrame(filas, columns=["grano", "variable", "nulos_antes", "nulos_despues"])
    return out, informe.sort_values("nulos_antes", ascending=False).reset_index(drop=True)


def descriptivo_numericas(df, cols):
    """describe() ampliado con asimetria, curtosis, rango y perdidos.

    Mismas columnas que la seccion 3 de `Tarea_Eleccionesy_v2.py`, mas dos que
    en serie horaria hacen falta: proporcion de ceros exactos (delata la
    convencion NULL/0 de REE) y de negativos (delata signo o balance neto).
    """
    d = df[cols].describe().T
    d["Asimetria"] = df[cols].skew()
    d["Kurtosis"] = df[cols].kurtosis()
    d["Rango"] = df[cols].apply(lambda x: np.ptp(x.dropna()) if x.notna().any() else np.nan)
    d["N_Missing"] = df[cols].isna().sum()
    d["%_Missing"] = df[cols].isna().mean() * 100
    d["%_Ceros"] = df[cols].apply(lambda x: (x.dropna() == 0).mean() * 100 if x.notna().any() else np.nan)
    d["%_Negativos"] = df[cols].apply(lambda x: (x.dropna() < 0).mean() * 100 if x.notna().any() else np.nan)
    d["N_Distintos"] = df[cols].nunique()
    return d


def diagnostico_atipicos(df, cols):
    """Aplica el criterio de clase (`atipicosAmissing`) pero SOLO para medir.

    Devuelve, por variable, cuantos valores marcaria como atipicos y en que
    proporcion. Nada se modifica: la decision de tratar o no cada caso se toma
    variable a variable, con el dominio delante.
    """
    filas = []
    for c in cols:
        s = df[c]
        if s.notna().sum() < 10 or s.nunique(dropna=True) < 2:
            filas.append((c, 0, 0.0, np.nan, np.nan))
            continue
        n = atipicosAmissing(s)[1]
        filas.append((c, int(n), n / len(s) * 100, s.skew(), s.min()))
    return (
        pd.DataFrame(filas, columns=["variable", "n_atipicos", "pct_atipicos", "asimetria", "minimo"])
        .sort_values("pct_atipicos", ascending=False)
        .reset_index(drop=True)
    )


def imputacion_temporal(df, cols, limite=3):
    """Interpolacion temporal acotada, en lugar de mediana global.

    `limite` es el hueco maximo (en horas) que se rellena. Por encima de eso el
    NaN se conserva: un apagon de datos de medio dia no se maquilla, se declara.
    """
    out = df.copy()
    antes = out[cols].isna().sum()
    aux = out.set_index("ts_utc")[cols].interpolate(
        method="time", limit=limite, limit_direction="both", limit_area="inside"
    )
    out[cols] = aux.to_numpy()
    despues = out[cols].isna().sum()
    informe = pd.DataFrame({"nulos_antes": antes, "nulos_despues": despues})
    informe["imputados"] = informe["nulos_antes"] - informe["nulos_despues"]
    return out, informe[informe["nulos_antes"] > 0].sort_values("imputados", ascending=False)


def ranking_asociacion(df, cols, target):
    """Ordena las candidatas por su asociacion con el objetivo continuo.

    Tres medidas a la vez porque miden cosas distintas y discrepar es informativo:
    - Pearson: relacion lineal, la que aprovecha una regresion sin transformar.
    - Spearman: relacion monotona, inmune a la escala y a los picos de precio.
    - V de Cramer (funciones_mineria): asociacion sobre quintiles, capta forma de
      U o de escalon que las dos anteriores dan por nula.
    """
    filas = []
    for c in cols:
        if c == target:
            continue
        par = df[[c, target]].dropna()
        if len(par) < 100 or par[c].nunique() < 2:
            filas.append((c, np.nan, np.nan, np.nan, len(par)))
            continue
        pear = par[c].corr(par[target])
        spear = par[c].corr(par[target], method="spearman")
        try:
            vc = Vcramer(par[c].reset_index(drop=True), par[target].reset_index(drop=True))
        except Exception:
            vc = np.nan
        filas.append((c, pear, spear, vc, len(par)))
    r = pd.DataFrame(filas, columns=["variable", "pearson", "spearman", "v_cramer", "n_pares"])
    r["abs_pearson"] = r["pearson"].abs()
    return r.sort_values("v_cramer", ascending=False).reset_index(drop=True)


def bloques_correlacion(df, cols, umbral=0.95):
    """Pares de variables con |r| por encima del umbral: candidatas a redundancia.

    En este dataset la redundancia es esperable y en parte deliberada (dos fuentes
    para la misma magnitud, un forecast que es suma exacta de otros dos). Listarla
    explicitamente evita meter las dos versiones en la matriz final sin querer.
    """
    corr = df[cols].corr()
    m = np.triu(np.ones(corr.shape, dtype=bool), k=1)
    pares = corr.where(m).stack()
    altas = pares[pares.abs() >= umbral].sort_values(key=np.abs, ascending=False)
    return altas.rename("correlacion").reset_index().rename(columns={"level_0": "var_a", "level_1": "var_b"})
