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
        "columns": ["demanda_mercado_prev_mw", "gen_wind_prev_mw", "gen_solar_pv_prev_mw"],
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