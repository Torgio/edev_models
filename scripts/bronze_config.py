"""Configuración compartida entre extracción y unión de la capa bronze.
Vive en scripts/ -- se importa desde extract_bronze.py y desde
notebooks/01_union_bronze.ipynb, para que no haya drift entre lo que uno
escribe y lo que el otro espera leer.
"""
from pathlib import Path

# Rutas ancladas a la posición de este archivo, no al cwd desde donde se
# ejecute el script o el notebook.
REPO_ROOT = Path(__file__).resolve().parent.parent
BRONZE_DIR = REPO_ROOT / "data" / "bronze"

TZ_LOCAL = "Europe/Madrid"

# Tabla que define el rango del calendario: la más limpia / con histórico más
# largo y confiable (ver auditoría: 2020->hoy, sin huecos conocidos).
ANCHOR_TABLE = "entsoe_gen_data"

DROP_COLS = ["updated_at"]

# Agregar una tabla nueva = una entrada acá. Ni extract_bronze.py ni
# 01_union_bronze.ipynb necesitan tocarse -- salvo que la granularidad sea
# nueva (hoy soportadas: hourly, daily, 3h).
#
# "columns": lista de columnas de origen a traer, ADEMÁS de time_col. Filtra
# ya en el SELECT de Postgres -- no se descarta después de traer todo.
# Selección según la matriz de decisiones del equipo (generación, load_inter,
# forecast, commodities, era5) -- ver docs/decisiones_datos.md.
#
# "grain": cómo se une esta tabla al calendario en 01_union_bronze.ipynb.
#   - "hourly" (default): join exacto por ts_utc.
#   - "daily": el valor de un día se difunde a las 24h de ese día -- join por
#     date_local, no por ts_utc (una fila de origen, muchas del calendario).
#   - "3h": el valor de un paso de 3h se sostiene hasta el próximo dato --
#     join por el ts_utc más reciente hacia atrás (merge_asof), no exacto.
TABLES = {
    "entsoe_gen_data": {
        "time_col": "datetime",
        "prefix": "entsoe",
        "grain": "hourly",
        "columns": [
            "solar_mw", "wind_mw", "hydro_run_river_mw", "hydro_reservoir_mw",
            "pumping_gen_mw", "pumping_cons_mw", "biomass_mw", "waste_mw",
            "other_renewable_mw", "oil_mw",
            # gas_mw: DESCARTADA por D-02 (mezcla CCGT + cogeneración, se prefieren
            # ree_gccgas_mw + ree_gotherthermal_mw por separado). Se trae solo para
            # reproducir la evidencia de esa decisión. No usar para modelar.
            "gas_mw",
        ],
    },
    "esios_gen": {
        "time_col": "datetime",
        "prefix": "esios_gen",
        "grain": "hourly",
        "columns": [
            "ree_gsolter_mw", "ree_gbattery_mw", "ree_cbattery_mw",
            "ree_gnuclear_mw", "ree_gccgas_mw", "ree_gotherthermal_mw", "ree_gcoal_mw",
            # ree_gsolar_mw: NO es una columna "final" (la matriz de generación no la
            # selecciona -- se usa calc_solar_fv_mw derivada en su lugar). Se trae
            # solo para reproducir la evidencia de D-03 (contaminación de autoconsumo
            # desde dic-2025) contra entsoe_solar_mw. No usar para modelar.
            "ree_gsolar_mw",
            # ree_gwind_mw / ree_ghidro_mw: descartadas por la matriz de generación
            # (coincide con ENTSO-E / mezcla hidráulica+bombeo respectivamente). Se
            # traen solo para el bloque E (consistencia cruzada), no para modelar.
            "ree_gwind_mw", "ree_ghidro_mw",
        ],
    },
    "spot_price": {
        "time_col": "datetime",
        "prefix": "spot",
        "grain": "hourly",
        # 14 columnas de precio. España por tres fuentes: sus medias coinciden
        # (85,84 / 85,84 / 85,85), así que son intercambiables -- se traen las tres para
        # poder cerrar esa comprobación con evidencia y documentar la elección de una vez.
        # Las once zonas europeas tienen FUGA para D+1, porque todas las zonas SDAC se casan
        # a la vez a las 12:00: el precio francés de D+1 no existe cuando hay que predecir
        # el español de D+1. Se traen igualmente porque el precio de D sí es feature legítima
        # y porque el análisis de congestión ES-PT y ES-FR sale de aquí, sin datos externos.
        "columns": [
            "es_esios", "es_entsoe", "es_omie",
            "pt_entsoe", "pt_omie",
            "fr_entsoe", "de_lu_entsoe", "it_nord_entsoe", "ch_entsoe",
            "be_entsoe", "nl_entsoe", "at_entsoe", "pl_entsoe", "cz_entsoe",
        ],
    },
    "esios_capacity_available": {
        "time_col": "datetime",
        "prefix": "cap_disp",
        "grain": "hourly",
        # Esquema real confirmado 26-ago vía information_schema/pgAdmin -- distinto al
        # catálogo original: sin gas_turbine_mw (no existe en esta tabla), y coal_mw
        # se llama coal_antracita_mw. 6 tecnologías, no 7.
        "columns": ["hydro_mw", "pump_mw", "nuclear_mw", "coal_antracita_mw", "ccgt_mw", "fuel_mw"],
    },
    "esios_capacity_installed": {
        "time_col": "date",
        "prefix": "cap_inst",
        "grain": "daily",
        # 18 tecnologías individuales (información_schema real, confirmado 21-ago).
        # Excluidos los 5 total_* (total_mw, total_renewable_mw, total_nonrenewable_mw,
        # total_hybrid_mw, total_autoconsume_mw): colinealidad exacta con la suma de sus
        # componentes, mismo criterio que en la matriz de generación.
        # ccgt_mw/nuclear_mw/pump_mw/fuel_mw: constantes según D-04 (evidencia, no
        # features). autoconsume_battery_mw: indicador 2366, congelado en origen (D-04).
        # autoconsume_solar_pv_mw: validación cruzada de D-03 (sí varía).
        "columns": [
            "hydro_mw", "pump_mw", "wind_mw", "wind_hybrid_mw",
            "solar_pv_mw", "solar_thermal_mw", "solar_pv_hybrid_mw",
            "other_renewable_mw", "waste_nonrenewable_mw", "waste_renewable_mw",
            "battery_hybrid_mw", "autoconsume_solar_pv_mw", "autoconsume_battery_mw",
            "nuclear_mw", "coal_mw", "fuel_mw", "ccgt_mw", "cogeneration_mw",
        ],
    },
    "load_inter": {
        "time_col": "datetime",
        "prefix": "load_inter",
        "grain": "hourly",
        "columns": [
            "ree_load", "entsoe_load",
            "ree_ntc_impfr", "ree_ntc_expfr", "ree_netflow_fr",
            "ree_ntc_imppt", "ree_ntc_exppt", "ree_netflow_pt",
            "ree_ntc_impma", "ree_ntc_expma", "ree_netflow_ma",
            "total_net_flow_mw", "gen_peninsular_mw",
        ],
    },
    "esios_forecast_da": {
        "time_col": "datetime",
        "prefix": "forecast",
        "grain": "hourly",
        # Mismas 3 columnas que COLS_SEGURAS_FORECAST en construir_dataset_maestro.py --
        # importadas de ahí en el notebook de unión, no reescritas a mano acá.
        "columns": [
            "demanda_mercado_prev_mw", "gen_wind_prev_mw", "gen_solar_pv_prev_mw",
            # demanda_residual_prev_mw: EXCLUIDA del maestro por revisión 10-14 días
            # (la previsión se sigue actualizando después de publicada). Se trae solo
            # para chequear si ADEMÁS arrastra contaminación de autoconsumo, comparando
            # contra demanda_mercado_prev_mw (documentada "sin autoconsumo"). No usar
            # para modelar.
            "demanda_residual_prev_mw",
        ],
    },
    "commodities": {
        "time_col": "fecha",
        "prefix": "commodities",
        "grain": "daily",
        "columns": ["gas_mibgas", "co2_eua_dec", "gas_ttf_m1"],
    },
    "era5_weather_agg": {
        "time_col": "ts",
        "prefix": "era5",
        "grain": "3h",
        # Todas las agregadas; tensor_path/tensor_index quedan fuera del bronce de EDA
        # a propósito -- no aportan nada a un análisis tabular, y los tensores en sí
        # se consultan aparte (ver BRONZE_README.md).
        "columns": [
            "t2m_mean", "d2m_mean", "wind10_mean", "wind100_mean", "wind_gust10_mean",
            "ssrd_mean", "tcc_mean", "tp_mean", "msl_mean",
        ],
    },
}

# Columnas derivadas -- se calculan DESPUÉS del merge, porque cruzan más de una
# tabla. "inputs" ya son los nombres CON el prefijo de tabla (post-merge).
DERIVED_COLUMNS = [
    {
        "name": "calc_solar_fv_mw",
        "inputs": ["entsoe_solar_mw", "esios_gen_ree_gsolter_mw"],
        "formula": lambda df: (df["entsoe_solar_mw"] - df["esios_gen_ree_gsolter_mw"]).clip(lower=0),
    },
    {
        "name": "calc_hydro_dispatch_mw",
        "inputs": ["entsoe_hydro_reservoir_mw", "entsoe_pumping_gen_mw"],
        "formula": lambda df: df["entsoe_hydro_reservoir_mw"] + df["entsoe_pumping_gen_mw"].fillna(0),
    },
    {
        # No es "cuál demanda es correcta" -- eso ya lo resolvió D-03 (entsoe_load).
        # Es que la resta en sí misma estima el autoconsumo peninsular (entra por
        # los dos lados del balance) -- ver D-03, validado cruzado contra
        # esios_capacity_installed.autoconsume_solar_pv_mw.
        "name": "calc_autoconsumo_mw",
        "inputs": ["load_inter_ree_load", "load_inter_entsoe_load"],
        "formula": lambda df: df["load_inter_ree_load"] - df["load_inter_entsoe_load"],
    },
]

# Convención de nombres de archivo -- definida una sola vez acá para que
# extract_bronze.py (que escribe) y 01_union_bronze.ipynb (que lee) no
# puedan desincronizarse.
RAW_SUFFIX = "_raw"
UNIFIED_FILENAME = "bronze_unificado.parquet"


def raw_path(table_name: str) -> Path:
    """Ruta del parquet bronze de una tabla, ej. entsoe_gen_data -> entsoe_gen_data_raw.parquet"""
    return BRONZE_DIR / f"{table_name}{RAW_SUFFIX}.parquet"
