"""
columnas_clave.py -- segundo paso, después de inspect_schema.py.

Qué hace, y por qué en este orden
---------------------------------
`inspect_schema.py` dio la foto de las 22 tablas. Esto baja al detalle de las que van a entrar
al bronce y hace tres cosas que el primero no hace:

1. **Muestra los valores reales**, no sólo los nombres. Un nombre de columna no dice si la
   unidad es MW o kW, ni si el precio viene en EUR/MWh o en céntimos.
2. **Genera las entradas de `bronze_config.TABLES` ya rellenas** con los nombres de columna
   reales, en `eda/bronze_config_generado.py`. Se revisa y se pega: se acabó rellenar `TODO`
   a mano, que es como se cuelan los nombres inventados.
3. **Corre los cuatro diagnósticos concretos** que quedaron abiertos en el EDA:
   - por qué `esios_capacity_available` multiplicó el calendario x24
   - qué hay dentro de `trayport_daily_ohlc` (formato largo, ~7 filas por fecha)
   - qué registró `pipeline_log` cuando `entsoe_load` se fue a cero (hallazgo C.1)
   - si `load_inter` y `entsoe_load_inter` son la misma información por dos caminos

Sólo lectura. Nunca `SELECT *` sobre tablas grandes sin `LIMIT`.

Uso
---
    python eda/columnas_clave.py                 # todo
    python eda/columnas_clave.py --sin-muestras  # sin las filas de ejemplo (salida más corta)
"""

from __future__ import annotations

import sys
import warnings
from datetime import datetime
from pathlib import Path

import pandas as pd
import psycopg2

warnings.filterwarnings("ignore", message=".*SQLAlchemy.*")

RAIZ = Path(__file__).resolve().parent.parent
SALIDA_CFG = RAIZ / "eda" / "bronze_config_generado.py"
SALIDA_MD = RAIZ / "docs" / "columnas_clave_tablas_principales.md"

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 200)

# Tablas que van a entrar al bronce, por orden de prioridad para el EDA.
PRIORITARIAS = [
    ("spot_price",               "hourly", "spot_",     "TARGET. Con varias fuentes contrastadas: traerlas TODAS (bloque F.8)."),
    ("forecast",                 "hourly", "fc_",       "Sin fuga. Los nombres de la matriz final del equipo."),
    ("esios_forecast_da",        "hourly", "esios_fc_", "Sin fuga. El bronce sólo trae 3 de sus columnas."),
    ("entsoe_forecast_da",       "hourly", "ent_fc_",   "Sin fuga. Segunda fuente de previsión: permite contraste."),
    ("esios_capacity_available", "hourly", "cap_disp_", "OJO: es HORARIA (58.361 filas), no diaria. Ver diagnóstico 1."),
    ("esios_capacity_installed", "daily",  "cap_inst_", "Diaria de verdad (2.430 filas). Varias columnas constantes (D-04)."),
    ("generation",               "hourly", "gen_",      "Con fuga: sólo con lag D-1/D-7."),
    ("esios_pdbc_gen",           "hourly", "pdbc_",     "Con fuga: contemporáneo es circular."),
    ("ecmwf_forecast_agg",       "3h",     "ecmwf_",    "SÓLO 168 FILAS: ventana móvil, no histórico. Ver aviso al final."),
    ("trayport_daily_ohlc",      "daily",  "tp_",       "Formato largo (~7 filas/fecha): hay que pivotar antes de unir."),
]

# Columnas que son metadatos de ingesta, no features. Se excluyen de la lista generada.
METADATOS = {
    "id", "created_at", "updated_at", "inserted_at", "load_ts", "ingested_at",
    "source", "fuente", "origen", "version", "run_id", "batch_id",
}

CANDIDATOS_TS = ("ts_utc", "datetime", "datetime_utc", "ts", "date_local", "date", "fecha", "day")


def conectar():
    """Misma conexión que scripts/extract_bronze.py: load_config() -> (_, db_config)."""
    sys.path.insert(0, str(RAIZ / "ingesta"))
    from config import load_config

    _, db_config = load_config()
    print(f"Conectando -> {db_config.get('host', '?')}/{db_config.get('dbname', '?')}\n")
    return psycopg2.connect(**db_config)


def columnas(con, tabla: str) -> pd.DataFrame:
    q = ("SELECT column_name, data_type FROM information_schema.columns "
         "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position")
    return pd.read_sql(q, con, params=(tabla,))


def muestra(con, tabla: str, ts: str | None, n: int = 3) -> pd.DataFrame:
    orden = f'ORDER BY "{ts}" DESC' if ts else ""
    return pd.read_sql(f'SELECT * FROM public."{tabla}" {orden} LIMIT {n}', con)


def rango_valores(con, tabla: str, cols: list[str], ts: str | None) -> pd.DataFrame:
    """min/max/media de las columnas numéricas: es lo que revela la unidad real."""
    if not cols:
        return pd.DataFrame()
    partes = []
    for c in cols:
        partes += [f'MIN("{c}") AS "min__{c}"', f'MAX("{c}") AS "max__{c}"',
                   f'AVG("{c}") AS "avg__{c}"']
    fila = pd.read_sql(f'SELECT {", ".join(partes)} FROM public."{tabla}"', con).iloc[0]
    return pd.DataFrame(
        [{"columna": c,
          "min": fila[f"min__{c}"], "max": fila[f"max__{c}"],
          "media": round(float(fila[f"avg__{c}"]), 2) if fila[f"avg__{c}"] is not None else None}
         for c in cols]
    ).set_index("columna")


# ---------------------------------------------------------------------------
# Diagnósticos abiertos del EDA
# ---------------------------------------------------------------------------

def diag_capacity(con) -> None:
    print("\n" + "=" * 78)
    print("DIAGNÓSTICO 1 · el origen de la duplicación x24")
    print("=" * 78)
    # `esios_capacity_available` NO tiene columna `date`: su clave es `datetime`. Era el
    # motivo de que este diagnóstico fallara en las tres primeras corridas.
    q = """
        SELECT 'esios_capacity_available' AS tabla, COUNT(*) AS filas,
               COUNT(DISTINCT (datetime AT TIME ZONE 'Europe/Madrid')::date) AS fechas,
               ROUND(COUNT(*)::numeric /
                     NULLIF(COUNT(DISTINCT (datetime AT TIME ZONE 'Europe/Madrid')::date), 0), 2)
                   AS filas_por_fecha
        FROM esios_capacity_available
        UNION ALL
        SELECT 'esios_capacity_installed', COUNT(*), COUNT(DISTINCT date),
               ROUND(COUNT(*)::numeric / NULLIF(COUNT(DISTINCT date), 0), 2)
        FROM esios_capacity_installed
    """
    # La pregunta que de verdad decide: ¿esas 24 filas por día son copias o valores distintos?
    q2 = """
        SELECT COUNT(*) FILTER (WHERE distintos > 1) AS dias_con_valor_variable,
               COUNT(*)                              AS dias_totales
        FROM (
          SELECT (datetime AT TIME ZONE 'Europe/Madrid')::date AS d,
                 COUNT(DISTINCT ccgt_mw) AS distintos
          FROM esios_capacity_available GROUP BY 1
        ) t
    """
    try:
        print(pd.read_sql(q, con).to_string(index=False))
        print("\n¿Varía el valor dentro del día?")
        r2 = pd.read_sql(q2, con).iloc[0]
        print(f"  días con más de un valor de ccgt_mw: {r2['dias_con_valor_variable']:,} "
              f"de {r2['dias_totales']:,}")
        if r2["dias_con_valor_variable"] == 0:
            print("\n  -> Las 24 filas de cada día son COPIAS. El dato es diario y el "
                  "horizonte se perdió.\n"
                  "     Corrección: deduplicar en la extracción y mantener grain='daily'.\n"
                  "     Confirma además D-01: la ingesta guarda un único valor por fecha.")
        else:
            print("\n  -> La capacidad SÍ varía dentro del día: la tabla es horaria de verdad.\n"
                  "     Corrección: declarar grain='hourly'. Y replantear el diagnóstico de "
                  "D-01,\n     porque la tabla conserva más estructura de la que suponíamos.")
    except Exception as e:
        print(f"  (no se pudo: {e} -- quizá la columna de fecha se llama distinto)")


def diag_trayport(con) -> None:
    print("\n" + "=" * 78)
    print("DIAGNÓSTICO 2 · qué hay dentro de trayport_daily_ohlc")
    print("=" * 78)
    cols = columnas(con, "trayport_daily_ohlc")["column_name"].tolist()
    print("Columnas:", cols)
    # La columna que discrimina el producto: la primera de tipo texto que no sea la fecha.
    texto = pd.read_sql(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='trayport_daily_ohlc' "
        "AND data_type IN ('text','character varying') ORDER BY ordinal_position", con
    )["column_name"].tolist()
    if texto:
        c = texto[0]
        print(f"\nValores distintos de '{c}' (el producto cotizado):")
        print(pd.read_sql(
            f'SELECT "{c}", COUNT(*) AS filas, MIN(fecha) AS desde, MAX(fecha) AS hasta '
            f'FROM trayport_daily_ohlc GROUP BY "{c}" ORDER BY filas DESC LIMIT 20', con
        ).to_string(index=False))
        print(
            "\nEs formato largo: una fila por (fecha, producto). Antes de unirla al bronce hay\n"
            "que pivotar a ancho, o el merge por fecha multiplicará el calendario igual que\n"
            "hizo la tabla de capacidad."
        )


def diag_pipeline_log(con) -> None:
    print("\n" + "=" * 78)
    print("DIAGNÓSTICO 3 · pipeline_log en las fechas del hallazgo C.1")
    print("=" * 78)
    print("Los 9 ceros espurios de entsoe_load: 17-mar-2026 11:00 UTC y 30-jun/1-jul-2026 de madrugada.")
    cols = columnas(con, "pipeline_log")["column_name"].tolist()
    print("Columnas de pipeline_log:", cols)
    ts = next((c for c in CANDIDATOS_TS + ("created_at", "run_ts", "timestamp") if c in cols), None)
    if ts is None:
        print("  (sin columna temporal reconocible: revisar a mano)")
        return
    q = f"""
        SELECT * FROM pipeline_log
        WHERE "{ts}"::date BETWEEN DATE '2026-06-29' AND DATE '2026-07-02'
           OR "{ts}"::date BETWEEN DATE '2026-03-16' AND DATE '2026-03-18'
        ORDER BY "{ts}"
    """
    try:
        r = pd.read_sql(q, con)
        print(f"\n{len(r)} registros en esas ventanas:")
        print(r.to_string(index=False) if len(r) else "  (ninguno)")
        print(
            "\nSi aparece un fallo o un reintento en esas horas, el hallazgo C.1 pasa de\n"
            "hipótesis ('pinta a corte de ingesta') a evidencia documentada. Si no aparece\n"
            "nada, también es información: el fallo no quedó registrado, y eso es una\n"
            "limitación del propio sistema de monitorización que conviene decir."
        )
    except Exception as e:
        print(f"  (no se pudo: {e})")


def diag_load_inter(con) -> None:
    print("\n" + "=" * 78)
    print("DIAGNÓSTICO 4 · load_inter vs entsoe_load_inter -- ¿dos caminos para el mismo dato?")
    print("=" * 78)
    a = set(columnas(con, "load_inter")["column_name"])
    b = set(columnas(con, "entsoe_load_inter")["column_name"])
    print(f"load_inter        : {len(a)} columnas")
    print(f"entsoe_load_inter : {len(b)} columnas")
    print(f"\nEn común ({len(a & b)}): {sorted(a & b)}")
    print(f"\nSólo en load_inter ({len(a - b)}): {sorted(a - b)}")
    print(f"\nSólo en entsoe_load_inter ({len(b - a)}): {sorted(b - a)}")
    print(
        "\nDe aquí sale la respuesta a la pregunta abierta desde el hallazgo C.1: qué columna\n"
        "de demanda alimenta el maestro, y si arrastra el autoconsumo de D-03."
    )


# ---------------------------------------------------------------------------

def main() -> None:
    sin_muestras = "--sin-muestras" in sys.argv
    con = conectar()

    bloques_cfg = []
    md = [
        "# Columnas clave de las tablas principales",
        "",
        f"*Generado por `eda/columnas_clave.py` el {datetime.now():%d-%m-%Y %H:%M}.*",
        "",
        "Complementa `columnas_bronce_eda.md` (que cubre las 22 tablas) con el detalle de las "
        "que van a entrar al bronce: rangos de valores reales, que son los que revelan la "
        "unidad, y una muestra de filas.",
        "",
    ]

    for tabla, grain, prefijo, nota in PRIORITARIAS:
        cols = columnas(con, tabla)
        if cols.empty:
            print(f"--  {tabla}: no existe")
            continue

        nombres = cols["column_name"].tolist()
        ts = next((c for c in CANDIDATOS_TS if c in nombres), None)
        utiles = [c for c in nombres if c not in METADATOS and c != ts]
        numericas = cols[
            cols["data_type"].isin(["numeric", "double precision", "real", "integer", "bigint", "smallint"])
        ]["column_name"].tolist()
        numericas = [c for c in numericas if c not in METADATOS]

        print("\n" + "=" * 78)
        print(f"{tabla}   (grain propuesto: {grain}, prefijo: {prefijo})")
        print(f"  {nota}")
        print("=" * 78)
        print(f"Clave temporal: {ts}")
        print(f"Columnas útiles ({len(utiles)}): {utiles}")

        md += [f"## `{tabla}`", "", f"*{nota}*", "",
               f"**Clave temporal:** `{ts}` · **grain propuesto:** `{grain}` · "
               f"**prefijo:** `{prefijo}`", ""]

        if numericas:
            rangos = rango_valores(con, tabla, numericas, ts)
            print("\nRangos reales (aquí se ve la unidad):")
            print(rangos.to_string())
            md += ["| Columna | min | max | media |", "|---|---|---|---|"]
            for c, r in rangos.iterrows():
                md.append(f"| `{c}` | {r['min']} | {r['max']} | {r['media']} |")
            md.append("")

        if not sin_muestras:
            m = muestra(con, tabla, ts)
            print("\nÚltimas filas:")
            print(m.to_string(index=False))

        # Entrada de bronze_config ya rellena
        lineas_cols = "\n".join(f'            "{c}",' for c in utiles)
        bloques_cfg.append(
            f'    "{tabla}": {{\n'
            f'        "grain": "{grain}",\n'
            f'        "ts_column": "{ts}",\n'
            f'        "prefix": "{prefijo}",\n'
            f'        "columns": [\n{lineas_cols}\n        ],\n'
            f'        "nota": "{nota}",\n'
            f'    }},'
        )

    # Diagnósticos
    diag_capacity(con)
    diag_trayport(con)
    diag_pipeline_log(con)
    diag_load_inter(con)

    con.close()

    cabecera = (
        '"""\n'
        "bronze_config_generado.py -- entradas de TABLES con los nombres REALES de columna.\n\n"
        f"Generado por eda/columnas_clave.py el {datetime.now():%d-%m-%Y %H:%M}.\n\n"
        "Revisar antes de pegar en bronze_config.py. En particular:\n"
        "  - quitar las columnas que no aporten al modelado (el bronce no es un volcado)\n"
        "  - confirmar el grain de cada tabla contra su nº de filas por fecha\n"
        "  - trayport_daily_ohlc necesita pivotarse a ancho antes de unirse\n"
        '"""\n\n'
        "TABLES_GENERADO = {\n"
    )
    SALIDA_CFG.parent.mkdir(parents=True, exist_ok=True)
    SALIDA_CFG.write_text(cabecera + "\n".join(bloques_cfg) + "\n}\n", encoding="utf-8")

    SALIDA_MD.parent.mkdir(parents=True, exist_ok=True)
    SALIDA_MD.write_text("\n".join(md), encoding="utf-8")

    print("\n" + "=" * 78)
    print(f"Escrito: {SALIDA_CFG}")
    print(f"Escrito: {SALIDA_MD}")
    print("=" * 78)
    print(
        "\nRECORDATORIO sobre ecmwf_forecast_agg: 168 filas es una ventana móvil de días, no\n"
        "histórico. Sin previsión meteorológica histórica no se puede entrenar con la meteo\n"
        "que existirá en producción, sólo con ERA5, que es reanálisis. Todo resultado que use\n"
        "meteorología es por tanto una COTA SUPERIOR optimista, y hay que decirlo así en la\n"
        "memoria. No es un fallo del EDA: es una limitación estructural del dato."
    )


if __name__ == "__main__":
    main()
