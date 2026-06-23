"""
TFM Energia UCM — ESIOS Data Check
Verifica que datos hay en la BD y que falta por cargar.

Usage:
    python esios_check.py
"""

import psycopg2
from datetime import date
from config import load_config

EXPECTED_START = date(2020, 1, 1)
EXPECTED_END   = date(2025, 12, 31)

KEY_COLS = [
    "price_eur_mwh", "demanda_real_mw", "gen_solar_mw",
    "gen_wind_mw", "gen_nuclear_real_mw", "saldo_francia_mw",
    "pct_gen_libre_co2", "ntc_francia_imp_mw", "co2_real_t",
]

def run():
    _, db_config = load_config()

    print("\n" + "="*60)
    print("  ESIOS DATA CHECK — tfm_energia.marketdata_qh")
    print("="*60)

    conn = psycopg2.connect(**db_config)
    cur  = conn.cursor()

    # 1. Total filas
    cur.execute("SELECT COUNT(*) FROM marketdata_qh")
    total = cur.fetchone()[0]
    print(f"\n  Total rows      : {total:,}")

    if total == 0:
        print("\n  Table is empty — run esios_load.py to start loading data.")
        conn.close()
        return

    # 2. Rango de fechas
    cur.execute("SELECT MIN(time_qh), MAX(time_qh) FROM marketdata_qh")
    min_t, max_t = cur.fetchone()
    print(f"  First record    : {min_t}")
    print(f"  Last record     : {max_t}")
    print(f"  Expected range  : {EXPECTED_START} → {EXPECTED_END}")

    # 3. Cobertura por año
    print(f"\n  Rows per year:")
    cur.execute("""
        SELECT EXTRACT(YEAR FROM time_qh)::int AS year,
               COUNT(*) AS rows,
               COUNT(*) / 8760.0 * 100 AS pct
        FROM marketdata_qh
        GROUP BY year ORDER BY year
    """)
    rows = cur.fetchall()
    for year, count, pct in rows:
        bar = "█" * int(pct / 5)
        print(f"    {year}: {count:6,} rows  {pct:5.1f}%  {bar}")

    # 4. Nulos por columna clave
    print(f"\n  Null check on key columns:")
    for col in KEY_COLS:
        try:
            cur.execute(f"SELECT COUNT(*) FROM marketdata_qh WHERE {col} IS NULL")
            nulls = cur.fetchone()[0]
            pct   = nulls / total * 100 if total > 0 else 0
            status = "OK  " if pct < 5 else "WARN" if pct < 20 else "FAIL"
            print(f"    [{status}] {col:<30} {nulls:6,} nulls ({pct:.1f}%)")
        except Exception as e:
            print(f"    [ERR ] {col}: {e}")

    # 5. Huecos
    print(f"\n  Gap detection:")
    cur.execute("""
        SELECT DISTINCT time_qh::date AS day
        FROM marketdata_qh ORDER BY day
    """)
    days_with_data = {r[0] for r in cur.fetchall()}
    if days_with_data:
        all_days = set()
        d = min(days_with_data)
        while d <= max(days_with_data):
            all_days.add(d)
            d = d + __import__('datetime').timedelta(days=1)
        gaps = sorted(all_days - days_with_data)
        if not gaps:
            print("    No gaps detected ✅")
        else:
            print(f"    {len(gaps)} days missing:")
            for g in gaps[:10]:
                print(f"      {g}")
            if len(gaps) > 10:
                print(f"      ... and {len(gaps)-10} more")

    # 6. Resumen
    print(f"\n  What to load next:")
    loaded_years = [r[0] for r in rows]
    missing = [y for y in range(2020, 2026) if y not in loaded_years]
    partial = [r[0] for r in rows if r[2] < 95]
    if missing:
        print(f"    Years not loaded     : {missing}")
    if partial:
        print(f"    Years incomplete (<95%): {partial}")
    if not missing and not partial:
        print(f"    All years 2020-2025 complete ✅")

    print("\n" + "="*60 + "\n")
    conn.close()

if __name__ == "__main__":
    run()
