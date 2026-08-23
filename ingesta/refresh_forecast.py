"""refresh_forecast.py — refresco incremental de la tabla forecast.

Capa limpia sobre esios_forecast_da: renombra a la convencion del proyecto
y deriva c_autoconsumo_prev = demanda_prev_mw - demanda_mercado_prev_mw.
Todos los indicadores de origen se publican antes de las 11:00 de D-1
(CNMC Circular 4/2019): entran al modelo sin fuga.
autoconsumo_estimado marca desde dic-2025; antes c_autoconsumo_prev es
ruido en torno a cero y no es interpretable como autoconsumo.
Ventana de 7 dias con ON CONFLICT DO UPDATE.
"""
from __future__ import annotations
import psycopg2
from config import load_config

TABLA = "forecast"
COLS = ["ree_demanda_prev", "c_autoconsumo_prev", "ree_gwind_prev",
        "ree_gsolar_prev", "ree_grenov_prev", "ree_ntc_impfr_prev",
        "ree_ntc_expfr_prev", "ree_ntc_imppt_prev", "ree_ntc_exppt_prev",
        "ree_ntc_impma_prev", "ree_ntc_expma_prev", "autoconsumo_estimado"]

SQL = """
INSERT INTO forecast
SELECT datetime, demanda_mercado_prev_mw,
       demanda_prev_mw - demanda_mercado_prev_mw,
       gen_wind_prev_mw, gen_solar_pv_prev_mw, gen_renovables_prev_mw,
       ntc_fr_imp_prev_mw, ntc_fr_exp_prev_mw,
       ntc_pt_imp_prev_mw, ntc_pt_exp_prev_mw,
       ntc_ma_imp_prev_mw, ntc_ma_exp_prev_mw,
       datetime >= '2025-12-01 00:00:00+01'::timestamptz
FROM esios_forecast_da
WHERE datetime > (SELECT max(datetime) - interval '7 days' FROM forecast)
ON CONFLICT (datetime) DO UPDATE SET
""" + ",\n".join(f"  {c} = EXCLUDED.{c}" for c in COLS)


def refrescar_forecast(log=None, conn=None) -> int:
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
    refrescar_forecast()
