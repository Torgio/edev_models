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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from zoneinfo import ZoneInfo

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
         -- El segundo criterio no es decorativo: el domingo de octubre las dos 02:00
         -- comparten la primera clave y sin desempate el orden lo decide el planificador.
         -- Ordenando tambien por el instante, "la primera" es SIEMPRE la de +02:00.
         ORDER BY 1, datetime""", con, params=(desde, hasta))
    s = d.set_index(pd.to_datetime(d.ts))["real"]
    # El domingo de octubre la 02:00 aparece DOS veces al convertir a hora local (la
    # +02:00 y la +01:00). En timestamptz son horas distintas, pero como indice local son
    # el mismo valor y `reindex` estalla con "cannot reindex on an axis with duplicates".
    # Esa hora solo se usa para el naive del dia siguiente. Se elige LA PRIMERA, que con el
    # ORDER BY de arriba es la de +02:00 (la anterior al retraso del reloj). La otra no se
    # usa: el dia siguiente queda marcado `ayer_cambio_hora` para que se sepa.
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
    # `prod_30d` son 30 dias POR DEFINICION. Una pasada puede cargar mas -- el backfill de
    # la serie diaria pide `--dias 40` -- y entonces esta fila diria 30 y serian 40. Se
    # recorta aqui, no en main, para que la etiqueta y el contenido no puedan divergir.
    fechas = pd.to_datetime(datos.ts).dt.date
    datos = datos[fechas >= fechas.max() - timedelta(days=VENTANA - 1)]

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
                      _f(m["MAE"]), _f(m["RMSE"]), _f(m["sMAPE"]), None, None,
                      _f(m["captura_%"]), _f(m["eur_dia"]), _f(m["pico_1h_%"]),
                      _f(m.get("skill_%")), json.dumps(SIMULADOR)))
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
                      _f(mn["MAE"]), _f(mn["RMSE"]), _f(mn["sMAPE"]), None, None,
                      _f(mn["captura_%"]), _f(mn["eur_dia"]), _f(mn["pico_1h_%"]), 0.0,
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


NAIVE_REGLA = ("precio de la MISMA HORA LOCAL del dia anterior. Los dos domingos del "
               "cambio de hora el desfase real no son 24 h sino 23 o 25, porque el indice "
               "es hora peninsular; esos dias quedan marcados estado='cambio_hora'")


def horas_del_dia(d: date) -> int:
    """23, 24 o 25: las horas que tiene ESE dia en hora peninsular, segun el calendario.

    El estado de un dia no se puede deducir de cuantas filas llegaron. Marzo llega con 23
    porque la 02:00 no existe, pero un dia normal al que le falta una hora tambien llega
    con 23 y no es lo mismo: uno es el calendario y el otro es un hueco que hay que ver.

    Octubre si llega entero con sus 25 horas -- `cargar()` une por `datetime`, que es
    timestamptz, y los dos instantes de las 02:00 son filas distintas aunque compartan
    etiqueta local. Lo que se pierde es otra cosa y en otro dia: `curva_real()` descarta la
    02:00 repetida para poder reindexar, asi que el naive del 26 a las 02:00 sale de la
    PRIMERA de las dos 02:00 del 25 -- la de +02:00, fijada por el ORDER BY de la consulta.
    Por eso el 25 sale `cambio_hora` y el 26 `ayer_cambio_hora`.
    """
    tz = ZoneInfo(TZ)
    ini = datetime(d.year, d.month, d.day, tzinfo=tz)
    return int(((ini + timedelta(days=1)).astimezone(timezone.utc)
                - ini.astimezone(timezone.utc)).total_seconds() // 3600)


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
    perfectamente comparable; lo que no lo es es su dinero, que necesita 24 h para cerrar
    el ciclo. Guardarlos con `estado` deja que el panel diga cuantos excluye y por que, como
    ya hace el contador de acierto de pico.
    """
    idx = pd.to_datetime(real.index)
    naive = pd.Series(real.values, index=idx + pd.Timedelta(days=1))   # el precio de ayer
    filas = []
    d = datos.assign(dia=pd.to_datetime(datos.ts).dt.date)
    for (modelo, seed, dia), g in d.groupby(["model", "seed", "dia"]):
        g = g.set_index(pd.to_datetime(g.ts)).sort_index()
        err = (g.pred - g.real).abs()
        errn = (naive.reindex(g.index) - g.real).abs()
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
        serie_diaria(con, datos, real, escribir)
        ventana(con, datos, real, escribir)
    finally:
        con.close()

    if a.simulacro:
        print("\n  (simulacro) no se ha escrito nada. Quita --simulacro para guardarlo.")


if __name__ == "__main__":
    main()
