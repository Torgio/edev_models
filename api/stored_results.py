"""Read stored evaluations and battery results. Never reconstruct missing metrics."""
import math


def clean(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [clean(item) for item in value]
    return value


def records(cursor):
    names = [column[0] for column in cursor.description]
    return [clean(dict(zip(names, row))) for row in cursor.fetchall()]


def evaluations(connection):
    with connection() as con, con.cursor() as cur:
        cur.execute("""
            SELECT e.model, e.seed, e.periodo, e.corte, e.n_obs, e.mae,
                   e.rmse, e.smape, e.cobertura_ic80, e.captura_pct,
                   e.eur_dia, e.pico_1h_pct, e.skill_vs_naive, e.simulador,
                   e.calculado_en, m.estado
            FROM model_metrics e
            LEFT JOIN models m ON m.model = e.model AND m.seed = e.seed
            ORDER BY e.periodo, e.corte, e.model, e.seed
        """)
        return {"origin": "model_metrics", "models": records(cur)}


def battery(connection, day):
    with connection() as con, con.cursor() as cur:
        cur.execute("""
            SELECT datetime, model, carga_mw, descarga_mw, soc_mwh,
                   ingreso_eur, simulador, updated_at
            FROM bess_plan
            WHERE (datetime AT TIME ZONE 'Europe/Madrid')::date = %s
            ORDER BY model, datetime
        """, (day,))
        plan = records(cur)
        cur.execute("""
            SELECT fecha_objetivo, model, ingreso_eur, ingreso_oraculo_eur,
                   ingreso_naive_eur, captura_pct, ciclos, simulador, calculado_en
            FROM bess_result WHERE fecha_objetivo = %s ORDER BY model
        """, (day,))
        return {"date": day, "origin": ["bess_plan", "bess_result"],
                "plan": plan, "results": records(cur)}
