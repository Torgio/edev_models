"""
TFM Energia UCM — Estructura de tiempo oficial del equipo (espina horaria + dataset de modelado)

Este script construye las DOS estructuras de tiempo que todo el equipo debe compartir de cara a
la presentacion del miercoles, para que cada quien pueda hacer su propio EDA/modelado sobre
exactamente la misma base:

  A) ESPINA HORARIA (`construir_espina_horaria`) -- union por timestamp exacto de las tablas
     nucleo (entsoe_gen_data, entsoe_load_inter, esios_gen, load_inter, esios_forecast_da) +
     variables diarias (commodities, capacidad) difundidas sobre las 24h. Para EDA, correlaciones,
     graficos. NOTA (19-ago-2026): esios_load_inter se elimino, sustituida por `load_inter`
     (demanda + interconexiones consolidadas, ESIOS+ENTSO-E unificado).

  B) DATASET DIARIO (`construir_dataset_diario`) -- una fila por dia D, target = las 24 horas de
     precio de D+1 (nunca del propio D), solo features sin fuga de informacion, lags de datos
     reales en D-1/D-7, y el split cronologico train/validation/test ya fijado. Para entrenar y
     comparar modelos -- el mismo split para todos, si no, comparar resultados el miercoles no
     tiene sentido.

Que resuelve el dataset diario, en una frase por punto:
  1. Una fila por dia D, target = las 24 horas de precio de D+1 (nunca del propio D).
  2. Solo features sin fuga de informacion (catalogo ya verificado con el equipo).
  3. Lags de datos reales (demanda, eolica/bombeo, solar, precio) en D-1 y D-7.
  4. Filtra automaticamente los dias con target incompleto (cambio de hora, borde de la ventana).
  5. Split cronologico train/validation/test ya fijado -- mismo periodo de test para todos.

Uso:
    from construir_dataset_maestro import (
        construir_espina_horaria, construir_dataset_diario, dividir_train_val_test,
    )

    espina = construir_espina_horaria()          # para EDA/correlaciones
    dataset = construir_dataset_diario()          # para modelado
    train, val, test = dividir_train_val_test(dataset)

O directamente desde la terminal, para generar los CSV compartidos:
    python construir_dataset_maestro.py                    # solo el dataset diario (rapido, ~1.7 MB)
    python construir_dataset_maestro.py --con-espina        # tambien la espina horaria (~35 MB, mas lento)

Requisitos: pandas, psycopg2-binary (mismo credentials.json que el resto de ingesta/).
"""

import sys
from pathlib import Path

import pandas as pd
import psycopg2

sys.path.append(str(Path(__file__).parent.parent / "ingesta"))
from config import load_config

# ── Ventana de datos y fronteras del split (fijas para todo el equipo) ──────────────────────
DATASET_START = "2020-01-01"
DATASET_END = "2026-08-15"

TRAIN_END = pd.Timestamp("2024-12-31").date()   # train: DATASET_START -> TRAIN_END
VAL_END = pd.Timestamp("2025-12-31").date()      # validation: TRAIN_END+1 -> VAL_END
# test: VAL_END+1 -> fecha mas reciente disponible

# Features seguras de esios_forecast_da (catalogo verificado, ver Banco de Evidencias punto #1-2).
# demanda_prev_mw (1775) se CAMBIO a demanda_mercado_prev_mw (2563) el 19-ago-2026 -- el equipo
# verifico que 1775 tiene un sesgo creciente (hasta +2.386 MW en 2026) y el doble de error en
# todos los años frente al 2563. gen_renovables_prev_mw se EXCLUYE a proposito: es
# gen_wind_prev_mw + gen_solar_pv_prev_mw exacto (verificado 18-ago-2026, identidad exacta en las
# 58.127 filas donde las tres tienen dato) -- colinealidad perfecta si entran los tres a la vez.
COLS_SEGURAS_FORECAST = ["demanda_mercado_prev_mw", "gen_wind_prev_mw", "gen_solar_pv_prev_mw"]
# EXCLUIDA a proposito: demanda_residual_prev_mw (revision 10-14 dias).
# potencia_indisp_pbf_mw ya NO EXISTE en esios_forecast_da (el equipo la elimino el 19-ago-2026 --
# era un duplicado exacto de esios_pbf_gen.unavailable_power_mw, y estar en la tabla de forecasts
# inducia a creerla leak-safe pre-cierre cuando en realidad es dato de PBF, post-cierre).

# Ganadores del punto #3 (duplicados de fuente resueltos).
# TABLA_DEMANDA_REAL: esios_load_inter/entsoe_load_inter se unificaron en `load_inter` el
# 19-ago-2026. Demanda real -> entsoe_load, NUNCA ree_load: desde dic-2025 REE empieza a sumarle
# una estimacion de autoconsumo a ree_load, y la brecha crece mes a mes (verificado: +435 MW en
# dic-2025 -> +2.495 MW en jul-2026) -- la serie cambia de significado a mitad del historico.
TABLA_DEMANDA_REAL = "load_inter"
COLS_DEMANDA_REAL = ["entsoe_load"]
TABLA_EOLICA_BOMBEO_REAL = "entsoe_gen_data"
COLS_EOLICA_BOMBEO_REAL = ["wind_mw", "pumping_gen_mw", "pumping_cons_mw"]
TABLA_SOLAR_REAL = "esios_gen"
COLS_SOLAR_REAL = ["ree_gsolar_mw", "ree_gsolter_mw"]


def _conectar():
    _, db_config = load_config()
    return psycopg2.connect(**db_config)


def _leer_horaria(conn, sql: str, col_ts: str, params: dict) -> pd.DataFrame:
    """SELECT + normalizacion UTC manual. NUNCA usar parse_dates=[...] aqui -- sobre una columna
    timestamptz que cruza un cambio de hora (CET/CEST), parse_dates la deja en un offset FIJO y
    convierte en NaT la mitad de las filas (las del otro offset). Un merge posterior por esa
    columna hace que esos NaT casen entre si y el join explota (visto en este proyecto: un join
    de 31.583 x 31.583 filas intento reservar 48 GB). Por eso se lee sin parse_dates y se
    normaliza a mano con pd.to_datetime(..., utc=True) justo despues."""
    df = pd.read_sql(sql, conn, params=params)
    df[col_ts] = pd.to_datetime(df[col_ts], utc=True)
    return df


def construir_espina_horaria(start: str = DATASET_START, end: str = DATASET_END) -> pd.DataFrame:
    """Espina horaria de EXPLORACION (correlaciones, distribuciones, graficos) -- no es el
    dataset de modelado (para eso, `construir_dataset_diario`). Union por timestamp exacto de
    las 5 tablas nucleo (ver Banco de Evidencias / notas de negocio para el criterio de por que
    estas 5), con `entsoe_gen_data` como eje porque es la mas completa/limpia. Se añaden ademas
    las variables diarias (commodities, capacidad) difundidas sobre las 24h del dia.
    """
    conn = _conectar()
    try:
        params = {"start": start, "end": end}
        df_gen = _leer_horaria(
            conn, "SELECT * FROM entsoe_gen_data WHERE datetime BETWEEN %(start)s AND %(end)s ORDER BY datetime",
            "datetime", params,
        )
        df_load = _leer_horaria(
            conn, "SELECT * FROM entsoe_load_inter WHERE datetime BETWEEN %(start)s AND %(end)s ORDER BY datetime",
            "datetime", params,
        )
        df_esios_gen = _leer_horaria(
            conn, "SELECT * FROM esios_gen WHERE datetime BETWEEN %(start)s AND %(end)s ORDER BY datetime",
            "datetime", params,
        )
        df_load_inter = _leer_horaria(
            conn, "SELECT * FROM load_inter WHERE datetime BETWEEN %(start)s AND %(end)s ORDER BY datetime",
            "datetime", params,
        )
        df_forecast = _leer_horaria(
            conn, "SELECT * FROM esios_forecast_da WHERE datetime BETWEEN %(start)s AND %(end)s ORDER BY datetime",
            "datetime", params,
        )
        df_commodities = pd.read_sql(
            "SELECT * FROM commodities WHERE fecha BETWEEN %(start)s AND %(end)s ORDER BY fecha", conn, params=params,
        ).sort_values("fecha").ffill()   # fines de semana/festivos -> ultimo cierre, no interpolar
        df_commodities["fecha"] = pd.to_datetime(df_commodities["fecha"]).dt.date
        df_cap_disp = pd.read_sql(
            "SELECT * FROM esios_capacity_available WHERE date BETWEEN %(start)s AND %(end)s ORDER BY date",
            conn, params=params,
        )
        df_cap_inst = pd.read_sql(
            "SELECT * FROM esios_capacity_installed WHERE date BETWEEN %(start)s AND %(end)s ORDER BY date",
            conn, params=params,
        )
    finally:
        conn.close()

    espina = df_gen.rename(columns={"datetime": "ts"})
    espina = espina.merge(df_load.rename(columns={"datetime": "ts"}), on="ts", how="left", suffixes=("", "_load"))
    espina = espina.merge(df_esios_gen.rename(columns={"datetime": "ts"}), on="ts", how="left", suffixes=("", "_esiosgen"))
    espina = espina.merge(df_load_inter.rename(columns={"datetime": "ts"}), on="ts", how="left", suffixes=("", "_loadinter"))
    espina = espina.merge(df_forecast.rename(columns={"datetime": "ts"}), on="ts", how="left", suffixes=("", "_fcst"))
    assert espina["ts"].is_unique, "la espina no deberia tener timestamps duplicados -- revisar duplicados de fuente"

    espina["fecha"] = espina["ts"].dt.tz_convert("UTC").dt.date
    for nombre, df_diario, col_fecha in [
        ("commodities", df_commodities, "fecha"),
        ("esios_capacity_available", df_cap_disp, "date"),
        ("esios_capacity_installed", df_cap_inst, "date"),
    ]:
        df_diario = df_diario.copy()
        df_diario["fecha"] = pd.to_datetime(df_diario[col_fecha]).dt.date
        if col_fecha != "fecha":
            df_diario = df_diario.drop(columns=[col_fecha])
        espina = espina.merge(df_diario, on="fecha", how="left", suffixes=("", f"_{nombre}"))

    assert espina["ts"].is_unique, "la espina no deberia tener timestamps duplicados tras el join diario"
    return espina


def _construir_lag(df: pd.DataFrame, dias: int, sufijo: str) -> pd.DataFrame:
    """Desplaza el indice HACIA ADELANTE `dias` dias: el dato descrito en la fecha X
    aparece en la fila X + dias (para que quede alineado a la fila que lo usa como lag)."""
    out = df.copy()
    out.index = pd.to_datetime(out.index) + pd.Timedelta(days=dias)
    out.index = out.index.date
    out.columns = [f"{c}_{sufijo}" for c in out.columns]
    out.index.name = "fecha"
    return out


def _target_d1(conn) -> pd.DataFrame:
    """Precio real horario -> 24 columnas por dia, YA DESPLAZADO para que la fila D
    contenga el precio real de D+1 (no el de D -- ver nota de correccion del 17-ago-2026)."""
    df = pd.read_sql(
        "SELECT datetime, es_esios FROM spot_price WHERE datetime BETWEEN %(start)s AND %(end)s ORDER BY datetime",
        conn, params={"start": DATASET_START, "end": DATASET_END},
    )
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)  # normalizacion UTC manual, NUNCA parse_dates
    df["fecha_madrid"] = df["datetime"].dt.tz_convert("Europe/Madrid").dt.date
    df["hora"] = df["datetime"].dt.tz_convert("Europe/Madrid").dt.hour

    wide = df.pivot_table(index="fecha_madrid", columns="hora", values="es_esios", aggfunc="first")
    wide.columns = [f"price_h{h:02d}" for h in wide.columns]
    wide.index.name = "fecha"

    idx_completo = pd.date_range(pd.to_datetime(wide.index.min()), pd.to_datetime(wide.index.max()), freq="D")
    wide.index = pd.to_datetime(wide.index)
    target_d1 = wide.reindex(idx_completo).shift(-1)   # fila D <- precio real de D+1
    target_d1.index = target_d1.index.date
    target_d1.index.name = "fecha"
    return target_d1


def _features_forecast(conn) -> pd.DataFrame:
    """Previsiones oficiales de esios_forecast_da (seguras: publicadas antes del cierre del
    mercado), agregadas por dia y alineadas a la fila D que las conoce."""
    df = pd.read_sql(
        f"SELECT datetime, {', '.join(COLS_SEGURAS_FORECAST)} FROM esios_forecast_da "
        f"WHERE datetime BETWEEN %(start)s AND %(end)s",
        conn, params={"start": DATASET_START, "end": DATASET_END},
    )
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df["fecha_objetivo"] = df["datetime"].dt.tz_convert("Europe/Madrid").dt.date  # dia D+1 que predice

    feats = df.groupby("fecha_objetivo")[COLS_SEGURAS_FORECAST].agg(["mean", "min", "max"])
    feats.columns = [f"{c}_{stat}" for c, stat in feats.columns]
    feats.index = (pd.to_datetime(feats.index) - pd.Timedelta(days=1)).date  # -> fila del dia D
    feats.index.name = "fecha"
    return feats


# NTC (capacidad de interconexion) -- ganador del punto #3, publicada antes del cierre igual que
# las previsiones. Ahora en `load_inter` (antes esios_load_inter, eliminada 19-ago-2026). Solo
# Francia y Portugal (Marruecos existe en la tabla pero no se resolvio en el punto #3 -- ver
# ree_ntc_impma/ree_ntc_expma si se decide incluirlo mas adelante).
COLS_NTC = ["ree_ntc_impfr", "ree_ntc_expfr", "ree_ntc_imppt", "ree_ntc_exppt"]

# entsoe_forecast_da: revivida el 17-ago-2026 (antes 192 filas, ahora historico completo).
# renewables_forecast_mw se EXCLUYE por el mismo motivo que gen_renovables_prev_mw: es
# wind_forecast_mw + solar_forecast_mw exacto (verificado 18-ago-2026, 57.598 filas identicas).
COLS_FORECAST_ENTSOE = ["load_forecast_mw", "wind_forecast_mw", "solar_forecast_mw"]


def _features_ntc(conn) -> pd.DataFrame:
    """NTC Francia/Portugal de load_inter -- mismo criterio de alineacion que
    _features_forecast (publicada antes del cierre, describe D+1, se coloca en la fila D)."""
    df = pd.read_sql(
        f"SELECT datetime, {', '.join(COLS_NTC)} FROM load_inter WHERE datetime BETWEEN %(start)s AND %(end)s",
        conn, params={"start": DATASET_START, "end": DATASET_END},
    )
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df["fecha_objetivo"] = df["datetime"].dt.tz_convert("Europe/Madrid").dt.date

    feats = df.groupby("fecha_objetivo")[COLS_NTC].agg(["mean", "min", "max"])
    feats.columns = [f"{c}_{stat}" for c, stat in feats.columns]
    feats.index = (pd.to_datetime(feats.index) - pd.Timedelta(days=1)).date
    feats.index.name = "fecha"
    return feats


def _features_forecast_entsoe(conn) -> pd.DataFrame:
    """Segunda prevision oficial (ENTSO-E, independiente de ESIOS) -- misma alineacion D+1."""
    df = pd.read_sql(
        f"SELECT datetime, {', '.join(COLS_FORECAST_ENTSOE)} FROM entsoe_forecast_da "
        f"WHERE datetime BETWEEN %(start)s AND %(end)s",
        conn, params={"start": DATASET_START, "end": DATASET_END},
    )
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df["fecha_objetivo"] = df["datetime"].dt.tz_convert("Europe/Madrid").dt.date

    feats = df.groupby("fecha_objetivo")[COLS_FORECAST_ENTSOE].agg(["mean", "min", "max"])
    feats.columns = [f"entsoe_{c}_{stat}" for c, stat in feats.columns]
    feats.index = (pd.to_datetime(feats.index) - pd.Timedelta(days=1)).date
    feats.index.name = "fecha"
    return feats


def _features_diferencia_previsiones(conn) -> pd.DataFrame:
    """Diferencia horaria entre las DOS previsiones independientes (ESIOS vs ENTSO-E) para
    demanda/eolica/solar, agregada por dia -- proxy de incertidumbre del dia. Las dos se
    publican antes del cierre, asi que la diferencia en si tambien es segura como feature
    (no hay fuga: ninguna de las dos "sabe" mas que la otra sobre el resultado real)."""
    df_esios = pd.read_sql(
        "SELECT datetime, demanda_prev_mw, gen_wind_prev_mw, gen_solar_pv_prev_mw FROM esios_forecast_da "
        "WHERE datetime BETWEEN %(start)s AND %(end)s",
        conn, params={"start": DATASET_START, "end": DATASET_END},
    )
    df_entsoe = pd.read_sql(
        f"SELECT datetime, {', '.join(COLS_FORECAST_ENTSOE)} FROM entsoe_forecast_da "
        f"WHERE datetime BETWEEN %(start)s AND %(end)s",
        conn, params={"start": DATASET_START, "end": DATASET_END},
    )
    df_esios["datetime"] = pd.to_datetime(df_esios["datetime"], utc=True)
    df_entsoe["datetime"] = pd.to_datetime(df_entsoe["datetime"], utc=True)

    df = df_esios.merge(df_entsoe, on="datetime", how="inner")
    df["diff_demanda"] = (df["demanda_prev_mw"] - df["load_forecast_mw"]).abs()
    df["diff_eolica"] = (df["gen_wind_prev_mw"] - df["wind_forecast_mw"]).abs()
    df["diff_solar"] = (df["gen_solar_pv_prev_mw"] - df["solar_forecast_mw"]).abs()
    df["fecha_objetivo"] = df["datetime"].dt.tz_convert("Europe/Madrid").dt.date

    cols_diff = ["diff_demanda", "diff_eolica", "diff_solar"]
    feats = df.groupby("fecha_objetivo")[cols_diff].agg(["mean", "max"])
    feats.columns = [f"{c}_{stat}" for c, stat in feats.columns]
    feats.index = (pd.to_datetime(feats.index) - pd.Timedelta(days=1)).date
    feats.index.name = "fecha"
    return feats


def _features_dia_d(conn) -> pd.DataFrame:
    """Commodities y capacidad disponible del propio dia D (lag natural: ya ocurrieron)."""
    df_comm = pd.read_sql(
        "SELECT fecha, gas_mibgas, gas_ttf, co2_ets FROM commodities WHERE fecha BETWEEN %(start)s AND %(end)s",
        conn, params={"start": DATASET_START, "end": DATASET_END},
    )
    df_comm["fecha"] = pd.to_datetime(df_comm["fecha"]).dt.date
    df_comm = df_comm.sort_values("fecha").set_index("fecha").ffill()  # fin de semana -> ultimo cierre

    df_capd = pd.read_sql(
        "SELECT date, total_mw FROM esios_capacity_available WHERE date BETWEEN %(start)s AND %(end)s",
        conn, params={"start": DATASET_START, "end": DATASET_END},
    )
    df_capd["date"] = pd.to_datetime(df_capd["date"]).dt.date
    df_capd = df_capd.rename(columns={"total_mw": "capacidad_disp_total_mw"}).set_index("date")

    return df_comm.join(df_capd, how="outer")


def _calendario(index_fechas) -> pd.DataFrame:
    """Calendario de D+1 (determinista, siempre conocido de antemano)."""
    cal = pd.DataFrame(index=index_fechas)
    d1 = pd.to_datetime(cal.index) + pd.Timedelta(days=1)
    cal["d1_dow"] = d1.dayofweek
    cal["d1_month"] = d1.month
    cal["d1_is_weekend"] = (d1.dayofweek >= 5).astype(int)
    return cal


def _features_lag_reales(conn) -> pd.DataFrame:
    """Demanda, eolica/bombeo y solar reales (ganadores del punto #3), agregadas por dia
    y desplazadas a D-1 y D-7 -- nunca el propio dia D (que aun no ha terminado al predecir)."""
    df_load = pd.read_sql(
        f"SELECT datetime, {', '.join(COLS_DEMANDA_REAL)} FROM {TABLA_DEMANDA_REAL} "
        f"WHERE datetime BETWEEN %(start)s AND %(end)s",
        conn, params={"start": DATASET_START, "end": DATASET_END},
    )
    df_eolica = pd.read_sql(
        f"SELECT datetime, {', '.join(COLS_EOLICA_BOMBEO_REAL)} FROM {TABLA_EOLICA_BOMBEO_REAL} "
        f"WHERE datetime BETWEEN %(start)s AND %(end)s",
        conn, params={"start": DATASET_START, "end": DATASET_END},
    )
    df_solar = pd.read_sql(
        f"SELECT datetime, {', '.join(COLS_SOLAR_REAL)} FROM {TABLA_SOLAR_REAL} "
        f"WHERE datetime BETWEEN %(start)s AND %(end)s",
        conn, params={"start": DATASET_START, "end": DATASET_END},
    )

    df_load["datetime"] = pd.to_datetime(df_load["datetime"], utc=True)
    df_eolica["datetime"] = pd.to_datetime(df_eolica["datetime"], utc=True)
    df_solar["datetime"] = pd.to_datetime(df_solar["datetime"], utc=True)

    df_load["fecha"] = df_load["datetime"].dt.tz_convert("Europe/Madrid").dt.date
    df_eolica["fecha"] = df_eolica["datetime"].dt.tz_convert("Europe/Madrid").dt.date
    df_solar["fecha"] = df_solar["datetime"].dt.tz_convert("Europe/Madrid").dt.date

    agg_load = df_load.groupby("fecha")[COLS_DEMANDA_REAL].agg(["mean", "min", "max"])
    agg_eolica = df_eolica.groupby("fecha")[COLS_EOLICA_BOMBEO_REAL].agg(["mean", "min", "max"])
    agg_solar = df_solar.groupby("fecha")[COLS_SOLAR_REAL].agg(["mean", "min", "max"])

    real_diario = agg_load.join([agg_eolica, agg_solar], how="outer")
    real_diario.columns = [f"{c}_{stat}" for c, stat in real_diario.columns]

    lag1 = _construir_lag(real_diario, 1, "lag1d")
    lag7 = _construir_lag(real_diario, 7, "lag7d")
    return lag1.join(lag7, how="outer")


def _features_lag_precio(target_wide_sin_shift: pd.DataFrame) -> pd.DataFrame:
    """Lag del precio real (D-1, D-7) -- la señal de autocorrelacion mas fuerte."""
    precio_diario = target_wide_sin_shift.agg(["mean", "min", "max"], axis=1)
    precio_diario.columns = [f"precio_real_{stat}" for stat in precio_diario.columns]
    lag1 = _construir_lag(precio_diario, 1, "lag1d")
    lag7 = _construir_lag(precio_diario, 7, "lag7d")
    return lag1.join(lag7, how="outer")


# Portugal y Francia son los UNICOS paises de spot_price con interconexion fisica a España.
# El resto (Alemania, Italia, Suiza, Belgica, Holanda, Austria, Polonia, Chequia) no tiene cable
# a España -- su correlacion con el precio español (0.5-0.68) probablemente ya la capturan
# gas_ttf/co2_ets, y meterlos sin un canal causal real arriesga redundancia sin justificacion.
COLS_PRECIO_VECINOS = ["pt_entsoe", "fr_entsoe"]


def _features_lag_precio_vecinos(conn) -> pd.DataFrame:
    """Precio real de Portugal y Francia, D-1 y D-7 -- NUNCA el propio D+1. El precio de esos
    paises para D+1 se fija en la MISMA subasta simultanea que el español (acoplamiento
    SDAC/MIBEL) -- no se conoce con antelacion, usarlo sin lag seria casi tan circular como usar
    el propio precio de España. Portugal correlaciona 0.997 con España (practicamente el mismo
    mercado, coincide en el 94.9% de las horas); Francia 0.70 (conexion real, con capacidad
    limitada frente al tamaño de ambos mercados)."""
    df = pd.read_sql(
        f"SELECT datetime, {', '.join(COLS_PRECIO_VECINOS)} FROM spot_price WHERE datetime BETWEEN %(start)s AND %(end)s",
        conn, params={"start": DATASET_START, "end": DATASET_END},
    )
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df["fecha"] = df["datetime"].dt.tz_convert("Europe/Madrid").dt.date

    precio_diario = df.groupby("fecha")[COLS_PRECIO_VECINOS].agg(["mean", "min", "max"])
    precio_diario.columns = [f"{c}_{stat}" for c, stat in precio_diario.columns]

    lag1 = _construir_lag(precio_diario, 1, "lag1d")
    lag7 = _construir_lag(precio_diario, 7, "lag7d")
    return lag1.join(lag7, how="outer")


COLS_CLIMA = ["t2m_mean", "wind10_mean", "wind100_mean", "ssrd_mean", "tcc_mean", "tp_mean"]


def _features_clima(conn) -> pd.DataFrame:
    """Clima (ERA5) agregado por dia, alineado a D+1 -- MISMO criterio de shift que
    `_features_forecast` (describe D+1, se coloca en la fila D que lo usaria).

    OJO -- esto NO es equivalente en seguridad al resto de features "seguras": ERA5 es
    reanalisis (clima REAL ya ocurrido), no una prevision. Usar el clima de D+1 asume que en
    produccion se tendria una prevision ECMWF igual de buena para D+1, lo cual todavia no esta
    validado (`ecmwf_forecast_agg` solo tiene unos dias de historico). Por eso esta funcion NO
    se incluye por defecto en `construir_dataset_diario` -- solo con `incluir_clima=True`,
    a proposito, para dejar clara la diferencia con el resto del catalogo.
    """
    df = pd.read_sql(
        f"SELECT ts, {', '.join(COLS_CLIMA)} FROM era5_weather_agg WHERE ts BETWEEN %(start)s AND %(end)s ORDER BY ts",
        conn, params={"start": DATASET_START, "end": DATASET_END},
    )
    df["ts"] = pd.to_datetime(df["ts"], utc=True)   # normalizacion UTC manual, NUNCA parse_dates
    df["t2m_mean"] = df["t2m_mean"] - 273.15   # Kelvin -> Celsius
    df["fecha_objetivo"] = df["ts"].dt.tz_convert("Europe/Madrid").dt.date  # dia D+1 que describe

    feats = df.groupby("fecha_objetivo")[COLS_CLIMA].agg(["mean", "min", "max"])
    feats.columns = [f"{c}_{stat}" for c, stat in feats.columns]
    feats.index = (pd.to_datetime(feats.index) - pd.Timedelta(days=1)).date  # -> fila del dia D
    feats.index.name = "fecha"
    return feats


def construir_dataset_diario(solo_filas_validas: bool = True, incluir_clima: bool = False) -> pd.DataFrame:
    """Construye el dataset maestro completo: target D+1 + features seguras + lags reales.

    Parametros:
        solo_filas_validas: si True (por defecto), excluye filas con el target D+1 incompleto
            (cambio de hora en marzo, borde final de la ventana de datos).
        incluir_clima: si True, añade las features de ERA5 (ver `_features_clima` -- proxy de
            una futura previsión ECMWF de D+1, no producción-segura tal cual). Por defecto False.

    Devuelve un DataFrame indexado por "fecha" (el dia D, dia en que se hace la prediccion).
    """
    conn = _conectar()
    try:
        # target_wide SIN shift: "precio real por dia", se reutiliza para los lags de precio
        target_wide = pd.read_sql(
            "SELECT datetime, es_esios FROM spot_price WHERE datetime BETWEEN %(start)s AND %(end)s ORDER BY datetime",
            conn, params={"start": DATASET_START, "end": DATASET_END},
        )
        target_wide["datetime"] = pd.to_datetime(target_wide["datetime"], utc=True)
        target_wide["fecha_madrid"] = target_wide["datetime"].dt.tz_convert("Europe/Madrid").dt.date
        target_wide["hora"] = target_wide["datetime"].dt.tz_convert("Europe/Madrid").dt.hour
        target_wide = target_wide.pivot_table(index="fecha_madrid", columns="hora", values="es_esios", aggfunc="first")
        target_wide.columns = [f"price_h{h:02d}" for h in target_wide.columns]
        target_wide.index.name = "fecha"

        target = _target_d1(conn)                       # con shift -1: target real de D+1
        feats_fcst = _features_forecast(conn)
        feats_ntc = _features_ntc(conn)
        feats_fcst_entsoe = _features_forecast_entsoe(conn)
        feats_diff_previsiones = _features_diferencia_previsiones(conn)
        feats_dia_d = _features_dia_d(conn)
        cal = _calendario(target.index)
        feats_lag_real = _features_lag_reales(conn)
        feats_lag_precio = _features_lag_precio(target_wide)
        feats_lag_precio_vecinos = _features_lag_precio_vecinos(conn)
        feats_clima = _features_clima(conn) if incluir_clima else None
    finally:
        conn.close()

    piezas = [feats_fcst, feats_ntc, feats_fcst_entsoe, feats_diff_previsiones,
              feats_dia_d, cal, feats_lag_real, feats_lag_precio, feats_lag_precio_vecinos]
    if feats_clima is not None:
        piezas.append(feats_clima)
    dataset = target.join(piezas, how="left")

    if solo_filas_validas:
        target_cols = [c for c in dataset.columns if c.startswith("price_h")]
        dataset = dataset[dataset[target_cols].notna().all(axis=1)].copy()

    return dataset


def dividir_train_val_test(dataset: pd.DataFrame):
    """Split cronologico oficial del equipo. Devuelve (train, validation, test).
    NUNCA aleatorio -- ver docstring del modulo."""
    idx = pd.to_datetime(dataset.index)
    mask_train = idx <= pd.Timestamp(TRAIN_END)
    mask_val = (idx > pd.Timestamp(TRAIN_END)) & (idx <= pd.Timestamp(VAL_END))
    mask_test = idx > pd.Timestamp(VAL_END)
    return dataset[mask_train], dataset[mask_val], dataset[mask_test]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Genera los CSV compartidos del equipo")
    parser.add_argument("--con-espina", action="store_true",
                        help="Ademas del dataset diario, genera tambien espina_horaria.csv (~35 MB, mas lento)")
    args = parser.parse_args()

    print("Construyendo dataset maestro (diario, para modelado)...")
    dataset = construir_dataset_diario()
    train, val, test = dividir_train_val_test(dataset)

    dataset = dataset.copy()
    dataset["split"] = "train"
    dataset.loc[val.index, "split"] = "validation"
    dataset.loc[test.index, "split"] = "test"

    out_path = Path(__file__).parent / "dataset_maestro.csv"
    dataset.to_csv(out_path, index_label="fecha")

    print(f"dataset: {dataset.shape[0]} dias x {dataset.shape[1]} columnas")
    print(f"  train:      {len(train):>5} dias  ({train.index.min()} -> {train.index.max()})")
    print(f"  validation: {len(val):>5} dias  ({val.index.min()} -> {val.index.max()})")
    print(f"  test:       {len(test):>5} dias  ({test.index.min()} -> {test.index.max()})")
    print(f"guardado en: {out_path}")

    if args.con_espina:
        print("\nConstruyendo espina horaria (para EDA/correlaciones)...")
        espina = construir_espina_horaria()
        out_espina = Path(__file__).parent / "espina_horaria.csv"
        espina.to_csv(out_espina, index=False)
        print(f"espina: {espina.shape[0]} filas x {espina.shape[1]} columnas")
        print(f"rango: {espina['ts'].min()} -> {espina['ts'].max()}")
        print(f"guardado en: {out_espina}")
