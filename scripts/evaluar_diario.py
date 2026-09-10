"""Evaluacion diaria con corte operativo D-1 12:00 (Europe/Madrid).

bess_result liquida exclusivamente planes v2 completos guardados antes del corte.
model_metrics simula modelo y naive sobre una interseccion exacta de instantes.
model_metrics_daily conserva MAEs pareados por dia, modelo y semilla.
Se mantienen instantes con zona, incluidos los dias de 23 y 25 horas.

`updated_at` es una comprobacion conservadora: una prediccion sobrescrita despues del
corte se excluye, aunque hubiera existido una version anterior. No sustituye un archivo
inmutable de emisiones ni demuestra la disponibilidad temporal de todas las features.

    python scripts/evaluar_diario.py --simulacro
    python scripts/evaluar_diario.py --dia 2026-09-02 --simulacro
    python scripts/evaluar_diario.py
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
for p in ("scripts", "modelos", "ingesta"):
    sys.path.insert(0, str(REPO / p))

TZ = "Europe/Madrid"
VENTANA = 30                       # dias de la ventana movil de produccion

from evaluar_modelos import metricas, comparar, metadata_evaluacion  # noqa: E402
from bess_evaluation import SIMULADOR, optimizar, valorar, ciclos, validar_plan
from tiempo_mercado import indice_local, serie_local, naive_local, dia_completo, periodos_dia


def _f(x):
    """np.float64 -> float. psycopg2 no adapta los escalares de NumPy: los interpola con
    su repr, que en NumPy 2 es "np.float64(0.0)", y Postgres busca un esquema `np`. Las
    metricas salen de medias de pandas, asi que TODAS pasan por aqui antes del INSERT."""
    return None if x is None or pd.isna(x) else float(x)


def _log(paso, texto):
    print(f"[{datetime.now():%H:%M:%S}] {paso}  {texto}", flush=True)


# ------------------------------------------------------------------------- lectura
def cargar(con, desde: date, hasta: date) -> pd.DataFrame:
    """Predicciones de produccion junto al precio que finalmente salio.

    El JOIN va por `datetime`, que es timestamptz, asi que el cambio de hora se resuelve
    solo: la 02:00+02:00 de octubre no se confunde con la 02:00+01:00. Un par (fecha, hora)
    no podria distinguirlas y ese dia mediria mal sin avisar.
    """
    return pd.read_sql("""
        SELECT p.model,
               COALESCE(p.seed, -1)                          AS seed,
               p.datetime                                      AS ts,
               p.prediction::double precision                AS pred,
               s.es_esios::double precision                  AS real
          FROM predictions p
          JOIN spot_price s ON s.datetime = p.datetime
         WHERE p.source = 'production'
           AND s.es_esios IS NOT NULL
           AND p.updated_at < (((p.datetime AT TIME ZONE 'Europe/Madrid')::date - 1
                                 + TIME '12:00') AT TIME ZONE 'Europe/Madrid')
           AND (p.datetime AT TIME ZONE 'Europe/Madrid')::date BETWEEN %s AND %s
         ORDER BY p.model, p.datetime""", con, params=(desde, hasta))


def curva_real(con, desde: date, hasta: date) -> pd.Series:
    """Precio real con instantes unicos; no eliminar la segunda hora de octubre."""
    d = pd.read_sql("""
        SELECT datetime AS ts, es_esios::double precision AS real
          FROM spot_price
         WHERE es_esios IS NOT NULL
           AND (datetime AT TIME ZONE 'Europe/Madrid')::date BETWEEN %s AND %s
         ORDER BY datetime""", con, params=(desde, hasta))
    return serie_local(d.set_index(pd.to_datetime(d.ts, utc=True))["real"])


# ------------------------------------------------------------------ la liquidacion
def liquidar(pred: np.ndarray, real: np.ndarray) -> float:
    """Simulacion fuera de muestra: decidir con pred y valorar con real."""
    return float(valorar(optimizar(pred), real).sum())


def dia_de(g: pd.DataFrame, real_ayer: np.ndarray | None, plan=None) -> dict | None:
    """Liquidar un plan fijo (o simular si se invoca sin plan), con horizonte completo."""
    g = g.copy()
    g["ts"] = indice_local(g.ts)
    g = g.sort_values("ts")
    if not dia_completo(g.ts) or not np.isfinite(g.real.to_numpy()).all():
        return None
    y = g.real.to_numpy()
    dispatch = optimizar(g.pred.to_numpy()) if plan is None else plan
    ingreso = float(valorar(dispatch, y).sum())
    oraculo = liquidar(y, y)
    naive = (liquidar(real_ayer, y) if real_ayer is not None
             and len(real_ayer) == len(y) and np.isfinite(real_ayer).all() else None)
    return {"ingreso_eur": ingreso, "ingreso_oraculo_eur": oraculo,
            "ingreso_naive_eur": naive,
            "captura_pct": 100 * ingreso / oraculo if oraculo > 1e-8 else None,
            "ciclos": ciclos(dispatch)}


def cargar_planes(con, desde, hasta):
    return pd.read_sql("""
        SELECT datetime AS ts, model, carga_mw, descarga_mw, soc_mwh, simulador
          FROM bess_plan
         WHERE (datetime AT TIME ZONE 'Europe/Madrid')::date BETWEEN %s AND %s
           AND updated_at < (((datetime AT TIME ZONE 'Europe/Madrid')::date - 1
                               + TIME '12:00') AT TIME ZONE 'Europe/Madrid')
         ORDER BY model, datetime""", con, params=(desde, hasta))


def evaluar_dias(con, datos: pd.DataFrame, real: pd.Series, escribir: bool,
                 desde=None, hasta=None) -> int:
    filas = []
    if desde is None or hasta is None:
        if datos.empty:
            return 0
        dates = indice_local(datos.ts).date
        desde, hasta = min(dates), max(dates)
    planes = cargar_planes(con, desde, hasta)
    if planes.empty:
        _log("bess_result", "sin planes previos al corte; no se reconstruyen operaciones")
        return 0
    planes = planes.assign(ts=indice_local(planes.ts))
    planes = planes.assign(dia=planes.ts.dt.date)
    real = serie_local(real)
    for (modelo, dia), saved in planes.groupby(["model", "dia"]):
        saved = saved.sort_values("ts")
        if (not dia_completo(saved.ts)
                or not saved.simulador.map(lambda x: x == SIMULADOR).all()):
            _log("bess_result", f"{modelo} {dia}: plan legado o incompatible; no se liquida")
            continue
        # La liquidacion depende del plan y el precio real, no de predictions actual.
        g = pd.DataFrame({"ts": saved.ts.to_numpy(),
                          "real": real.reindex(pd.DatetimeIndex(saved.ts)).to_numpy()})
        dispatch = {key: saved[col].to_numpy(dtype=float) for key, col in
                    (("carga", "carga_mw"), ("descarga", "descarga_mw"), ("soc", "soc_mwh"))}
        try:
            validar_plan(dispatch)
        except ValueError as exc:
            _log("bess_result", f"{modelo} {dia}: {exc}; no se liquida")
            continue
        ayer = naive_local(real, g.ts).to_numpy()
        r = dia_de(g, ayer, dispatch)
        if r is None:
            continue
        filas.append((dia, modelo, r["ingreso_eur"], r["ingreso_oraculo_eur"],
                      r["ingreso_naive_eur"], r["captura_pct"], r["ciclos"],
                      json.dumps(SIMULADOR)))
    if not filas:
        _log("bess_result", "ningun dia completo que liquidar")
        return 0
    if escribir:
        with con.cursor() as cur:
            cur.executemany("""
                INSERT INTO bess_result (fecha_objetivo, model, ingreso_eur,
                    ingreso_oraculo_eur, ingreso_naive_eur, captura_pct, ciclos, simulador)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (fecha_objetivo, model) DO UPDATE SET
                    ingreso_eur=EXCLUDED.ingreso_eur,
                    ingreso_oraculo_eur=EXCLUDED.ingreso_oraculo_eur,
                    ingreso_naive_eur=EXCLUDED.ingreso_naive_eur,
                    captura_pct=EXCLUDED.captura_pct, ciclos=EXCLUDED.ciclos,
                    simulador=EXCLUDED.simulador, calculado_en=now()""", filas)
        con.commit()
    _log("bess_result", f"{len(filas)} filas (dia x modelo)"
                        f"{'' if escribir else '  [simulacro, no escritas]'}")
    return len(filas)


# --------------------------------------------------------------- la ventana movil
def ventana(con, datos: pd.DataFrame, real: pd.Series, escribir: bool):
    """`periodo='prod_30d'`: como va cada modelo en los ultimos 30 dias reales.

    El skill se mide contra la persistencia calculada sobre ESTAS MISMAS horas, no contra
    el naive de validacion. Un agosto plano y un enero de crisis dan MAE incomparables; el
    cociente contra la persistencia del mismo tramo es lo unico que sobrevive al cambio de
    regimen, y es la razon de que la tabla guarde `skill_vs_naive` y no solo el MAE.
    """
    # `prod_30d` son 30 dias POR DEFINICION. Una pasada puede cargar mas -- el backfill de
    # la serie diaria pide `--dias 40` -- y entonces esta fila diria 30 y serian 40. Se
    # recorta aqui, no en main, para que la etiqueta y el contenido no puedan divergir.
    if datos.empty:
        return 0
    datos = datos.assign(ts=indice_local(datos.ts))
    fechas = datos.ts.dt.date
    datos = datos[fechas >= fechas.max() - timedelta(days=VENTANA - 1)]

    groups = list(datos.groupby(["model", "seed"]))
    common = None
    for _, g in groups:
        valid = g[np.isfinite(g[["pred", "real"]].to_numpy()).all(axis=1)]
        idx = pd.DatetimeIndex(valid.ts)
        if idx.has_duplicates:
            raise ValueError("Instantes duplicados por modelo/semilla")
        common = idx if common is None else common.intersection(idx)
    if common is None or common.empty:
        _log("ventana", "sin instantes comunes entre modelos; no hay ranking comparable")
        return 0
    baseline = naive_local(real, common)
    common = common[np.isfinite(baseline.to_numpy())]
    datos = datos[datos.ts.isin(common)]
    if datos.empty:
        return 0
    spec = metadata_evaluacion(common)
    filas = []
    for (modelo, seed), g in datos.groupby(["model", "seed"]):
        g = g.set_index(pd.to_datetime(g.ts))
        nv = naive_local(real, g.index)
        m = comparar(g.pred, g.real, nv)
        if m is None:
            continue
        filas.append((modelo, int(seed), "prod_30d", "global", int(m["n_horas"]),
                      _f(m["MAE"]), _f(m["RMSE"]), _f(m["sMAPE"]), None, None,
                      _f(m["captura_%"]), _f(m["eur_dia"]), _f(m["pico_1h_%"]),
                      _f(m.get("skill_%")), json.dumps(spec)))
    # LA PERSISTENCIA, COMO UN MODELO MAS. Sin esta fila la tabla no contesta la unica
    # pregunta que importa: ¿aporta el modelo, o el dinero lo pone la horquilla del
    # mercado? En un mes de dias parecidos, predecir "manana como hoy" puede capturar casi
    # lo mismo con MAE mucho peor -- y entonces el valor del modelo no esta en el MAE.
    # Se mide sobre las MISMAS horas que el modelo con mas cobertura, no sobre todas.
    ref = max(datos.groupby(["model", "seed"]), key=lambda kv: len(kv[1]))[1]
    ridx = pd.to_datetime(ref.ts)
    nv = naive_local(real, ridx)
    mn = metricas(nv, pd.Series(ref.real.values, index=ridx))
    if mn is not None:
        filas.append(("naive_D1", -1, "prod_30d", "global", int(mn["n_horas"]),
                      _f(mn["MAE"]), _f(mn["RMSE"]), _f(mn["sMAPE"]), None, None,
                      _f(mn["captura_%"]), _f(mn["eur_dia"]), _f(mn["pico_1h_%"]), 0.0,
                      json.dumps(spec)))

    if escribir and filas:
        with con.cursor() as cur:
            cur.executemany("""
                INSERT INTO model_metrics (model, seed, periodo, corte, n_obs, mae, rmse,
                    smape, pinball80, cobertura_ic80, captura_pct, eur_dia, pico_1h_pct,
                    skill_vs_naive, simulador)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (model, seed, periodo, corte) DO UPDATE SET
                    n_obs=EXCLUDED.n_obs, mae=EXCLUDED.mae, rmse=EXCLUDED.rmse,
                    smape=EXCLUDED.smape, captura_pct=EXCLUDED.captura_pct,
                    eur_dia=EXCLUDED.eur_dia, pico_1h_pct=EXCLUDED.pico_1h_pct,
                    skill_vs_naive=EXCLUDED.skill_vs_naive,
                    simulador=EXCLUDED.simulador, calculado_en=now()""", filas)
        con.commit()

    def fmt(value):
        return "--" if value is None or not np.isfinite(value) else f"{value:.2f}"
    print(f"\n  Produccion · {len(common)} horas comunes · simulacion {SIMULADOR['version']}")
    print("    modelo / semilla · MAE · captura % · pico % · skill % · EUR/dia")
    for row in sorted(filas, key=lambda r: r[5]):
        print(f"    {row[0]} / {row[1]} · " + " · ".join(fmt(row[i]) for i in (5, 10, 12, 13, 11)))
    return len(filas)


NAIVE_REGLA = ("precio de la MISMA HORA LOCAL del dia anterior. Los dos domingos del "
               "cambio de hora el desfase real no son 24 h sino 23 o 25, porque el indice "
               "es hora peninsular; esos dias quedan marcados estado='cambio_hora'. "
               "Si ayer tuvo dos horas iguales se usa la primera; si no existio, no se imputa.")


def horas_del_dia(d: date) -> int:
    return len(periodos_dia(d))


# --------------------------------------------------------------- la serie diaria
def serie_diaria(con, datos: pd.DataFrame, real: pd.Series, escribir: bool) -> int:
    """`model_metrics_daily`: una fila por (dia, modelo, semilla). El error, dia a dia.

    POR QUE NO BASTA `prod_30d`. Esa fila es una ventana movil que se recalcula entera en
    cada pasada: contesta "¿como va AHORA?" y borra el ayer. Con solo esa fila no se puede
    dibujar si la ventaja sobre la persistencia se mantiene o se apaga, que es justo lo que
    hay que enseñar de un modelo que lleva semanas sin reentrenar.

    SE GUARDAN LOS DOS MAE, NO SOLO EL SKILL. El skill de una ventana NO es la media de los
    skills diarios. El 30-ago-2026 la persistencia acerto casi sola --naive de 5,56-- y el
    skill de ese dia sale -233 %: promediarlo arrastraria la ventana entera. Lo correcto es
    1 - sum(mae*n)/sum(mae_naive*n), y para eso hacen falta los dos numeradores.

    LOS DIAS RAROS SE MARCAN, NO SE TIRAN. Un dia de 23 o 25 horas tiene un MAE
    perfectamente comparable; lo que no lo es es su baseline en las horas afectadas por el cambio de hora. Guardarlos con `estado` deja que el panel diga cuantos excluye y por que, como
    ya hace el contador de acierto de pico.
    """
    filas = []
    d = datos.assign(ts=indice_local(datos.ts))
    d = d[~d.ts.isna()]
    d = d.assign(dia=d.ts.dt.date)
    for (modelo, seed, dia), g in d.groupby(["model", "seed", "dia"]):
        g = g.set_index(pd.to_datetime(g.ts)).sort_index()
        if g.index.has_duplicates:
            raise ValueError("Instantes duplicados por modelo/semilla/dia")
        err = (g.pred - g.real).abs().replace([np.inf, -np.inf], np.nan)
        errn = (naive_local(real, g.index) - g.real).abs().replace([np.inf, -np.inf], np.nan)
        # LOS DOS MAE, SOBRE LAS MISMAS HORAS. Sin esta mascara, una hora sin naive entra
        # en el numerador y no en el denominador: el cociente compararia dos coberturas
        # distintas y el skill saldria sesgado sin que nada avisara. Hoy coinciden; el dia
        # que `spot_price` tenga un hueco, dejarian de coincidir en silencio.
        valido = err.notna() & errn.notna()
        n = int(valido.sum())              # HORAS COMPARABLES, que es lo que pondera la
                                           # media movil de la vista (SUM(mae*n_obs))
        mae = err[valido].mean() if n else err.mean()
        mae_n = errn[valido].mean() if n else None
        # "no habia naive" y "el naive fue perfecto" no son lo mismo, aunque los dos dejen
        # el skill sin definir. Con un mercado plano dos dias seguidos, mae_naive vale 0 y
        # el dia es perfectamente valido: cuenta, y su cero entra en la suma de la ventana.
        hay_naive = n > 0 and pd.notna(mae_n)
        skill = (100 * (1 - mae / mae_n)
                 if hay_naive and mae_n > 0 and pd.notna(mae) else None)
        esperadas = horas_del_dia(dia)
        if not hay_naive:
            estado = "sin_naive"           # el primer dia de la serie no tiene ayer
        elif esperadas != 24:
            estado = "cambio_hora"         # lo dice el calendario, no el numero de filas
        elif horas_del_dia(dia - timedelta(days=1)) != 24:
            # El naive de hoy sale de AYER, y ayer fue un dia raro. En marzo faltaba la
            # 02:00, asi que hoy esa hora se queda sin baseline; en octubre sobraba, y se
            # uso solo una de las dos. En los dos casos el skill de hoy esta medido sobre
            # una persistencia incompleta: dos dias al año, dicho en vez de escondido.
            estado = "ayer_cambio_hora"
        elif mae_n == 0:
            estado = "naive_perfecto"      # el precio no se movio: no hay skill que medir
        elif n == 24:
            estado = "ok"
        else:
            estado = "horas_incompletas"
        filas.append((dia, modelo, int(seed), "production", n, _f(mae), _f(mae_n),
                      _f(skill), estado, NAIVE_REGLA))

    if not filas:
        _log("serie", "ningun dia que medir")
        return 0
    if escribir:
        with con.cursor() as cur:
            cur.executemany("""
                INSERT INTO model_metrics_daily (fecha, model, seed, source, n_obs, mae,
                    mae_naive, skill_vs_naive, estado, naive_regla)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (fecha, model, seed, source) DO UPDATE SET
                    n_obs=EXCLUDED.n_obs, mae=EXCLUDED.mae, mae_naive=EXCLUDED.mae_naive,
                    skill_vs_naive=EXCLUDED.skill_vs_naive, estado=EXCLUDED.estado,
                    naive_regla=EXCLUDED.naive_regla, calculado_en=now()""", filas)
        con.commit()

    raros = [f for f in filas if f[8] != "ok"]
    _log("serie", f"{len(filas)} filas (dia x modelo x semilla)"
                  f"{f' · {len(raros)} marcadas: ' + ', '.join(sorted({f[8] for f in raros})) if raros else ''}"
                  f"{'' if escribir else '  [simulacro, no escritas]'}")

    # La media movil en consola, para el modelo con mas dias: es la lectura que importa.
    t = pd.DataFrame(filas, columns=["fecha", "model", "seed", "source", "n", "mae",
                                     "mae_naive", "skill", "estado", "regla"])
    t = t[t.estado.isin(["ok", "cambio_hora"])]
    if not t.empty:
        modelo = t.groupby(["model", "seed"]).size().idxmax()
        g = t[(t.model == modelo[0]) & (t.seed == modelo[1])].sort_values("fecha")
        num = (g.mae * g.n).rolling(7).sum()
        den = (g.mae_naive * g.n).rolling(7).sum()
        mov = 100 * (1 - num / den)
        vistos = mov.dropna()
        if len(vistos) >= 2:
            print(f"\n  media movil de 7 dias · {modelo[0]} (s{modelo[1]}): "
                  f"{vistos.iloc[0]:+.1f} % -> {vistos.iloc[-1]:+.1f} %")
            print("    " + "  ".join(f"{v:+.0f}" for v in vistos))
    return len(filas)


# ------------------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dia", help="evaluar solo este dia objetivo")
    ap.add_argument("--dias", type=int, default=VENTANA,
                    help=f"ventana hacia atras para la metrica movil (por defecto {VENTANA})")
    ap.add_argument("--simulacro", action="store_true", help="calcula y enseña, no escribe")
    a = ap.parse_args()

    from guardar_predicciones import conexion
    con = conexion()
    escribir = not a.simulacro
    try:
        if a.dia:
            desde = hasta = pd.Timestamp(a.dia).date()
        else:
            # HASTA MAÑANA, NO HASTA HOY. Lo que se predice es D+1 y su precio se
            # publica a las 13:00 de D, asi que a las 13:30 -- cuando corre el cron -- el
            # dia recien predicho YA es evaluable. Con `hasta = hoy` se quedaba fuera y se
            # recuperaba al dia siguiente: no era un agujero permanente, pero el panel iba
            # siempre un dia por detras y el resultado del dia nunca estaba. Los dias sin
            # precio todavia no entran igualmente: el JOIN contra `spot_price` los descarta.
            hasta = date.today() + timedelta(days=1)
            desde = hasta - timedelta(days=a.dias)       # `dias` dias cerrados + manana

        datos = cargar(con, desde, hasta)
        real = curva_real(con, desde - timedelta(days=1), hasta)
        evaluar_dias(con, datos, real, escribir, desde, hasta)
        if datos.empty:
            print(f"\n  No hay predicciones de produccion con precio publicado entre "
                  f"{desde} y {hasta}.")
            print("  Si la pasada de hoy es de esta manana, el PMD del dia objetivo aun no")
            print("  existe: OMIE lo publica a las 13:00 del dia anterior al objetivo.")
            return
        dias = sorted(indice_local(datos.ts).date)
        _log("datos", f"{len(datos):,} horas · {datos.model.nunique()} modelos · "
                      f"{len(dias)} dias ({dias[0]} -> {dias[-1]})")

        serie_diaria(con, datos, real, escribir)
        ventana(con, datos, real, escribir)
    finally:
        con.close()

    if a.simulacro:
        print("\n  (simulacro) no se ha escrito nada. Quita --simulacro para guardarlo.")


if __name__ == "__main__":
    main()
