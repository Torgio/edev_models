"""
refresh_generation.py — refresco incremental de la tabla generation
===================================================================

generation es la capa limpia de generacion real por tecnologia: 16 columnas
seleccionadas de esios_gen y entsoe_gen_data, renombradas con prefijo de
fuente (ree_ = ESIOS, ent_ = ENTSO-E, c_ = calculada). Los motivos columna a
columna estan en matriz_generacion_FINAL_20260819_v2.xlsx.

POR QUE SE LLAMA DESDE LOS DOS PIPELINES
generation necesita las dos tablas base. esios_gen corre a las 19:30 y
entsoe_daily a las 20:00, pero el orden no debe importar: el INSERT es
idempotente, asi que la primera llamada inserta lo que puede (si ENTSO-E aun no
tiene el dia, el JOIN no encuentra esas horas y no inserta nada, sin error) y
la segunda completa.

VENTANA DE 7 DIAS
ENTSO-E republica: verificado el 15/08/2026, la prevision de demanda del dia 16
cambio entre dos consultas separadas por horas. Con ON CONFLICT DO UPDATE y
siete dias de ventana, las correcciones se propagan en vez de congelarse.

DOS COLUMNAS CALCULADAS
  c_gsolar = GREATEST(0, solar_mw [B16] - ree_gsolter_mw [1294])
      B16 agrupa FV y termosolar; el 1294 es termosolar sola. Se deriva porque
      el 1295 incorpora autoconsumo desde dic-2025. El CASE ... IS NULL es
      imprescindible: GREATEST ignora los NULL y los volveria ceros falsos.

  c_ghydrodispatch = hydro_reservoir_mw [B12] + COALESCE(pumping_gen_mw [B10], 0)
      ENTSO-E no separaba B10 de B12 antes de dic-2022: la turbinacion iba
      dentro del embalse y pumping_gen_mw es NULL en 16.787 horas. Fusionarlas
      da una serie homogenea en 2021-2026.

USO
    python refresh_generation.py        # refresco manual
    from refresh_generation import refrescar_generation
"""

from __future__ import annotations

import psycopg2

from config import load_config

TABLA = "generation"
VENTANA_DIAS = 7

SQL = f"""
INSERT INTO {TABLA}
SELECT ent.datetime,
       CASE WHEN ent.solar_mw IS NULL OR esi.ree_gsolter_mw IS NULL THEN NULL
            ELSE GREATEST(0, ent.solar_mw - esi.ree_gsolter_mw)
       END,
       esi.ree_gsolter_mw,
       ent.wind_mw,
       ent.hydro_run_river_mw,
       ent.hydro_reservoir_mw + COALESCE(ent.pumping_gen_mw, 0),
       ent.pumping_cons_mw,
       esi.ree_gbattery_mw,
       esi.ree_cbattery_mw,
       ent.biomass_mw,
       ent.waste_mw,
       ent.other_renewable_mw,
       esi.ree_gnuclear_mw,
       esi.ree_gccgas_mw,
       esi.ree_gotherthermal_mw,
       esi.ree_gcoal_mw,
       ent.oil_mw
FROM entsoe_gen_data ent
JOIN esios_gen       esi USING (datetime)
WHERE ent.datetime > (SELECT max(datetime) - interval '{VENTANA_DIAS} days'
                      FROM {TABLA})
ON CONFLICT (datetime) DO UPDATE SET
  c_gsolar          = EXCLUDED.c_gsolar,
  ree_gsolter       = EXCLUDED.ree_gsolter,
  ent_gwind         = EXCLUDED.ent_gwind,
  ent_ghydroriver   = EXCLUDED.ent_ghydroriver,
  c_ghydrodispatch  = EXCLUDED.c_ghydrodispatch,
  ent_cpumping      = EXCLUDED.ent_cpumping,
  ree_gbattery      = EXCLUDED.ree_gbattery,
  ree_cbattery      = EXCLUDED.ree_cbattery,
  ent_gbiomass      = EXCLUDED.ent_gbiomass,
  ent_gwaste        = EXCLUDED.ent_gwaste,
  ent_gotherrenew   = EXCLUDED.ent_gotherrenew,
  ree_gnuclear      = EXCLUDED.ree_gnuclear,
  ree_gccgt         = EXCLUDED.ree_gccgt,
  ree_gotherthermal = EXCLUDED.ree_gotherthermal,
  ree_gcoal         = EXCLUDED.ree_gcoal,
  ent_goil          = EXCLUDED.ent_goil
"""


def refrescar_generation(log=None, conn=None) -> int:
    """
    Propaga a generation lo que haya en las tablas base dentro de la ventana.
    Devuelve las filas afectadas, o -1 si fallo. Nunca lanza: un fallo aqui no
    debe tumbar un pipeline de ingesta que ya ha hecho su trabajo.
    """
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
    refrescar_generation()