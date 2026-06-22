"""
TFM Energia UCM — ESIOS Data Check
Verifica que datos hay en la BD y que falta por cargar.

Usage:
    python esios_check.py
"""

import psycopg2
import pandas as pd
from datetime import date

# ── Configuration ──────────────────────────────────────────────────────────────

DB_CONFIG = {
    "host":     "91.134.143.153",
    "port":     5432,
    "dbname":   "tfm_energia",
    "user":     "postgres",
    "password": "TFMenergia2026#",
}

EXPECTED_START = date(2020, 1, 1)
EXPECTED_END   = date(2025, 12, 31)

# Columnas clave a verificar
KEY_COLS = [
    "price_eur_mwh",
    "demanda_real_mw",
    "gen_solar_mw",
    "gen_wind_mw",
    "gen_nuclear_real_mw",
    "saldo_francia_mw",
    "pct_gen_libre_co2",
]

# ── Checks ─────────────────────────────────────────────────────────────────────

def run():
    print("\n" + "="*60)
    print("  ESIOS DATA CHECK — tfm_energia.marketdata_qh")
    print("="*60)

    conn = psycopg2.connect(**DB_CONFIG)
    cur  = conn.cursor()

    # 1. Total de filas
    cur.execute("SELECT COUNT(*) FROM marketdata_qh")
    total = cur.fetchone()[0]
    print(f"\n Total rows in table: {total:,}")

    if total == 0:
        print("\n  Table is empty — run esios_load.py to start loading data.")
        conn.close()
        return

    # 2. Rango de fechas disponible
    cur.execute("SELECT MIN(time_qh), MAX(time_qh) FROM marketdata_qh")
    min_t, max_t = cur.fetchone()
    print(f"\n Date range in DB:")
    print(f"   First record : {min_t}")
    print(f"   Last record  : {max_t}")
    print(f"   Expected     : {EXPECTED_START} → {EXPECTED_END}")

    # 3. Cobertura por año
    print(f"\n Rows per year:")
    cur.execute("""
        SELECT EXTRACT(YEAR FROM time_qh)::int AS year,
               COUNT(*) AS rows,
               COUNT(*) / 8760.0 * 100 AS pct_coverage
        FROM marketdata_qh
        GROUP BY year
        ORDER BY year
    """)
    rows = cur.fetchall()
    for year, count, pct in rows:
        bar = "█" * int(pct / 5)
        print(f"   {year}: {count:6,} rows  {pct:5.1f}%  {bar}")

    # 4. Nulos por columna clave
    print(f"\n Null check on key columns:")
    for col in KEY_COLS:
        try:
            cur.execute(f"SELECT COUNT(*) FROM marketdata_qh WHERE {col} IS NULL")
            nulls = cur.fetchone()[0]
            pct = nulls / total * 100 if total > 0 else 0
            status = "OK" if pct < 5 else "WARN" if pct < 20 else "FAIL"
            print(f"   [{status}] {col:30s} {nulls:6,} nulls ({pct:.1f}%)")
        except Exception as e:
            print(f"   [ERR] {col}: {e}")

    # 5. Huecos detectados (dias sin datos)
    print(f"\n Gap detection (days with no data):")
    cur.execute("""
        SELECT generate_series(
            MIN(time_qh)::date,
            MAX(time_qh)::date,
            '1 day'::interval
        )::date AS day
        FROM marketdata_qh
    """)
    all_days = set(r[0] for r in cur.fetchall())

    cur.execute("""
        SELECT DISTINCT time_qh::date AS day
        FROM marketdata_qh
        ORDER BY day
    """)
    days_with_data = set(r[0] for r in cur.fetchall())
    gaps = sorted(all_days - days_with_data)

    if not gaps:
        print("   No gaps detected")
    else:
        print(f"   {len(gaps)} days missing:")
        for g in gaps[:20]:
            print(f"   {g}")
        if len(gaps) > 20:
            print(f"   ... and {len(gaps)-20} more")

    # 6. Resumen de que falta cargar
    print(f"\n What to load next:")
    if not rows:
        print(f"   Everything from {EXPECTED_START} to {EXPECTED_END}")
    else:
        loaded_years = [r[0] for r in rows]
        all_years    = list(range(2020, 2026))
        missing      = [y for y in all_years if y not in loaded_years]
        partial      = [r[0] for r in rows if r[2] < 95]

        if missing:
            print(f"   Years not loaded     : {missing}")
        if partial:
            print(f"   Years incomplete (<95%): {partial}")
        if not missing and not partial:
            print(f"   All years 2020-2025 loaded and complete!")

    print("\n" + "="*60 + "\n")
    conn.close()

if __name__ == "__main__":
    run()
