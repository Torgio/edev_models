"""Cerrar el bucle: cuando OMIE publica el precio, medir lo que se predijo.

QUE PROBLEMA RESUELVE
`run_diario.py` deja cada dia 264 filas en `predictions` y un plan en `bess_plan`. Eso es
una promesa, no una medida. A las 13:00 del dia siguiente OMIE publica el PMD y la promesa
se puede contrastar -- pero si nadie la contrasta, el unico numero que tenemos para
defender el trabajo sigue siendo el MAE del tramo de test, que es una foto de enero a
julio de 2026 sobre dias que eligio el reparto.

POR QUE ESTE NUMERO VALE MAS QUE EL DEL TEST
En test los dias los eligio un `split`. Aqui son simplemente los que han pasado: nadie los
escogio, ningun modelo los vio al entrenar, y el precio con el que se liquidan se publico
DESPUES de la prediccion. Es la unica metrica del proyecto que no admite la sospecha de
haber mirado al futuro.

DOS TABLAS, DOS GRANULARIDADES, Y NO ES CAPRICHO
    bess_result     una fila por (dia, modelo). El dinero es un hecho de un dia concreto:
                    el 2 de septiembre la bateria gano X. Guardarlo por dia permite
                    reconstruir despues cualquier ventana, y ver el dia malo que una media
                    de 30 dias esconde.
    model_metrics   una fila por modelo con `periodo='prod_30d'`. Es una ventana movil que
                    se RECALCULA entera en cada pasada, no se acumula. El panel pregunta
                    "¿como va ahora?", y ahora son los ultimos 30 dias, no los ultimos 300.

EL SIMULADOR ES EL MISMO QUE EL DEL BACKTEST
Potencia, capacidad, eficiencia y ciclos se importan de `evaluar_modelos.py`. Si aqui se
declararan otra vez, la captura de produccion y la de validacion dejarian de ser
comparables sin que nadie se diera cuenta -- que es exactamente el problema del que nacio
el leaderboard unico. Los supuestos viajan en la columna `simulador` junto a cada numero.

    python scripts/evaluar_diario.py                    # todos los dias pendientes
    python scripts/evaluar_diario.py --dia 2026-09-02
    python scripts/evaluar_diario.py --simulacro        # calcula y enseña, no escribe
    python scripts/evaluar_diario.py --rehacer          # recalcula dias ya evaluados

En el cron, despues de que OMIE publique:

    CRON_TZ=Europe/Madrid
    30 13 * * * /home/ubuntu/tfm-env/bin/python /home/ubuntu/scripts/evaluar_diario.py \
                >> /home/ubuntu/scripts/logs/evaluar_diario.log 2>&1
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

from evaluar_modelos import (POTENCIA_MW, CAPACIDAD_MWH, EFICIENCIA, HORAS,  # noqa: E402
                             metricas)

SIMULADOR = {"potencia_mw": POTENCIA_MW, "capacidad_mwh": CAPACIDAD_MWH,
             "eficiencia": EFICIENCIA, "ciclos_dia": 1, "horizonte": "D+1",
             "regla": "carga en las HORAS mas baratas predichas, descarga en las mas caras"}


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
               (p.datetime AT TIME ZONE 'Europe/Madrid')     AS ts,
               p.prediction::double precision                AS pred,
               s.es_esios::double precision                  AS real
          FROM predictions p
          JOIN spot_price s ON s.datetime = p.datetime
         WHERE p.source = 'production'
           AND s.es_esios IS NOT NULL
           AND (p.datetime AT TIME ZONE 'Europe/Madrid')::date BETWEEN %s AND %s
         ORDER BY p.model, p.datetime""", con, params=(desde, hasta))


def curva_real(con, desde: date, hasta: date) -> pd.Series:
    """El PMD publicado, indexado por hora local. Sirve de verdad y de baseline naive."""
    d = pd.read_sql("""
        SELECT (datetime AT TIME ZONE 'Europe/Madrid') AS ts,
               es_esios::double precision              AS real
          FROM spot_price
         WHERE es_esios IS NOT NULL
           AND (datetime AT TIME ZONE 'Europe/Madrid')::date BETWEEN %s AND %s
         ORDER BY 1""", con, params=(desde, hasta))
    s = d.set_index(pd.to_datetime(d.ts))["real"]
    # El domingo de octubre la 02:00 aparece DOS veces al convertir a hora local (la
    # +02:00 y la +01:00). En timestamptz son horas distintas, pero como indice local son
    # el mismo valor y `reindex` estalla con "cannot reindex on an axis with duplicates".
    # Esa hora solo se usa para el naive del dia siguiente, asi que basta con la primera.
    return s[~s.index.duplicated(keep="first")]


# ------------------------------------------------------------------ la liquidacion
def liquidar(pred: np.ndarray, real: np.ndarray) -> float:
    """Lo que gana la bateria decidiendo con `pred` y cobrando a precio `real`.

    Identico a `arbitraje()` en evaluar_modelos, pero para un solo dia: el modelo elige el
    cuando, el mercado pone el cuanto. La eficiencia se aplica a la descarga porque el MWh
    que sale es menos que el que entro.
    """
    orden = pred.argsort()
    carga, descarga = orden[:HORAS], orden[-HORAS:]
    return float(EFICIENCIA * real[descarga].sum() - real[carga].sum())


def dia_de(g: pd.DataFrame, real_ayer: np.ndarray | None) -> dict | None:
    """Las tres cifras de un dia: lo que gano el modelo, el techo y el suelo."""
    g = g.sort_values("ts")
    if len(g) != 24:               # 23 en marzo, 25 en octubre: no comparables con el resto
        return None
    p, y = g.pred.values, g.real.values
    ingreso = liquidar(p, y)
    oraculo = liquidar(y, y)       # decidir con el precio real = prevision perfecta
    naive = liquidar(real_ayer, y) if real_ayer is not None and len(real_ayer) == 24 else None
    return {
        "ingreso_eur": ingreso,
        "ingreso_oraculo_eur": oraculo,
        "ingreso_naive_eur": naive,
        "captura_pct": 100 * ingreso / oraculo if oraculo else None,
        # Un ciclo por dia por construccion: se cargan HORAS horas a POTENCIA y se
        # descargan otras tantas, o sea CAPACIDAD_MWH dentro y CAPACIDAD_MWH fuera.
        "ciclos": HORAS * POTENCIA_MW / CAPACIDAD_MWH,
    }


def evaluar_dias(con, datos: pd.DataFrame, real: pd.Series, escribir: bool) -> int:
    filas = []
    datos = datos.assign(dia=pd.to_datetime(datos.ts).dt.date)
    for (modelo, dia), g in datos.groupby(["model", "dia"]):
        ayer = real[pd.to_datetime(real.index).date == dia - timedelta(days=1)].values
        r = dia_de(g, ayer if len(ayer) == 24 else None)
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
    idx = pd.to_datetime(real.index)
    naive = pd.Series(real.values, index=idx + pd.Timedelta(days=1))   # el precio de ayer
    filas = []
    for (modelo, seed), g in datos.groupby(["model", "seed"]):
        g = g.set_index(pd.to_datetime(g.ts))
        nv = naive.reindex(g.index)
        mae_naive = (nv - g.real).abs().mean()
        m = metricas(g.pred, g.real, mae_ref=mae_naive if pd.notna(mae_naive) else None)
        if m is None:
            continue
        filas.append((modelo, int(seed), "prod_30d", "global", int(m["n_horas"]),
                      m["MAE"], m["RMSE"], m["sMAPE"], None, None,
                      m["captura_%"], m["eur_dia"], m["pico_1h_%"], m.get("skill_%"),
                      json.dumps(SIMULADOR)))
    # LA PERSISTENCIA, COMO UN MODELO MAS. Sin esta fila la tabla no contesta la unica
    # pregunta que importa: ¿aporta el modelo, o el dinero lo pone la horquilla del
    # mercado? En un mes de dias parecidos, predecir "manana como hoy" puede capturar casi
    # lo mismo con MAE mucho peor -- y entonces el valor del modelo no esta en el MAE.
    # Se mide sobre las MISMAS horas que el modelo con mas cobertura, no sobre todas.
    ref = max(datos.groupby(["model", "seed"]), key=lambda kv: len(kv[1]))[1]
    ridx = pd.to_datetime(ref.ts)
    nv = naive.reindex(ridx)
    mn = metricas(nv, pd.Series(ref.real.values, index=ridx))
    if mn is not None:
        filas.append(("naive_D1", -1, "prod_30d", "global", int(mn["n_horas"]),
                      mn["MAE"], mn["RMSE"], mn["sMAPE"], None, None,
                      mn["captura_%"], mn["eur_dia"], mn["pico_1h_%"], 0.0,
                      json.dumps(SIMULADOR)))

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

    # COMPARABILIDAD. Un modelo con 3 dias y otro con 31 no se pueden ordenar juntos: no
    # han corrido los mismos dias y agosto no reparte la dificultad por igual. Se marcan
    # los que no cubren la ventana entera y se listan aparte, en vez de mezclarlos en un
    # ranking que invita a leer como "peor" lo que solo es "medido en otros dias".
    completo = max((r[4] for r in filas), default=0)
    llenos = sorted([r for r in filas if r[4] >= completo * 0.9], key=lambda r: r[5])
    cojos = sorted([r for r in filas if r[4] < completo * 0.9], key=lambda r: r[5])

    def cuadro(rs, titulo):
        if not rs:
            return
        print(f"\n  {titulo}\n")
        print(f"    {'model':18s} {'sem':>4s} {'dias':>5s} {'MAE':>7s} {'sMAPE':>7s} "
              f"{'captura':>8s} {'pico':>6s} {'skill':>7s} {'EUR/dia':>8s}")
        for r in rs:
            sk = f"{r[13]:6.1f}%" if r[13] is not None and pd.notna(r[13]) else "     --"
            sem = "--" if r[1] == -1 else str(r[1])
            print(f"    {r[0]:18s} {sem:>4s} {r[4]//24:5d} {r[5]:7.2f} {r[7]:6.1f}% "
                  f"{r[10]:7.2f}% {r[12]:5.1f}% {sk} {r[11]:8.2f}")

    marca = "" if escribir else "   [simulacro, no escrito]"
    cuadro(llenos, f"produccion · ventana completa ({completo // 24} dias){marca}")
    cuadro(cojos, "cobertura parcial · NO comparables con los de arriba: son otros dias")
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
            hasta = date.today()
            desde = hasta - timedelta(days=a.dias - 1)   # inclusivo por los dos lados

        datos = cargar(con, desde, hasta)
        if datos.empty:
            print(f"\n  No hay predicciones de produccion con precio publicado entre "
                  f"{desde} y {hasta}.")
            print("  Si la pasada de hoy es de esta manana, el PMD del dia objetivo aun no")
            print("  existe: OMIE lo publica a las 13:00 del dia anterior al objetivo.")
            return
        dias = sorted(pd.to_datetime(datos.ts).dt.date.unique())
        _log("datos", f"{len(datos):,} horas · {datos.model.nunique()} modelos · "
                      f"{len(dias)} dias ({dias[0]} -> {dias[-1]})")

        # un dia mas por detras: el naive del primer dia es el precio del dia anterior
        real = curva_real(con, desde - timedelta(days=1), hasta)

        evaluar_dias(con, datos, real, escribir)
        ventana(con, datos, real, escribir)
    finally:
        con.close()

    if a.simulacro:
        print("\n  (simulacro) no se ha escrito nada. Quita --simulacro para guardarlo.")


if __name__ == "__main__":
    main()
