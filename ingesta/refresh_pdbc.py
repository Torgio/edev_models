"""
refresh_pdbc.py — refresco incremental de esios_pdbc_gen
========================================================

PDBC = PDBF menos bilaterales fisicos, por tecnologia. ESIOS no publica el
PDBC agregado por tecnologia (verificado 23-ago-2026 sobre los 1.971
indicadores del catalogo), de ahi la derivacion.

CCGT, fuel_gas, waste, other_renew y bombeo pasan sin restar: no tienen
columna bilateral. El ciclo combinado no participa en bilaterales fisicos.
Las columnas de intermediacion del bilateral (generic_*, retail_*,
direct_consumer) NO son generacion y quedan fuera.

Publicado a las 13:30 de D-1, despues del cierre del mercado (12:00): usar
solo con desfase (pdbc_lag1, pdbc_lag7).

Ventana de 7 dias con ON CONFLICT DO UPDATE para que las revisiones se
propaguen en vez de congelarse.
"""

from __future__ import annotations

import psycopg2

from config import load_config

TABLA = "esios_pdbc_gen"
VENTANA_DIAS = 7

COLS = ["wind_mw", "solar_pv_mw", "solar_thermal_mw", "hydro_ugh_mw",
        "hydro_no_ugh_mw", "nuclear_mw", "coal_mw", "cogen_mw", "biomass_mw",
        "biogas_mw", "hybrid_mw", "ccgt_mw", "fuel_gas_mw", "waste_mw",
        "other_renew_mw", "pumping_gen_mw", "pumping_cons_mw"]

SQL = f"""
INSERT INTO {TABLA}
SELECT g.datetime,
       g.wind_mw          - COALESCE(b.bil_wind_onshore_mw, 0),
       g.solar_pv_mw      - COALESCE(b.bil_solar_pv_mw, 0),
       g.solar_thermal_mw - COALESCE(b.bil_solar_thermal_mw, 0),
       g.hydro_ugh_mw     - COALESCE(b.bil_hydro_ugh_mw, 0),
       g.hydro_no_ugh_mw  - COALESCE(b.bil_hydro_no_ugh_mw, 0),
       g.nuclear_mw       - COALESCE(b.bil_nuclear_mw, 0),
       g.coal_mw          - COALESCE(b.bil_coal_mw, 0),
       g.cogen_mw         - COALESCE(b.bil_cogen_mw, 0),
       g.biomass_mw       - COALESCE(b.bil_biomass_mw, 0),
       g.biogas_mw        - COALESCE(b.bil_biogas_mw, 0),
       g.hybrid_mw        - COALESCE(b.bil_hybrid_mw, 0),
       g.ccgt_mw, g.fuel_gas_mw, g.waste_mw, g.other_renew_mw,
       g.pumping_gen_mw, g.pumping_cons_mw
FROM esios_pbf_gen g
LEFT JOIN esios_pbf_bilateral b USING (datetime)
WHERE g.datetime > (SELECT max(datetime) - interval '{VENTANA_DIAS} days'
                    FROM {TABLA})
ON CONFLICT (datetime) DO UPDATE SET
""" + ",\n".join(f"  {c} = EXCLUDED.{c}" for c in COLS)


def refrescar_pdbc(log=None, conn=None) -> int:
    """Propaga a esios_pdbc_gen lo que haya en las tablas base dentro de la
    ventana. Devuelve filas afectadas, o -1 si fallo. Nunca lanza."""
    propia = conn is None
    try:
        if propia:
            _, db_config = load_config()
            conn = psycopg2.connect(**db_config)

        with conn.cursor() as cur:
            cur.execute(SQL)
            n = cur.rowcount
            cur.execute(f"SELECT max(datetime) FROM {TABLA}")
            ultimo = cur.fetchone()[0]
        conn.commit()

        msg = f"{TABLA}: {n} filas actualizadas | ultimo dato {ultimo}"
        log.info(msg) if log else print(msg)
        return n

    except Exception as e:
        msg = f"{TABLA}: ERROR {type(e).__name__} {str(e)[:120]}"
        if conn:
            conn.rollback()
        log.error(msg) if log else print(msg)
        return -1

    finally:
        if propia and conn:
            conn.close()


if __name__ == "__main__":
    refrescar_pdbc()
