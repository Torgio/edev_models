"""Read stored evaluations and battery results. Never reconstruct missing metrics."""
import math
from datetime import timedelta


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


def weighted_skill(rows):
    """Aggregate stored daily MAEs; daily skill percentages must never be averaged."""
    comparable = [row for row in rows if row.get("n_obs") and row.get("mae") is not None
                  and row.get("mae_naive") is not None]
    denominator = sum(row["mae_naive"] * row["n_obs"] for row in comparable)
    if not comparable or denominator == 0:
        return None
    numerator = sum(row["mae"] * row["n_obs"] for row in comparable)
    return 100 * (1 - numerator / denominator)


def performance_summary(rows, window_days, start, end):
    recent_start = end - timedelta(days=min(10, window_days) - 1)
    split = start + timedelta(days=window_days // 2)
    recent = [row for row in rows if row["date"] >= recent_start]
    first = [row for row in rows if row["date"] < split]
    second = [row for row in rows if row["date"] >= split]
    return {
        "start_date": start,
        "end_date": end,
        "window_days": window_days,
        "evaluated_days": len(rows),
        "observations": sum(row.get("n_obs") or 0 for row in rows),
        "days_won": sum(row.get("mae") is not None and row.get("mae_naive") is not None
                        and row["mae"] < row["mae_naive"] for row in rows),
        "skill_pct": weighted_skill(rows),
        "recent_days": min(10, window_days),
        "recent_evaluated_days": len(recent),
        "recent_skill_pct": weighted_skill(recent),
        "first_half_skill_pct": weighted_skill(first),
        "second_half_skill_pct": weighted_skill(second),
    }


def performance_history(connection, model, seed, days, source="production"):
    with connection() as con, con.cursor() as cur:
        cur.execute("""
            SELECT model, seed, count(*) AS days, min(fecha) AS start_date,
                   max(fecha) AS end_date
            FROM model_metrics_daily
            WHERE source = %s AND mae IS NOT NULL AND mae_naive IS NOT NULL
            GROUP BY model, seed
            ORDER BY count(*) DESC, model, seed
        """, (source,))
        available = records(cur)
        if not any(row["model"] == model and row["seed"] == seed for row in available):
            return None

        cur.execute("""
            SELECT max(fecha) FROM model_metrics_daily
            WHERE source = %s AND model = %s AND seed = %s
              AND mae IS NOT NULL AND mae_naive IS NOT NULL
        """, (source, model, seed))
        end = cur.fetchone()[0]
        start = end - timedelta(days=days - 1)
        cur.execute("""
            SELECT d.fecha AS date, d.n_obs, d.mae, d.mae_naive, d.skill_vs_naive,
                   CASE WHEN v.dias_en_ventana >= 7 THEN v.skill_7d END AS skill_7d,
                   d.estado, v.dias_en_ventana, d.naive_regla
            FROM model_metrics_daily d
            LEFT JOIN model_metrics_daily_7d v
              ON v.fecha = d.fecha AND v.model = d.model AND v.seed = d.seed
             AND v.source = d.source
            WHERE d.source = %s AND d.model = %s AND d.seed = %s
              AND d.fecha BETWEEN %s AND %s
              AND d.mae IS NOT NULL AND d.mae_naive IS NOT NULL
            ORDER BY d.fecha
        """, (source, model, seed, start, end))
        series = records(cur)

    naive_rule = next((row.get("naive_regla") for row in series
                       if row.get("naive_regla")), None)
    for row in series:
        row.pop("naive_regla", None)

    return {
        "origin": "model_metrics_daily",
        "model": model,
        "seed": seed,
        "source": source,
        "available": available,
        "summary": performance_summary(series, days, start, end),
        "series": series,
        "naive_rule": naive_rule,
        "definition": "100 × (1 − Σ(MAE modelo × horas) / Σ(MAE naive × horas)).",
    }


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
