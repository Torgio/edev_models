r"""
TFM Energia UCM - Dataset maestro en formato HORARIO (una fila por hora objetivo, no por dia)

Version PARALELA a `construir_dataset_maestro.py` (que sigue intacta y vigente) -- ver
conversacion del 22-ago-2026. Misma pregunta de negocio (predecir el precio de D+1 sin fuga de
informacion), misma ventana, mismas fuentes y mismo catalogo de columnas -- de hecho este modulo
IMPORTA las constantes de `construir_dataset_maestro.py` para garantizar que los dos datasets son
comparables columna a columna, y que la unica diferencia real es la estructura de filas.

LA DIFERENCIA
--------------
`construir_dataset_maestro.py`: una fila = un dia D, 24 columnas de destino (price_h00..price_h23),
features comprimidas a mean/min/max porque hay que meter 24 horas en una fila.

Este script: una fila = UNA hora objetivo (una hora concreta de D+1), UNA sola columna de
destino, y la hora del dia (0-23) es una feature mas. Esto tiene tres consecuencias:

  1. Los lags dejan de ser "media/min/max de ayer" y pasan a ser directos: lag_24h (la MISMA hora,
     ayer) y lag_168h (la MISMA hora, hace una semana) -- mas preciso, y es el estandar en
     forecasting horario de electricidad.
  2. Las features de prevision/NTC/clima (horarias nativas) se unen por timestamp EXACTO, sin
     agregar ni desplazar por dia -- simplifica el codigo respecto al dataset diario.
  3. Un solo modelo aprende de las 24 horas juntas (con "hora" como feature), en vez de 24 modelos
     independientes via MultiOutputRegressor -- puede compartir patrones entre horas que hoy se
     aprenden 24 veces por separado, sin comunicacion entre si.

El split train/validation/test usa las MISMAS fronteras (TRAIN_END/VAL_END) que el dataset
diario, aplicadas al dia D (el dia que hace la prediccion, no el dia D+1 objetivo) -- para que
un mismo dia caiga siempre en el mismo split en los dos datasets, y la comparacion sea limpia.

IMPORTANTE: ningun modelo ya entrenado (SARIMA, RandomForest, XGBoost, LightGBM) es compatible
con este dataset tal cual -- la estructura de entrada/salida cambia (de 24 salidas a 1, de
features dia-agregadas a features hora-especificas). Hay que reentrenar todos sobre este dataset
para poder comparar.

Uso:
    from construir_dataset_horario import construir_dataset_horario, dividir_train_val_test_horario

    dataset = construir_dataset_horario()
    train, val, test = dividir_train_val_test_horario(dataset)
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2

sys.path.append(str(Path(__file__).parent.parent / "ingesta"))
from config import load_config

# Reutiliza el catalogo de columnas ya verificado -- garantiza que los dos datasets son
# comparables (mismas fuentes, mismo criterio de leak-safety), solo cambia la estructura de filas.
from construir_dataset_maestro import (
    DATASET_START, DATASET_END, TRAIN_END, VAL_END,
    COLS_SEGURAS_FORECAST, COLS_NTC, COLS_FORECAST_ENTSOE,
    TABLA_DEMANDA_REAL, COLS_DEMANDA_REAL, TABLA_ENTSOE_REAL, COLS_ENTSOE_REAL,
    TABLA_ESIOS_REAL, COLS_ESIOS_REAL, COLS_PRECIO_VECINOS, COLS_CLIMA, COLS_COMMODITIES,
    COLS_AUTOCONSUMO_PREV, COLS_CAPACIDAD_DISPONIBLE,
)

LAG_1D_HORAS = 24    # "D-1" en el dataset diario -> 24 horas atras, misma hora
LAG_7D_HORAS = 168   # "D-7" en el dataset diario -> 168 horas atras, misma hora

# esios_pdbc_gen (22-ago-2026): programa de generacion por tecnologia del PDBC (Programa Diario
# Base de Casacion) -- resultado de la MISMA subasta que fija el precio, no una prevision
# independiente. Se ofrece en dos modos, nunca por defecto -- ver `pdbc` en
# construir_dataset_horario():
#   "lag"                -> tratamiento conservador, igual que el resto de reales (24h/168h atras).
#                           No puede haber fuga: son datos que ya ocurrieron.
#   "mismodia_diagnostico" -> SOLO PARA DIAGNOSTICO. Usa el PDBC del propio D+1 sin desplazar, para
#                           ver si mejora de forma sospechosa (señal de que es circular con el
#                           precio). Prefijo "PELIGRO_" en las columnas para que no se use sin
#                           darse cuenta en un pipeline real.
COLS_PDBC = ["wind_mw", "solar_pv_mw", "solar_thermal_mw", "hydro_ugh_mw", "hydro_no_ugh_mw",
             "nuclear_mw", "coal_mw", "cogen_mw", "biomass_mw", "biogas_mw", "hybrid_mw",
             "ccgt_mw", "fuel_gas_mw", "waste_mw", "other_renew_mw", "pumping_gen_mw", "pumping_cons_mw"]


def _conectar():
    _, db_config = load_config()
    return psycopg2.connect(**db_config)


def _grid_horario() -> pd.DatetimeIndex:
    """Rejilla horaria UTC completa y regular -- sobre UTC (sin cambios de hora) los shift() de
    24/168 posiciones son exactos por construccion, sin los problemas de borde de DST que si
    afectan a una agregacion por dia calendario local."""
    fin = pd.Timestamp(DATASET_END) + pd.Timedelta(hours=23)
    return pd.date_range(DATASET_START, fin, freq="h", tz="UTC")


def _serie_horaria(conn, tabla: str, cols: list, idx: pd.DatetimeIndex, col_ts: str = "datetime") -> pd.DataFrame:
    """Lee columnas horarias nativas de una tabla y las reindexa a la rejilla UTC completa.
    NUNCA parse_dates=[...] -- se lee crudo y se normaliza a mano con to_datetime(utc=True)."""
    df = pd.read_sql(
        f"SELECT {col_ts}, {', '.join(cols)} FROM {tabla} WHERE {col_ts} BETWEEN %(start)s AND %(end)s",
        conn, params={"start": DATASET_START, "end": DATASET_END},
    )
    df[col_ts] = pd.to_datetime(df[col_ts], utc=True)
    df = df.drop_duplicates(subset=[col_ts]).set_index(col_ts).reindex(idx)
    return df


def construir_dataset_horario(solo_filas_validas: bool = True, pdbc: str | None = None) -> pd.DataFrame:
    """Construye el dataset maestro horario: una fila por hora objetivo (D+1, hora h).

    Parametros:
        pdbc: None (por defecto, no se incluye), "lag" (tratamiento conservador, 24h/168h atras,
            igual que el resto de reales), o "mismodia_diagnostico" (SOLO para comprobar si hay
            fuga -- usa el PDBC del propio D+1 sin desplazar; columnas con prefijo "PELIGRO_").

    Devuelve un DataFrame indexado por timestamp UTC (la hora exacta que se predice), con una
    sola columna de destino `precio` y las features correspondientes.
    """
    idx = _grid_horario()
    conn = _conectar()
    try:
        # --- Objetivo: el precio de cada hora, sin transformar -- la fila YA es esa hora ---
        df_precio = _serie_horaria(conn, "spot_price", ["es_esios"], idx)
        precio = df_precio["es_esios"]

        # --- Features "seguras" horarias nativas: se unen por timestamp EXACTO, sin desplazar
        # ni agregar -- describen exactamente la hora de la fila y ya se sabian antes del cierre
        # del mercado (mismo catalogo verificado que el dataset diario). ---
        df_fcst = _serie_horaria(conn, "esios_forecast_da", COLS_SEGURAS_FORECAST, idx)
        df_ntc = _serie_horaria(conn, "load_inter", COLS_NTC, idx)
        df_fcst_entsoe = _serie_horaria(conn, "entsoe_forecast_da", COLS_FORECAST_ENTSOE, idx)
        df_fcst_entsoe.columns = [f"entsoe_{c}" for c in df_fcst_entsoe.columns]

        # Autoconsumo previsto, de la nueva vista `forecast` (22-ago-2026) -- union por timestamp
        # exacto, mismo criterio que el resto de features seguras de esta seccion.
        df_autoconsumo = _serie_horaria(conn, "forecast", COLS_AUTOCONSUMO_PREV, idx)
        if "autoconsumo_estimado" in df_autoconsumo.columns:
            df_autoconsumo["autoconsumo_estimado"] = df_autoconsumo["autoconsumo_estimado"].astype("Int64")
        # era5_weather_agg es nativa cada 3 horas (verificado 22-ago-2026), no horaria -- al
        # reindexar a la rejilla horaria, 2 de cada 3 horas quedan NaN "de fabrica", no por un
        # hueco real. En el dataset diario esto queda escondido porque el promedio del dia usa
        # las horas que si tienen dato; aqui hay que interpolar explicitamente (limit=2, el
        # tamaño exacto del hueco entre dos lecturas de 3h) para no perder dos tercios del clima.
        df_clima = _serie_horaria(conn, "era5_weather_agg", COLS_CLIMA, idx, col_ts="ts")
        df_clima = df_clima.interpolate(limit=2)
        if "t2m_mean" in df_clima.columns:
            df_clima["t2m_mean"] = df_clima["t2m_mean"] - 273.15
        if "d2m_mean" in df_clima.columns:
            df_clima["d2m_mean"] = df_clima["d2m_mean"] - 273.15

        # --- Features reales, con lag horario directo (24h / 168h atras) ---
        df_demanda = _serie_horaria(conn, TABLA_DEMANDA_REAL, COLS_DEMANDA_REAL, idx)
        df_entsoe_real = _serie_horaria(conn, TABLA_ENTSOE_REAL, COLS_ENTSOE_REAL, idx)
        df_esios_real = _serie_horaria(conn, TABLA_ESIOS_REAL, COLS_ESIOS_REAL, idx)

        # FV limpia (misma formula que el dataset diario): GREATEST(0, entsoe.solar - esios.termosolar)
        df_solar = pd.read_sql(
            "SELECT e.datetime, GREATEST(0, e.solar_mw - s.ree_gsolter_mw) AS solar_fv_mw "
            "FROM entsoe_gen_data e JOIN esios_gen s ON e.datetime = s.datetime "
            "WHERE e.datetime BETWEEN %(start)s AND %(end)s",
            conn, params={"start": DATASET_START, "end": DATASET_END},
        )
        df_solar["datetime"] = pd.to_datetime(df_solar["datetime"], utc=True)
        df_solar_fv = df_solar.drop_duplicates(subset=["datetime"]).set_index("datetime").reindex(idx)

        # Hidraulica despachable fusionada (misma formula que el dataset diario)
        df_hidro = pd.read_sql(
            "SELECT datetime, hydro_reservoir_mw + COALESCE(pumping_gen_mw, 0) AS hydro_dispatch_mw "
            "FROM entsoe_gen_data WHERE datetime BETWEEN %(start)s AND %(end)s",
            conn, params={"start": DATASET_START, "end": DATASET_END},
        )
        df_hidro["datetime"] = pd.to_datetime(df_hidro["datetime"], utc=True)
        df_hydro_dispatch = df_hidro.drop_duplicates(subset=["datetime"]).set_index("datetime").reindex(idx)

        df_vecinos = _serie_horaria(conn, "spot_price", COLS_PRECIO_VECINOS, idx)

        # Capacidad disponible por tecnologia, horaria nativa desde el cambio de esquema del
        # 22-ago-2026 -- mismo tratamiento que el resto de reales (lag 24h/168h).
        df_capacidad = _serie_horaria(conn, "esios_capacity_available", COLS_CAPACIDAD_DISPONIBLE, idx)

        # PDBC -- ver docstring de COLS_PDBC arriba. None por defecto.
        # prefijo "pdbc_" obligatorio: wind_mw/nuclear_mw/ccgt_mw/etc. colisionan con nombres ya
        # usados en entsoe_gen_data/esios_capacity_available -- sin esto, el join produce columnas
        # de lag duplicadas (wind_mw_lag24h x2) y LightGBM revienta al construir el dataset.
        df_pdbc = _serie_horaria(conn, "esios_pdbc_gen", COLS_PDBC, idx) if pdbc else None
        if df_pdbc is not None:
            df_pdbc = df_pdbc.add_prefix("pdbc_")

        # --- Commodities: dato DIARIO, se difunde a las 24 horas del dia que corresponda ---
        df_comm = pd.read_sql(
            f"SELECT fecha, {', '.join(COLS_COMMODITIES)} FROM commodities "
            f"WHERE fecha BETWEEN %(start)s AND %(end)s",
            conn, params={"start": DATASET_START, "end": DATASET_END},
        )
    finally:
        conn.close()

    # entsoe_load=0 espurio (ver docs/notas_memoria_tfm.md nota 13) -- mismo fix que el diario.
    if "entsoe_load" in df_demanda.columns:
        df_demanda.loc[df_demanda["entsoe_load"] == 0, "entsoe_load"] = pd.NA

    # --- Ensamblar: reales con lag 24h/168h ---
    reales = pd.concat([df_demanda, df_entsoe_real, df_esios_real, df_solar_fv, df_hydro_dispatch,
                         precio.rename("precio_propio"), df_vecinos, df_capacidad], axis=1)
    if pdbc == "lag":
        reales = pd.concat([reales, df_pdbc], axis=1)
    lag_24h = reales.shift(LAG_1D_HORAS).add_suffix("_lag24h")
    lag_168h = reales.shift(LAG_7D_HORAS).add_suffix("_lag168h")

    # Indicador de disponibilidad para columnas con nulos masivos y ESTRUCTURALES (no aleatorios):
    # ree_gbattery_mw / ree_cbattery_mw solo tienen dato real desde que hay baterias de red
    # operativas -- 99,3% de nulos en train (ver docs/notas_memoria_tfm.md). En vez de imputar la
    # mediana y que el modelo trate ese valor como si fuera una lectura real, se añade una columna
    # binaria (1 = habia dato real esa hora, 0 = se va a imputar) para que el modelo pueda aprender
    # a distinguir ambos casos. La imputacion de la mediana sigue haciendose aparte, en el script
    # de entrenamiento (fillna(medianas)) -- este indicador solo la acompaña.
    COLS_INDICADOR_DISPONIBILIDAD = ["ree_gbattery_mw", "ree_cbattery_mw"]
    for col in COLS_INDICADOR_DISPONIBILIDAD:
        if col in reales.columns:
            lag_24h[f"{col}_lag24h_disponible"] = lag_24h[f"{col}_lag24h"].notna().astype(int)
            lag_168h[f"{col}_lag168h_disponible"] = lag_168h[f"{col}_lag168h"].notna().astype(int)

    pdbc_mismodia = None
    if pdbc == "mismodia_diagnostico":
        pdbc_mismodia = df_pdbc.add_prefix("PELIGRO_mismodia_")

    # --- Commodities: dia D = fecha local Madrid de la fila, menos 1 dia (mismo criterio "ya
    # ocurrio" que _features_dia_d del dataset diario) ---
    df_comm["fecha"] = pd.to_datetime(df_comm["fecha"]).dt.date
    df_comm = df_comm.sort_values("fecha").set_index("fecha").ffill()
    dia_d = (idx.tz_convert("Europe/Madrid") - pd.Timedelta(days=1)).date
    comm_por_fila = df_comm.reindex(dia_d)
    comm_por_fila.index = idx

    # --- Calendario: describe la propia hora objetivo (ya se sabe con certeza de antemano) ---
    idx_madrid = idx.tz_convert("Europe/Madrid")
    # Mecanismo iberico de tope al gas ("excepcion iberica", RDL 10/2022): vigente 15-jun-2022 a
    # 31-dic-2023, verificado de forma independiente (MITECO / prensa especializada, 25-ago-2026),
    # no solo tomado del script de un compañero. Es un cambio de regla del mercado que el modelo
    # no puede inferir de otras columnas -- fecha conocida con certeza de antemano, no hay fuga.
    # Caveat: el mecanismo estuvo legalmente vigente todo ese rango pero no siempre "activo" en la
    # practica (hubo tramos en que el precio del gas cayo por debajo del umbral y no llego a topar)
    # -- este flag marca la ventana LEGAL, un primer indicador estructural, no una medida exacta de
    # cuando el tope realmente influyo en el precio.
    TOPE_GAS_INICIO = pd.Timestamp("2022-06-15").date()
    TOPE_GAS_FIN = pd.Timestamp("2023-12-31").date()
    fecha_local = idx_madrid.date
    regimen_tope_gas = ((fecha_local >= TOPE_GAS_INICIO) & (fecha_local <= TOPE_GAS_FIN)).astype(int)
    calendario = pd.DataFrame({
        "hora": idx_madrid.hour,
        "dow": idx_madrid.dayofweek,
        "month": idx_madrid.month,
        "is_weekend": (idx_madrid.dayofweek >= 5).astype(int),
        "regimen_tope_gas": regimen_tope_gas,
    }, index=idx)

    piezas = [precio.rename("precio"), df_fcst, df_autoconsumo, df_ntc, df_fcst_entsoe, df_clima,
              lag_24h, lag_168h, comm_por_fila, calendario]
    if pdbc_mismodia is not None:
        piezas.append(pdbc_mismodia)
    dataset = pd.concat(piezas, axis=1)

    if solo_filas_validas:
        dataset = dataset[dataset["precio"].notna()].copy()

    dataset = dataset.round(2)
    dataset.index.name = "hora_objetivo"
    return dataset


def dividir_train_val_test_horario(dataset: pd.DataFrame):
    """Split cronologico, usando las MISMAS fronteras que el dataset diario (TRAIN_END/VAL_END),
    aplicadas al dia D que hace la prediccion (fecha local Madrid de la fila, menos 1 dia) -- para
    que un mismo dia caiga en el mismo split en los dos datasets y la comparacion sea limpia."""
    dia_d = pd.Series(
        (dataset.index.tz_convert("Europe/Madrid") - pd.Timedelta(days=1)).date,
        index=dataset.index,
    )
    mask_train = dia_d <= TRAIN_END
    mask_val = (dia_d > TRAIN_END) & (dia_d <= VAL_END)
    mask_test = dia_d > VAL_END
    return dataset[mask_train], dataset[mask_val], dataset[mask_test]


if __name__ == "__main__":
    print("Construyendo dataset maestro horario...")
    dataset = construir_dataset_horario()
    train, val, test = dividir_train_val_test_horario(dataset)
    print(f"dataset: {dataset.shape[0]} filas (horas) x {dataset.shape[1]} columnas")
    print(f"  train:      {len(train):>6} horas  ({train.index.min()} -> {train.index.max()})")
    print(f"  validation: {len(val):>6} horas  ({val.index.min()} -> {val.index.max()})")
    print(f"  test:       {len(test):>6} horas  ({test.index.min()} -> {test.index.max()})")

    out_path = Path(__file__).parent / "dataset_horario.csv"
    dataset.to_csv(out_path)
    print(f"guardado en: {out_path}")
