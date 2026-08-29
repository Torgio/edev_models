"""
union_bronze.py -- genera `data/bronze/bronze_unificado.parquet`.

Es el paso que falta entre `extract_bronze.py` (que ya corriste: dejó los ocho `*_raw.parquet`)
y los notebooks de EDA, que leen el unificado y hoy fallan con FileNotFoundError.

Hace lo mismo que `notebooks/01_union_bronze.ipynb` pero como script, para poder lanzarlo de
una vez desde la terminal, y con los dos guardarraíles de integridad que faltaban.

No se conecta a Postgres: sólo lee los parquets ya extraídos.

Uso
---
    python eda/union_bronze.py
    python eda/union_bronze.py --inicio 2020-01-01 --fin 2026-08-28

Qué comprueba
-------------
1. ANTES del merge: que cada tabla tenga una sola fila por clave de join. Si una tabla horaria
   está declarada como `daily`, el merge por `date_local` sería muchos-a-muchos y multiplicaría
   el calendario. Es lo que produjo el parquet de 1.393.183 filas donde debía haber 58.056.
2. DESPUÉS del merge: que el resultado tenga exactamente las filas del calendario.

Nota sobre `esios_capacity_available`: verificado el 28-ago que SÍ varía dentro del día (1.882
de 2.432 días tienen más de un valor de `ccgt_mw`), así que es horaria de verdad y debe estar
declarada `grain="hourly"` en `bronze_config.py`. Si sigue como `daily`, este script para en el
primer guardarraíl y dice exactamente por qué.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "scripts"))

from bronze_config import (          # noqa: E402
    ANCHOR_TABLE, BRONZE_DIR, DERIVED_COLUMNS, TABLES, TZ_LOCAL,
    UNIFIED_FILENAME, raw_path,
)


# ---------------------------------------------------------------------------
# Carga
# ---------------------------------------------------------------------------

def cargar_parquets() -> dict[str, pd.DataFrame]:
    normalized = {}
    for nombre in TABLES:
        path = raw_path(nombre)
        if not path.exists():
            print(f"  --  {nombre:28s} sin parquet ({path.name}). Se omite de la unión.")
            continue
        normalized[nombre] = pd.read_parquet(path)
        print(f"  OK  {nombre:28s} {normalized[nombre].shape[0]:>8,} x "
              f"{normalized[nombre].shape[1]:>2}")
    if ANCHOR_TABLE not in normalized:
        sys.exit(f"\nFalta el parquet de la tabla ancla ({ANCHOR_TABLE}): sin ella no se puede "
                 f"construir el calendario. Correr: python scripts/extract_bronze.py")
    return normalized


# ---------------------------------------------------------------------------
# Calendario
# ---------------------------------------------------------------------------

def construir_calendario(inicio, fin, tz_local=TZ_LOCAL) -> pd.DataFrame:
    ts_utc = pd.date_range(inicio, fin, freq="h", tz="UTC")
    cal = pd.DataFrame({"ts_utc": ts_utc})
    cal["ts_local"] = cal["ts_utc"].dt.tz_convert(tz_local)
    cal["date_utc"] = cal["ts_utc"].dt.date
    cal["date_local"] = cal["ts_local"].dt.date
    cal["hour_utc"] = cal["ts_utc"].dt.hour
    cal["hour_local"] = cal["ts_local"].dt.hour
    return cal


# ---------------------------------------------------------------------------
# Guardarraíles
# ---------------------------------------------------------------------------

def verificar_claves(normalized, TABLES) -> None:
    """
    Cada tabla debe tener UNA fila por clave de join. Lanza excepción en vez de avisar: un
    aviso por consola se pierde entre las salidas, una excepción para la ejecución donde
    está el problema. Es lo que faltó para que la duplicación x24 no sobreviviera un EDA
    entero sin que nadie la viera.
    """
    problemas = []
    print("\nComprobando claves de join:")
    for nombre, df in normalized.items():
        grain = TABLES[nombre].get("grain", "hourly")
        clave = "date_local" if grain == "daily" else "ts_utc"

        if clave not in df.columns:
            problemas.append(f"{nombre}: no tiene la clave '{clave}' que exige grain='{grain}'")
            print(f"  !!  {nombre:28s} sin la columna '{clave}'")
            continue

        dup = int(df[clave].duplicated().sum())
        unicas = df[clave].nunique()
        estado = "ok" if dup == 0 else f"{dup:,} DUPLICADAS"
        print(f"  {'ok' if dup == 0 else '!!'}  {nombre:28s} grain={grain:6s} "
              f"filas={len(df):>8,} claves={unicas:>8,}  {estado}")

        if dup:
            factor = len(df) / max(unicas, 1)
            problemas.append(
                f"{nombre}: {dup:,} claves duplicadas en '{clave}' ({factor:.1f} filas por "
                f"clave). O el grain está mal declarado, o la extracción trae filas de más. "
                f"Si la tabla es horaria, poner grain='hourly' en bronze_config.py."
            )

    if problemas:
        raise ValueError("\nEl merge multiplicaría filas:\n  - " + "\n  - ".join(problemas))
    print("  Todas las claves son únicas: el merge no puede multiplicar filas.")


def verificar_resultado(bronze_unificado, calendario) -> None:
    n, esperadas = len(bronze_unificado), len(calendario)
    if n != esperadas:
        raise ValueError(
            f"El merge multiplicó filas: {n:,} frente a {esperadas:,} del calendario "
            f"(factor {n / esperadas:.2f}). Revisar qué tabla tiene la clave duplicada."
        )
    print(f"\nIntegridad OK: {n:,} filas = {esperadas:,} horas del calendario.")


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

def unir_exacto(base: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    """`hourly` y `3h` se unen igual: join exacto por ts_utc. Para 3h, las horas sin lectura
    quedan en NaN a propósito -- no se sostiene el último valor conocido."""
    return base.merge(df, on="ts_utc", how="left")


def unir_daily(base: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    """El dato es diario: se coloca SÓLO en la hora 00 local, sin difundir a las otras 23.
    El bronce no imputa, cualquiera que sea la granularidad de origen."""
    merged = base.merge(df, on="date_local", how="left")
    cols_nuevas = [c for c in df.columns if c != "date_local"]
    merged.loc[merged["hour_local"] != 0, cols_nuevas] = np.nan
    return merged


UNIR_POR_GRAIN = {"hourly": unir_exacto, "3h": unir_exacto, "daily": unir_daily}


def main() -> None:
    ap = argparse.ArgumentParser(description="Une los parquets del bronce sobre un calendario horario.")
    ap.add_argument("--inicio", help="YYYY-MM-DD. Por defecto, el mínimo de la tabla ancla.")
    ap.add_argument("--fin", help="YYYY-MM-DD. Por defecto, el máximo de la tabla ancla.")
    args = ap.parse_args()

    print(f"Leyendo parquets de {BRONZE_DIR}")
    normalized = cargar_parquets()

    ancla = normalized[ANCHOR_TABLE]
    inicio = pd.Timestamp(args.inicio, tz="UTC") if args.inicio else ancla["ts_utc"].min()
    fin = (pd.Timestamp(args.fin, tz="UTC") + pd.Timedelta(hours=23)) if args.fin else ancla["ts_utc"].max()

    calendario = construir_calendario(inicio, fin)
    print(f"\nCalendario: {inicio} -> {fin}  ({len(calendario):,} horas), "
          f"anclado en '{ANCHOR_TABLE}'")

    verificar_claves(normalized, TABLES)

    bronze_unificado = calendario.copy()
    for nombre, df in normalized.items():
        grain = TABLES[nombre].get("grain", "hourly")
        bronze_unificado = UNIR_POR_GRAIN[grain](bronze_unificado, df)

    verificar_resultado(bronze_unificado, calendario)

    print("\nColumnas derivadas:")
    for derivada in DERIVED_COLUMNS:
        faltantes = [c for c in derivada["inputs"] if c not in bronze_unificado.columns]
        if faltantes:
            print(f"  --  {derivada['name']}: faltan {faltantes}. Se omite.")
            continue
        bronze_unificado[derivada["name"]] = derivada["formula"](bronze_unificado)
        print(f"  OK  {derivada['name']}")

    salida = BRONZE_DIR / UNIFIED_FILENAME
    bronze_unificado.to_parquet(salida, index=False)

    print(f"\nGuardado: {salida}")
    print(f"  {bronze_unificado.shape[0]:,} filas x {bronze_unificado.shape[1]} columnas")
    print(f"  Rango: {bronze_unificado['ts_utc'].min()} -> {bronze_unificado['ts_utc'].max()}")

    nulos = bronze_unificado.isna().mean().mul(100).round(1).sort_values(ascending=False)
    print("\nColumnas con más nulos (recordatorio: en las tablas daily y 3h son "
          "estructurales, no huecos):")
    print(nulos.head(8).to_string())


if __name__ == "__main__":
    main()
