"""
inspect_schema.py -- radiografía de las tablas de `tfm_energia`.

Para qué existe
---------------
1. Genera `docs/columnas_bronce_eda.md`, hoy citado en `BRONCE_README.md` pero inexistente.
2. Da los nombres reales de columna de las tablas que todavía no están en `bronze_config.py`
   (`spot_price`, `forecast`, `generation`, `esios_pdbc_gen`, `esios_pbf_*`,
   `ecmwf_forecast_agg`, `trayport_*`), para completar el registro sin adivinar.
3. Avisa de las tablas con la clave temporal repetida -- el chequeo que habría cazado la
   duplicación x24 del parquet unificado antes de que sobreviviera a un EDA entero.

Sólo lectura. No usa `SELECT *`: la lista de columnas se construye desde `information_schema`.

Conexión
--------
La misma que `scripts/extract_bronze.py`: `ingesta/config.py` -> `load_config()`, que devuelve
una tupla `(_, db_config)`, y de ahí `psycopg2.connect(**db_config)`. No hay variables de
entorno ni `.env` en este proyecto: la configuración vive en `ingesta/config.py` y punto.

Uso
---
    python eda/inspect_schema.py                      # todas las tablas
    python eda/inspect_schema.py spot_price forecast  # sólo algunas
    python eda/inspect_schema.py --sin-perfil         # estructura sin escanear filas
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import psycopg2

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "docs" / "columnas_bronce_eda.md"

# Nombres habituales de la columna temporal, en orden de preferencia.
CANDIDATOS_TS = ("ts_utc", "datetime", "datetime_utc", "ts", "date_local", "date", "fecha", "day")

# Clasificación de fuga declarada a mano: es una decisión del equipo, no algo que la base sepa.
FRONTERA = {
    "spot_price": ("TARGET", "Casación 12:00 de D, publicación ~12:45. El de D+1 es lo que se predice."),
    "forecast": ("SIN FUGA", "Previsiones REE de D+1 publicadas antes de las 11:00 de D-1 (Circular 4/2019)."),
    "esios_forecast_da": ("SIN FUGA", "Previsión day-ahead de ESIOS."),
    "entsoe_forecast_da": ("SIN FUGA", "Previsión day-ahead de ENTSO-E."),
    "ecmwf_forecast_agg": ("SIN FUGA", "Previsión meteorológica: la que existe en producción."),
    "esios_capacity_available": ("CONDICIONAL", "D-01: la ingesta guarda una fila por date creada a las 21:05."),
    "esios_capacity_installed": ("SIN FUGA", "Potencia instalada. Varias columnas constantes (D-04)."),
    "era5_weather_agg": ("CON FUGA", "Reanálisis. Sólo con lag o como ablación de meteo perfecta."),
    "generation": ("CON FUGA", "Generación real. Usar lag D-1/D-7."),
    "entsoe_gen_data": ("CON FUGA", "Generación real ENTSO-E. Usar lag."),
    "esios_gen": ("CON FUGA", "Generación real ESIOS. Usar lag."),
    "load_inter": ("CON FUGA", "Demanda e interconexiones reales. Usar lag."),
    "entsoe_load_inter": ("CON FUGA", "Ídem, fuente ENTSO-E. ¿Duplica load_inter?"),
    "esios_pdbc_gen": ("CON FUGA", "Misma casación que el precio -> circular en contemporáneo."),
    "esios_pbf_gen": ("CON FUGA", "Programa base de funcionamiento."),
    "esios_pbf_bilateral": ("CON FUGA", "Programa base, contratación bilateral."),
    "esios_pbf_load_inter": ("CON FUGA", "Programa base, demanda e interconexiones."),
    "commodities": ("CON DESFASE", "TTF y EUA cierran ~17:30 -> el último disponible a las 12:00 de D es D-2."),
    "trayport_daily": ("CON DESFASE", "Verificar hora de cierre antes de decidir el desfase."),
    "trayport_daily_ohlc": ("CON DESFASE", "Ídem."),
    "trayport_trades": ("CON DESFASE", "Ídem. Verificar además granularidad y unidad."),
    "pipeline_log": ("OPERATIVA", "Metadatos de ingesta. No es feature: sirve para auditar huecos."),
}


# ---------------------------------------------------------------------------
# Conexión -- la misma que usa todo el proyecto
# ---------------------------------------------------------------------------

def conectar():
    """Misma conexión que scripts/extract_bronze.py: load_config() -> (_, db_config)."""
    # Posición 0 a propósito: que gane ingesta/config.py sobre cualquier otro `config`
    # instalado en el entorno de Anaconda.
    sys.path.insert(0, str(RAIZ / "ingesta"))
    from config import load_config

    _, db_config = load_config()
    print(f"Conectando -> {db_config.get('host', '?')}/{db_config.get('dbname', '?')}")
    return psycopg2.connect(**db_config)


# ---------------------------------------------------------------------------
# Introspección
# ---------------------------------------------------------------------------

def listar_tablas(con) -> list[str]:
    q = ("SELECT table_name FROM information_schema.tables "
         "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name")
    return pd.read_sql(q, con)["table_name"].tolist()


def columnas(con, tabla: str) -> pd.DataFrame:
    q = ("SELECT column_name, data_type, is_nullable FROM information_schema.columns "
         "WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position")
    return pd.read_sql(q, con, params=(tabla,))


def perfil(con, tabla: str, cols: pd.DataFrame) -> dict:
    """Filas, rango temporal, filas por marca temporal y % de nulos por columna."""
    nombres = cols["column_name"].tolist()
    ts = next((c for c in CANDIDATOS_TS if c in nombres), None)

    partes = ["COUNT(*) AS n_filas"]
    if ts:
        partes += [f'MIN("{ts}") AS ts_min', f'MAX("{ts}") AS ts_max',
                   f'COUNT(DISTINCT "{ts}") AS ts_distintos']
    for c in nombres:
        partes.append(f'SUM(CASE WHEN "{c}" IS NULL THEN 1 ELSE 0 END) AS "nulos__{c}"')

    fila = pd.read_sql(f'SELECT {", ".join(partes)} FROM public."{tabla}"', con).iloc[0]

    n = int(fila["n_filas"]) or 1
    return {
        "n_filas": int(fila["n_filas"]),
        "ts_col": ts,
        "ts_min": fila.get("ts_min"),
        "ts_max": fila.get("ts_max"),
        "ts_distintos": int(fila["ts_distintos"]) if ts else None,
        "nulos": {c: round(float(fila[f"nulos__{c}"]) / n * 100, 2) for c in nombres},
    }


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sin_perfil = "--sin-perfil" in sys.argv

    con = conectar()
    tablas = args or listar_tablas(con)
    print(f"{len(tablas)} tablas a inspeccionar\n")

    lineas = [
        "# Columnas de la base — referencia para el EDA",
        "",
        f"*Generado por `eda/inspect_schema.py` el {datetime.now():%d-%m-%Y %H:%M}. "
        "No editar a mano: se regenera.*",
        "",
        "La columna **frontera** no sale de la base: es la clasificación de fuga acordada por el "
        "equipo y vive en el diccionario `FRONTERA` de ese script. Si cambia una decisión, se "
        "cambia ahí y se regenera este documento.",
        "",
        "| Tabla | Filas | Rango temporal | Clave repetida | Frontera |",
        "|---|---|---|---|---|",
    ]

    detalle, avisos = [], []
    for t in tablas:
        cols = columnas(con, t)
        if cols.empty:
            print(f"  {t}: no existe o sin columnas visibles")
            continue

        p = None if sin_perfil else perfil(con, t, cols)
        veredicto, motivo = FRONTERA.get(t, ("revisar", "sin clasificar todavía"))

        dup = 0
        if p and p["ts_col"]:
            dup = p["n_filas"] - p["ts_distintos"]
            if dup:
                avisos.append(f"{t}: {dup:,} filas con `{p['ts_col']}` repetido")

        if p:
            rango = f"{p['ts_min']} → {p['ts_max']}" if p["ts_col"] else "—"
            marca = "—" if not p["ts_col"] else ("no" if dup == 0 else f"**{dup:,}** ⚠")
            lineas.append(f"| [`{t}`](#{t}) | {p['n_filas']:,} | {rango} | {marca} | {veredicto} |")
        else:
            lineas.append(f"| [`{t}`](#{t}) | — | — | — | {veredicto} |")

        detalle += ["", f"<a name='{t}'></a>", f"## `{t}`", "",
                    f"**Frontera:** {veredicto} — {motivo}", ""]
        if p:
            detalle.append(f"**Filas:** {p['n_filas']:,}")
            if p["ts_col"]:
                aviso = "" if dup == 0 else f"  ⚠ **{dup:,} filas con marca temporal repetida**"
                detalle.append(f"· **Clave temporal:** `{p['ts_col']}` "
                               f"({p['ts_distintos']:,} valores distintos){aviso}")
                detalle.append(f"· **Rango:** {p['ts_min']} → {p['ts_max']}")
            detalle.append("")

        detalle += ["| Columna | Tipo | Nullable | % nulos |", "|---|---|---|---|"]
        for _, r in cols.iterrows():
            pct = f"{p['nulos'][r['column_name']]:.2f}" if p else "—"
            detalle.append(f"| `{r['column_name']}` | {r['data_type']} | {r['is_nullable']} | {pct} |")

        print(f"  OK {t:28s} {len(cols):>3} columnas"
              + (f"  {p['n_filas']:>9,} filas" if p else "")
              + ("" if dup == 0 else f"   *** {dup:,} CLAVES REPETIDAS ***"))

    con.close()
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    SALIDA.write_text("\n".join(lineas + detalle), encoding="utf-8")

    print(f"\nEscrito: {SALIDA}")
    if avisos:
        print("\nTABLAS CON LA CLAVE TEMPORAL REPETIDA -- candidatas a provocar el merge x24:")
        for a in avisos:
            print("  -", a)
        print("\nUna tabla declarada como grain='daily' con más de una fila por fecha convierte "
              "el merge en muchos-a-muchos y multiplica el calendario entero.")
    else:
        print("\nNinguna tabla tiene la clave temporal repetida.")


if __name__ == "__main__":
    main()