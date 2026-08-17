"""
TFM Energia UCM — Pipeline de ingesta capa Bronze
Lee todas las tablas fuente desde PostgreSQL y las concatena en un unico
DataFrame "bronze" (una fila por registro, con la tabla de origen marcada
en la columna source_df).
 
Este fichero usa config.py para obtener la conexion a la base de datos.
No contiene credenciales: estas se leen desde credentials.json (ver config.py).
"""



import sys
from pathlib import Path
from psycopg2.extras import execute_values
from sqlalchemy import create_engine
import pandas as pd

## sys.path.append("/Users/Powan/Desktop/Maestria/TFM/edev_models/ingesta/config.py")
## from config import load_config



##_, db_config = load_config()

sys.path.append(str(Path(__file__).parent))
from config import load_config


TABLE_NAMES  = [
    "commodities",
    "ecmwf_forecast_agg",
    "entsoe_forecast_da",
    "entsoe_gen_data",
    "entsoe_load_inter",
    "era5_weather_agg",
    "esios_capacity_available",
    "esios_capacity_installed",
    "esios_forecast_da",
    "esios_gen",
    "esios_load_inter",
    "esios_pbf_bilateral",
    "esios_pbf_gen",
    "esios_pbf_load_inter",
    "spot_price",
    ##"trayport_daily", No se va a utilizar 
    "trayport_daily_ohlc",
    "trayport_trades"

]

def get_engine():
    """
    Crea y devuelve el engine de SQLAlchemy a partir de las credenciales
    cargadas por config.load_config().
 
    Uso:
        from bronzeDF_pipeline import get_engine
        engine = get_engine()
    """
    _, db_config = load_config()
 
    return create_engine(
        f"postgresql+psycopg2://{db_config['user']}:{db_config['password']}"
        f"@{db_config['host']}:{db_config['port']}/{db_config['dbname']}"
    )


def load_source_tables(engine, table_names=TABLE_NAMES):
    """
    Lee cada tabla de table_names desde la base de datos y devuelve un
    diccionario {nombre_variable: DataFrame}.
 
    Uso:
        engine = get_engine()
        dfs = load_source_tables(engine)
    """
    dfs = {}
 
    for name in table_names:
        var_name = f"df_{name}"
        dfs[var_name] = pd.read_sql(f"SELECT * FROM {name}", engine)
 
    return dfs


def build_bronze_df(dfs):
    """
    Concatena el diccionario de DataFrames en un unico DataFrame Bronze,
    anadiendo una columna source_df que identifica la tabla de origen
    de cada fila.
 
    Uso:
        dfs = load_source_tables(engine)
        df_bronze = build_bronze_df(dfs)
    """
    return pd.concat(
        [df.assign(source_df=name) for name, df in dfs.items()],
        ignore_index=True,
    )


def main():
    engine = get_engine()
    dfs = load_source_tables(engine)
    df_bronze = build_bronze_df(dfs)
 
    print(df_bronze.shape)
    print(df_bronze.iloc[0])
 
    return df_bronze
 
 
if __name__ == "__main__":
    main()




