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

# Features seguras de esios_forecast_da -- ampliado 20-ago-2026 segun la seleccion del equipo
# (matriz FORECAST). demanda_prev_mw (1775) SE MANTIENE junto a demanda_mercado_prev_mw (2563)
# a peticion del equipo, aunque el 1775 tiene un sesgo creciente documentado (hasta +2.386 MW en
# 2026, el doble de error todos los años frente al 2563) -- no es fuga, es redundancia con una
# version peor, se deja que el modelo decida. gen_renovables_prev_mw tambien se mantiene pese a
# ser colineal exacto con gen_wind_prev_mw + gen_solar_pv_prev_mw (verificado 18-ago-2026, 58.127
# filas identicas) -- mismo criterio, redundancia aceptada a proposito, no invalida el dataset.
COLS_SEGURAS_FORECAST = [
    "demanda_prev_mw", "demanda_mercado_prev_mw", "gen_wind_prev_mw", "gen_solar_pv_prev_mw",
    "gen_renovables_prev_mw", "gen_solartermica_prev_mw", "cap_baleares_prev_mw",
    "ntc_fr_imp_prev_mw", "ntc_fr_exp_prev_mw", "ntc_pt_imp_prev_mw", "ntc_pt_exp_prev_mw",
    "ntc_ma_imp_prev_mw", "ntc_ma_exp_prev_mw",
]
# EXCLUIDA -- OJO, NO es una omision, es una fuga real: demanda_residual_prev_mw se revisa
# 10-14 dias despues de publicarse (verificado con check_tables/verificar_revision_indicadores.py).
# El valor guardado hoy en la BD para una fecha historica es el YA REVISADO, no el que existia en
# el momento de predecir -- misma familia de bug que el del target D+1 que se corrigio el
# 17-ago-2026. Pendiente de confirmacion explicita antes de añadirla (ver conversacion 20-ago).
# potencia_indisp_pbf_mw ya NO EXISTE en esios_forecast_da (el equipo la elimino el 19-ago-2026 --
# era un duplicado exacto de esios_pbf_gen.unavailable_power_mw, y estar en la tabla de forecasts
# inducia a creerla leak-safe pre-cierre cuando en realidad es dato de PBF, post-cierre).

# Matriz de generacion/demanda del equipo, cerrada 19-ago-2026 (ver docs/notas_memoria_tfm.md y
# la vista materializada `generation` en la BD) -- reemplaza la seleccion anterior del 19-ago.
#
# Demanda real -> ree_load (load_inter), a peticion expresa del equipo (20-ago-2026), pese a que
# la version anterior de este script usaba entsoe_load por la contaminacion de autoconsumo
# documentada en D-03 (brecha +435 a +2.495 MW dic-2025/jul-2026). El equipo decidio usar ree_load
# de todas formas -- si se quiere volver a entsoe_load, es cambiar esta lista.
#
# entsoe_load añadida el 20-ago-2026 (adicion de ultimo momento del equipo, no un reemplazo de
# ree_load -- las dos entran juntas). OJO para quien la use: las dos miden lo mismo salvo por el
# autoconsumo (D-03), asi que en 2020-2024 son practicamente identicas y desde dic-2025 se separan
# cada vez mas -- entran ambas tal como se indico, sin resolver esa redundancia aqui.
#
# load_inter tambien aporta netflow/total_net_flow/gen_peninsular: son dato REAL ya ocurrido
# (no una capacidad publicada de antemano como el NTC), asi que van aqui con el resto de reales
# con lag D-1/D-7, no en COLS_NTC (que sigue el criterio "seguro, D+1" de _features_ntc).
TABLA_DEMANDA_REAL = "load_inter"
COLS_DEMANDA_REAL = ["ree_load", "entsoe_load", "ree_netflow_fr", "ree_netflow_pt", "ree_netflow_ma",
                      "total_net_flow_mw", "gen_peninsular_mw"]

# ENTSO-E gana eolica, hidraulica fluyente, consumo de bombeo, biomasa, residuos, otras
# renovables y fuel-oil. hydro_reservoir_mw y pumping_gen_mw YA NO van sueltas -- ver
# _features_lag_reales: se fusionan en hydro_dispatch_mw (equivalente a c_ghydrodispatch de la
# matriz) porque ENTSO-E no separaba la turbinacion de bombeo (B10) del embalse (B12) antes de
# dic-2022 -- pumping_gen_mw es NULL el 100% de 2020-2021 y 91% de 2022 (verificado 20-ago-2026),
# y hydro_reservoir_mw sola tiene un escalon de nivel en esa misma frontera (media 2.756 en 2021,
# 1.720 en 2022, 1.940 en 2023) -- exactamente el tipo de discontinuidad a mitad de train que ya
# encontramos antes con el autoconsumo, solo que aqui la fuente misma cambia de definicion.
TABLA_ENTSOE_REAL = "entsoe_gen_data"
COLS_ENTSOE_REAL = ["wind_mw", "pumping_cons_mw", "hydro_run_river_mw",
                     "biomass_mw", "waste_mw", "other_renewable_mw", "oil_mw"]

# ESIOS gana termosolar, CCGT puro, cogeneracion y resto (SIN dividir -- ver nota de
# _features_lag_reales), nuclear, carbon, y bateria (descarga/carga). ree_gnuclear_mw y
# ree_gcoal_mw van por ESIOS, no ENTSO-E, aunque coinciden al ±0,4% -- es la fuente que eligio el
# equipo (corregido 20-ago-2026: una version anterior de este patch los tenia por ENTSO-E).
TABLA_ESIOS_REAL = "esios_gen"
COLS_ESIOS_REAL = ["ree_gsolter_mw", "ree_gccgas_mw", "ree_gbattery_mw", "ree_cbattery_mw",
                    "ree_gnuclear_mw", "ree_gotherthermal_mw", "ree_gcoal_mw"]
# ree_gsolar_mw (FV) se EXCLUYE de COLS_ESIOS_REAL a proposito: incorpora autoconsumo desde
# dic-2025, mismo problema que ree_load (verificado: brecha de -546 a +1.387 MW entre sep-2025 y
# ago-2026). Se reconstruye una FV limpia por resta en vez de leerla directo -- ver
# _features_lag_reales: GREATEST(0, entsoe_gen_data.solar_mw - esios_gen.ree_gsolter_mw). ENTSO-E
# solar_mw es B16 = FV+termosolar sin el problema de autoconsumo (es de ENTSO-E, no de ESIOS);
# restarle la termosolar limpia de ESIOS deja una FV limpia.
#
# ree_gotherthermal_mw (cogeneracion y resto, indicador 1297) entra COMPLETA, sin dividir en
# cogeneracion_gas_mw/resto_no_convencional_mw como se planteo en una version anterior
# (gas_lags.patch): el equipo evaluo esa division a fondo y la descarto -- la resta sigue las
# rampas del CCGT (57 MW a las 9h, 332 MW a las 19h) porque en realidad mide el desfase entre una
# CONSIGNA en tiempo real (entsoe.gas_mw, B04) y una TELEMEDIDA real (ree_gccgas_mw), no una
# tecnologia; resto_no_convencional_mw llegaba a ser negativa 10 veces su propia magnitud en el
# 11% de las horas de 2025. Ver docs/decisiones_datos.md D-02 y la matriz FINAL del 19-ago.


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
            "SELECT * FROM esios_capacity_available WHERE datetime BETWEEN %(start)s AND %(end)s ORDER BY date",
            conn, params=params,
        )
        df_cap_inst = pd.read_sql(
            "SELECT * FROM esios_capacity_installed WHERE datetime BETWEEN %(start)s AND %(end)s ORDER BY date",
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
# las previsiones. Ahora en `load_inter` (antes esios_load_inter, eliminada 19-ago-2026). Marruecos
# (ree_ntc_impma/expma) se incluye desde el 20-ago-2026 a peticion del equipo -- antes se dejaba
# fuera porque no se habia resuelto en el punto #3. NOTA: es capacidad publicada, no flujo real
# (eso va en COLS_DEMANDA_REAL, ver arriba) -- por eso se alinea D+1 como las previsiones.
COLS_NTC = ["ree_ntc_impfr", "ree_ntc_expfr", "ree_ntc_imppt", "ree_ntc_exppt",
            "ree_ntc_impma", "ree_ntc_expma"]

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
    """Segunda prevision oficial (ENTSO-E, independiente de ESIOS) -- misma alineacion D+1.
    FUERA del dataset por defecto desde 20-ago-2026 -- decision de reunion del equipo, ver
    docs/columnas_pendientes_equipo.md. Solo se activa con `incluir_columnas_pendientes=True`."""
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
    (no hay fuga: ninguna de las dos "sabe" mas que la otra sobre el resultado real).
    FUERA del dataset por defecto desde 20-ago-2026 -- decision de reunion del equipo, ver
    docs/columnas_pendientes_equipo.md. Depende de _features_forecast_entsoe, mismo interruptor."""
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


COLS_COMMODITIES = ["gas_mibgas", "gas_ttf", "co2_ets", "carbon_api2", "co2_eua_dec", "gas_ttf_m1"]


def _features_dia_d(conn) -> pd.DataFrame:
    """Commodities del propio dia D (lag natural: ya ocurrieron). Las 6 columnas de commodities --
    ampliado 20-ago-2026, antes solo se usaban 3 de las 6."""
    df_comm = pd.read_sql(
        f"SELECT fecha, {', '.join(COLS_COMMODITIES)} FROM commodities WHERE fecha BETWEEN %(start)s AND %(end)s",
        conn, params={"start": DATASET_START, "end": DATASET_END},
    )
    df_comm["fecha"] = pd.to_datetime(df_comm["fecha"]).dt.date
    df_comm = df_comm.sort_values("fecha").set_index("fecha").ffill()  # fin de semana -> ultimo cierre
    return df_comm


def _features_capacidad_disponible(conn) -> pd.DataFrame:
    """Capacidad disponible del propio dia D (lag natural). FUERA del dataset por defecto desde
    20-ago-2026 -- decision de reunion del equipo, ver docs/columnas_pendientes_equipo.md. Solo
    se activa con `incluir_columnas_pendientes=True` en `construir_dataset_diario`."""
    df_capd = pd.read_sql(
        "SELECT date, total_mw FROM esios_capacity_available WHERE datetime BETWEEN %(start)s AND %(end)s",
        conn, params={"start": DATASET_START, "end": DATASET_END},
    )
    df_capd["date"] = pd.to_datetime(df_capd["date"]).dt.date
    return df_capd.rename(columns={"total_mw": "capacidad_disp_total_mw"}).set_index("date")


def _calendario(index_fechas) -> pd.DataFrame:
    """Calendario de D+1 (determinista, siempre conocido de antemano)."""
    cal = pd.DataFrame(index=index_fechas)
    d1 = pd.to_datetime(cal.index) + pd.Timedelta(days=1)
    cal["d1_dow"] = d1.dayofweek
    cal["d1_month"] = d1.month
    cal["d1_is_weekend"] = (d1.dayofweek >= 5).astype(int)
    return cal


def _features_lag_reales(conn) -> pd.DataFrame:
    """Demanda, eolica/bombeo/hidraulica, termosolar+CCGT, y FV limpia (reconstruida), todas
    reales -- agregadas por dia y desplazadas a D-1 y D-7. Nunca el propio dia D (que aun no ha
    terminado al predecir)."""
    df_load = pd.read_sql(
        f"SELECT datetime, {', '.join(COLS_DEMANDA_REAL)} FROM {TABLA_DEMANDA_REAL} "
        f"WHERE datetime BETWEEN %(start)s AND %(end)s",
        conn, params={"start": DATASET_START, "end": DATASET_END},
    )
    # entsoe_load en 0 exacto es fisicamente imposible para demanda peninsular (verificado
    # 21-ago-2026: 9 horas de 58.151, 8 seguidas el 1-jul-2026 madrugada + 1 aislada el
    # 17-mar-2026 -- corte de ingesta puntual, no un problema sistemico. Caen en el tramo de
    # test, asi que sin este fix corromperian los lags D-1/D-7 de esos dias especificos con un
    # cero irreal. Se pasan a NaN para que la agregacion diaria (mean/min/max) las ignore, en vez
    # de tratarlas como demanda real de 0 MW).
    if "entsoe_load" in df_load.columns:
        df_load.loc[df_load["entsoe_load"] == 0, "entsoe_load"] = pd.NA
    df_entsoe = pd.read_sql(
        f"SELECT datetime, {', '.join(COLS_ENTSOE_REAL)} FROM {TABLA_ENTSOE_REAL} "
        f"WHERE datetime BETWEEN %(start)s AND %(end)s",
        conn, params={"start": DATASET_START, "end": DATASET_END},
    )
    df_esios = pd.read_sql(
        f"SELECT datetime, {', '.join(COLS_ESIOS_REAL)} FROM {TABLA_ESIOS_REAL} "
        f"WHERE datetime BETWEEN %(start)s AND %(end)s",
        conn, params={"start": DATASET_START, "end": DATASET_END},
    )
    # FV limpia: NUNCA se lee ree_gsolar_mw directo (contaminada de autoconsumo desde dic-2025).
    # Se reconstruye por resta: GREATEST(0, entsoe.solar_mw - esios.ree_gsolter_mw). El GREATEST
    # corrige los casos (~1% de las horas) donde la resta da un valor negativo por ruido de
    # redondeo entre las dos fuentes.
    df_solar_fv = pd.read_sql(
        "SELECT e.datetime, GREATEST(0, e.solar_mw - s.ree_gsolter_mw) AS solar_fv_mw "
        "FROM entsoe_gen_data e JOIN esios_gen s ON e.datetime = s.datetime "
        "WHERE e.datetime BETWEEN %(start)s AND %(end)s AND e.solar_mw IS NOT NULL AND s.ree_gsolter_mw IS NOT NULL",
        conn, params={"start": DATASET_START, "end": DATASET_END},
    )
    # Hidraulica despachable, fusionada (equivalente a c_ghydrodispatch de la matriz del equipo):
    # embalse (B12) + turbinacion de bombeo (B10), con COALESCE a 0 SOLO en el bombeo -- si el
    # embalse mismo es NULL, el resultado se queda NULL (no se inventa un cero). Necesario porque
    # ENTSO-E no separaba B10 de B12 antes de dic-2022 -- ver nota en COLS_ENTSOE_REAL.
    df_hydro_dispatch = pd.read_sql(
        "SELECT datetime, hydro_reservoir_mw + COALESCE(pumping_gen_mw, 0) AS hydro_dispatch_mw "
        "FROM entsoe_gen_data WHERE datetime BETWEEN %(start)s AND %(end)s",
        conn, params={"start": DATASET_START, "end": DATASET_END},
    )

    piezas_horarias = [
        (df_load, COLS_DEMANDA_REAL), (df_entsoe, COLS_ENTSOE_REAL),
        (df_esios, COLS_ESIOS_REAL), (df_solar_fv, ["solar_fv_mw"]),
        (df_hydro_dispatch, ["hydro_dispatch_mw"]),
    ]
    aggs = []
    for df, cols in piezas_horarias:
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)  # normalizacion UTC manual, NUNCA parse_dates
        df["fecha"] = df["datetime"].dt.tz_convert("Europe/Madrid").dt.date
        aggs.append(df.groupby("fecha")[cols].agg(["mean", "min", "max"]))

    real_diario = aggs[0].join(aggs[1:], how="outer")
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
    """Precio real de Portugal y Francia, D-1 y D-7 -- NUNCA el propio D+1. FUERA del dataset por
    defecto desde 20-ago-2026 -- decision de reunion del equipo, ver
    docs/columnas_pendientes_equipo.md. Solo se activa con `incluir_columnas_pendientes=True`.
    El precio de esos paises para D+1 se fija en la MISMA subasta simultanea que el español
    (acoplamiento SDAC/MIBEL) -- no se conoce con antelacion, usarlo sin lag seria casi tan
    circular como usar el propio precio de España. Portugal correlaciona 0.997 con España
    (practicamente el mismo mercado, coincide en el 94.9% de las horas); Francia 0.70 (conexion real, con capacidad
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


# Las 9 columnas de medicion de era5_weather_agg -- ampliado 20-ago-2026 a peticion del equipo,
# antes solo se usaban 6. Quedan fuera tensor_path/tensor_index: son metadatos internos del
# pipeline de descarga (ruta del tensor NetCDF, indice), no variables fisicas.
COLS_CLIMA = ["t2m_mean", "d2m_mean", "wind10_mean", "wind100_mean", "wind_gust10_mean",
              "ssrd_mean", "tcc_mean", "tp_mean", "msl_mean"]


def _features_clima(conn) -> pd.DataFrame:
    """Clima (ERA5) agregado por dia, alineado a D+1 -- MISMO criterio de shift que
    `_features_forecast` (describe D+1, se coloca en la fila D que lo usaria).

    Incluida por defecto desde el 20-ago-2026, con visto bueno del equipo. OJO -- la salvedad de
    seguridad sigue siendo real y vale la pena tenerla presente pese a la aprobacion: ERA5 es
    reanalisis (clima REAL ya ocurrido), no una prevision. Usar el clima de D+1 asume que en
    produccion se tendria una prevision ECMWF igual de buena para D+1, lo cual todavia no esta
    validado (`ecmwf_forecast_agg` solo tiene unos dias de historico) -- pendiente de conversacion
    del equipo, ver conversacion 20-ago-2026. El equipo decidio aceptar esa asuncion por ahora
    -- para desactivarla, `incluir_clima=False`.
    """
    df = pd.read_sql(
        f"SELECT ts, {', '.join(COLS_CLIMA)} FROM era5_weather_agg WHERE ts BETWEEN %(start)s AND %(end)s ORDER BY ts",
        conn, params={"start": DATASET_START, "end": DATASET_END},
    )
    df["ts"] = pd.to_datetime(df["ts"], utc=True)   # normalizacion UTC manual, NUNCA parse_dates
    # Kelvin -> Celsius: t2m (temperatura) y d2m (punto de rocio) son las dos unicas en Kelvin
    # (verificado 20-ago-2026: d2m_mean ronda 276-277, mismo rango que t2m_mean sin convertir).
    df["t2m_mean"] = df["t2m_mean"] - 273.15
    df["d2m_mean"] = df["d2m_mean"] - 273.15
    df["fecha_objetivo"] = df["ts"].dt.tz_convert("Europe/Madrid").dt.date  # dia D+1 que describe

    feats = df.groupby("fecha_objetivo")[COLS_CLIMA].agg(["mean", "min", "max"])
    feats.columns = [f"{c}_{stat}" for c, stat in feats.columns]
    feats.index = (pd.to_datetime(feats.index) - pd.Timedelta(days=1)).date  # -> fila del dia D
    feats.index.name = "fecha"
    return feats


def construir_dataset_diario(solo_filas_validas: bool = True, incluir_clima: bool = True,
                              incluir_columnas_pendientes: bool = False) -> pd.DataFrame:
    """Construye el dataset maestro completo: target D+1 + features seguras + lags reales.

    Parametros:
        solo_filas_validas: si True (por defecto), excluye filas con el target D+1 incompleto
            (cambio de hora en marzo, borde final de la ventana de datos).
        incluir_clima: si True (por defecto desde 20-ago-2026, con visto bueno del equipo), añade
            las features de ERA5 (ver `_features_clima` -- proxy de una futura previsión ECMWF de
            D+1, no producción-segura tal cual, salvedad aceptada por el equipo). `False` para
            desactivarlo.
        incluir_columnas_pendientes: si True, añade las 4 familias de columnas que la reunion del
            equipo del 20-ago-2026 dejo FUERA del dataset por ahora (previsión ENTSO-E, diferencia
            entre previsiones, capacidad disponible, precio de paises vecinos) -- ver
            docs/columnas_pendientes_equipo.md para el detalle y el porque. Por defecto False.

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
        feats_dia_d = _features_dia_d(conn)
        cal = _calendario(target.index)
        feats_lag_real = _features_lag_reales(conn)
        feats_lag_precio = _features_lag_precio(target_wide)
        feats_clima = _features_clima(conn) if incluir_clima else None

        if incluir_columnas_pendientes:
            feats_fcst_entsoe = _features_forecast_entsoe(conn)
            feats_diff_previsiones = _features_diferencia_previsiones(conn)
            feats_capacidad = _features_capacidad_disponible(conn)
            feats_lag_precio_vecinos = _features_lag_precio_vecinos(conn)
        else:
            feats_fcst_entsoe = feats_diff_previsiones = feats_capacidad = feats_lag_precio_vecinos = None
    finally:
        conn.close()

    piezas = [feats_fcst, feats_ntc, feats_dia_d, cal, feats_lag_real, feats_lag_precio]
    for pieza in (feats_fcst_entsoe, feats_diff_previsiones, feats_capacidad, feats_lag_precio_vecinos, feats_clima):
        if pieza is not None:
            piezas.append(pieza)
    dataset = target.join(piezas, how="left")

    if solo_filas_validas:
        target_cols = [c for c in dataset.columns if c.startswith("price_h")]
        dataset = dataset[dataset[target_cols].notna().all(axis=1)].copy()

    # Redondeo a 2 decimales -- a peticion del equipo, 21-ago-2026. round() solo toca columnas
    # numericas de coma flotante; el calendario (d1_dow/d1_month/d1_is_weekend, enteros) no se ve
    # afectado. No cambia ninguna decision de modelado, solo la presentacion de los valores.
    dataset = dataset.round(2)

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