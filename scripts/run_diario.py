"""La pasada del dia: construir la fila de manana, predecir, planificar la bateria.

QUE PROBLEMA RESUELVE
Las piezas ya estaban todas -- `construir_matriz_produccion.py` levanta la fila de D+1,
`predecir.py` sirve los ocho representantes y `guardar_predicciones.py` los escribe en
`predictions` --, pero nadie las encadenaba. Sin eso hay un backtest muy bien medido y
cero filas con `source='production'`: el contador del panel se queda en 0 para siempre.

LA VENTANA
El mercado diario de OMIE casa a las 12:00 del dia D las 24 horas de D+1. Una prediccion
que llegue despues ya no se puede ofertar, asi que la pasada tiene que caber antes. A las
11:15 esta todo publicado:

    07:00 UTC   ECMWF publica el run 00Z              la prevision meteo de D+1
    13:00 D-1   OMIE publica el PMD del dia D         el lag de precio
    13:45 D-1   REE publica el PBF del dia D          las columnas `pbf*_D` y `pdbc_*_D`
    15:00 D-1   el cron de esios carga todo eso en la BD
    ---------------------------------------------------- 11:15 D   esta pasada
    12:00 D     cierra la casacion de D+1

El cron de esios va a las 15:00 y no a las 9:00 a proposito: el indicador 462 no existe
hasta que se publica el PBF. Eso no estorba aqui, porque la fila de D+1 usa el PBF del
dia D -- publicado la tarde de D-1 --, no el de D+1. Ninguna columna describe D+1 salvo
el calendario y la meteo, y esas dos son legitimas: el calendario es determinista y la
meteo es prevision, no dato realizado. Lo comprueba `auditoria_frontera.py` por desfase
medido, columna a columna.

ESTO NO ES EL CRON DE PRODUCCION
El cron es el de `production/crontab.txt`: tres lineas a las 11:15, 11:30 y 11:45 que hacen
lo mismo por separado, y son las que estan instaladas en el servidor. Este script es la
version manual -- una orden en vez de tres -- para probar la cadena entera de un tirON y
para el `--revisar`. No hay que cambiar el crontab por esto.

QUE COMPRUEBAN DE VERDAD LOS PASOS 2 Y 3, QUE ES MENOS DE LO QUE PARECE
El paso 2 compara el solape con `matriz_nucleo`, pero `_empalmar` construye ese pasado
COPIANDO `matriz_nucleo`, asi que compara una copia consigo misma: solo puede fallar si
cambia el catalogo de columnas, cosa que el constructor ya aborta antes por su cuenta.

El paso 3 mira si la fila de manana tiene huecos, y no los va a tener nunca: `apagon.imputar`
y `depurar_matriz.depurar` corren ANTES y dejan la matriz en cero nulos. Si una fuente
estuviera caida, sus celdas no llegarian vacias sino rellenadas por "analogo de hace 7 dias",
y este paso diria que todo esta bien.

Sirven como red contra un fallo tonto -- que la fila del dia no se haya creado, que el
constructor haya petado -- y para nada mas. La comprobacion que de verdad hace falta esta en
el `inf` que devuelven `apagon.imputar` y `depurar_matriz.depurar`, y que
`construir_matriz_produccion` descarta con un `_`: cuantas celdas de la fila de manana son
reales y cuantas reconstruidas. Eso hay que hablarlo con Torgio, que es codigo suyo.

    python scripts/run_diario.py --revisar          # que hay ya escrito; no toca la BD
    python scripts/run_diario.py --simulacro        # llega hasta el paso 3 y no escribe
    python scripts/run_diario.py
    python scripts/run_diario.py --dia 2026-08-29   # rehacer la pasada de un dia
    python scripts/run_diario.py --usar-cache       # reaprovecha el bronce si ya es de hoy
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
for p in ("scripts", "modelos", "ingesta"):
    sys.path.insert(0, str(REPO / p))

ORO = REPO / "data" / "gold"
MATRIZ = ORO / "matriz_produccion"
TZ = "Europe/Madrid"
CIERRE = 12                      # hora de Madrid a la que casa OMIE

from evaluar_modelos import POTENCIA_MW, CAPACIDAD_MWH, EFICIENCIA, HORAS  # noqa: E402

SIMULADOR = {"potencia_mw": POTENCIA_MW, "capacidad_mwh": CAPACIDAD_MWH,
             "eficiencia": EFICIENCIA, "ciclos_dia": 1, "horizonte": "D+1",
             "regla": "carga en las HORAS mas baratas predichas, descarga en las mas caras"}

# Columnas que pueden venir vacias en la fila de manana sin que eso sea un fallo.
# `target_price` es el precio de D+1: no existe todavia, es justo lo que se predice.
HUECO_LEGITIMO = {"target_price", "split", "fecha_pred"}


def _log(paso, texto):
    print(f"[{datetime.now():%H:%M:%S}] {paso}  {texto}", flush=True)


# ------------------------------------------------------------------ 1 y 2  la matriz
def construir(objetivo: date, usar_cache: bool) -> bool:
    """Delega en el script del equipo en vez de reimplementarlo.

    Se lanza como proceso aparte a proposito: `construir_matriz_produccion` toca los
    modulos del constructor del equipo (MODELO_END, EXIGIR_TARGET, ...) y los restaura en
    un `finally`. Un proceso nuevo por pasada garantiza que ningun ajuste sobreviva a la
    ejecucion, pase lo que pase por el camino.
    """
    orden = [sys.executable, str(REPO / "scripts" / "construir_matriz_produccion.py"),
             "--hasta", objetivo.isoformat()]
    if usar_cache:
        orden.append("--usar-cache")
    _log("1 matriz", f"corte en {objetivo}  ({' '.join(orden[1:])})")
    if subprocess.run(orden, cwd=REPO).returncode != 0:
        _log("1 matriz", "ABORTA: el constructor ha fallado")
        return False

    meta = json.loads(Path(f"{MATRIZ}.meta.json").read_text(encoding="utf-8"))
    _log("2 contrato", f"hash {meta['hash']} · {meta['filas']:,} filas x {meta['columnas']} col")
    if not meta.get("solape_identico"):
        _log("2 contrato", "ABORTA: el solape con matriz_nucleo NO es identico.")
        print("    Los dias de train han cambiado, luego los escaladores tambien, luego")
        print("    los modelos guardados recibirian la entrada en otra escala. Mira que")
        print("    columnas difieren con:  python scripts/construir_matriz_produccion.py --verificar")
        return False
    _log("2 contrato", "solape identico -> los modelos guardados siguen siendo validos")
    return True


# ------------------------------------------------------------------ 3  la fila del dia
def revisar_fila(objetivo: date) -> bool:
    """¿Estan de verdad las 24 horas de manana, y sin huecos?

    Es la comprobacion que detecta una fuente caida. Si el cron de esios no corrio ayer a
    las 15:00, la matriz se construye igual y las columnas del PBF salen a NaN: el modelo
    predeciria con imputaciones y nadie se enteraria. Mejor no predecir.
    """
    d = pd.read_parquet(f"{MATRIZ}.parquet")
    d["fecha_objetivo"] = pd.to_datetime(d["fecha_objetivo"]).dt.date
    fila = d[d["fecha_objetivo"] == objetivo]

    _log("3 frontera", f"{len(fila)} horas para {objetivo}")
    if len(fila) < 23:                      # 23 el domingo de marzo, 25 el de octubre
        _log("3 frontera", "ABORTA: no estan las horas del dia. La fila de manana no se ha creado.")
        print("    Suele significar que `ESPINA_CALENDARIO` no llego a activarse o que la")
        print("    ultima fecha leible de la BD se queda corta.")
        return False

    mirar = [c for c in fila.columns if c not in HUECO_LEGITIMO]
    huecos = fila[mirar].isna().sum()
    huecos = huecos[huecos > 0]
    if len(huecos):
        _log("3 frontera", f"ABORTA: {len(huecos)} columnas con huecos en la fila de manana")
        for c, n in huecos.head(12).items():
            print(f"      {c:38s} {n:>3d}/{len(fila)} horas vacias")
        if len(huecos) > 12:
            print(f"      ... y {len(huecos) - 12} mas")
        print("    Mira que tabla las alimenta y si su cron corrio ayer.")
        return False
    _log("3 frontera", f"sin huecos en {len(mirar)} columnas")
    return True


# ------------------------------------------------------------- 0  que hay ya escrito
def _por_fuente(con, objetivo):
    with con.cursor() as cur:
        cur.execute("""
            SELECT source, count(*), count(DISTINCT model), max(updated_at)
              FROM predictions
             WHERE (datetime AT TIME ZONE 'Europe/Madrid')::date = %s
             GROUP BY source ORDER BY source""", (objetivo,))
        return cur.fetchall()


def revisar(con, objetivo):
    """Solo lee. Que hay hoy en las tablas que la pasada va a escribir.

        python scripts/run_diario.py --revisar
    """
    with con.cursor() as cur:
        cur.execute("""
            SELECT source, count(*), count(DISTINCT model),
                   min((datetime AT TIME ZONE 'Europe/Madrid')::date),
                   max((datetime AT TIME ZONE 'Europe/Madrid')::date), max(updated_at)
              FROM predictions GROUP BY source ORDER BY source""")
        todo = cur.fetchall()
    print("  predictions, lo que hay ahora")
    print(f"    {'source':12s} {'filas':>8s} {'modelos':>8s}  desde        hasta        ultima escritura")
    for s, n, m, d0, d1, up in todo:
        print(f"    {s:12s} {n:8,} {m:8d}  {d0}   {d1}   {up:%Y-%m-%d %H:%M}")

    print(f"\n  el dia que se va a escribir: {objetivo}")
    filas = _por_fuente(con, objetivo)
    if not filas:
        print("    vacio -> la pasada no pisa nada")
    for s, n, m, up in filas:
        aviso = "  <-- SE CONVERTIRIA EN 'production'" if s == "test" else "  (se refresca)"
        print(f"    {s:12s} {n:5,} filas · {m} modelos · escrito {up:%Y-%m-%d %H:%M}{aviso}")

    with con.cursor() as cur:
        cur.execute("""SELECT model, count(*) FROM bess_plan
                        WHERE (datetime AT TIME ZONE 'Europe/Madrid')::date = %s
                        GROUP BY model""", (objetivo,))
        bp = cur.fetchall()
    print(f"\n  bess_plan para {objetivo}: " +
          (", ".join(f"{m} ({n} horas)" for m, n in bp) if bp else "vacio"))
    print(f"  campeon que decidiria el plan: {campeon(con)}")


def choque(con, objetivo, forzar: bool) -> bool:
    """El backfill de test y la produccion no pueden convivir en el mismo dia.

    La PK de `predictions` es (datetime, model) y NO incluye `source`. Es una decision del
    equipo y para el uso normal esta bien -- un dia es de test o es de produccion, no de
    los dos --, pero tiene un filo: un INSERT de produccion sobre una hora que ya tiene
    fila de test no crea una fila nueva, sino que ejecuta el DO UPDATE y le cambia el
    `source`. La prediccion del backfill desaparece y la unica forma de recuperarla es
    volver a lanzar `--backfill` entero.

    En la pasada normal esto no puede pasar: manana no esta en el tramo de test. Salta si
    se usa `--dia` apuntando hacia atras, que es justo cuando uno no se lo espera.
    """
    for s, n, m, up in _por_fuente(con, objetivo):
        if s == "test" and not forzar:
            _log("0 choque", f"ABORTA: {objetivo} ya tiene {n:,} filas de test ({m} modelos)")
            print("    La PK de `predictions` no incluye `source`, asi que escribir")
            print("    produccion encima las convertiria y perderias el backfill de ese dia.")
            print("    Si de verdad lo quieres:  --forzar")
            return False
        if s == "test":
            _log("0 choque", f"AVISO: --forzar activo, se pisan {n:,} filas de test de {objetivo}")
        else:
            _log("0 choque", f"{objetivo} ya tiene {n:,} filas de {s}: se refrescan")
    return True


# --------------------------------------------------------------------- 4  predecir
def predecir(con, objetivo: date):
    """Los once modelos y el `ensemble11`, por la puerta del equipo.

    `equipo=True` sirve ademas de las ocho redes los tres arboles que se pueden cargar --
    el lightgbm y el xgboost de Magdalena y el lgbm_nucleo de Willy -- y guarda la media de
    los once como `ensemble11`, no como `ensemble`: esa serie ya esta grabada y pisarla
    haria incomparables los numeros de la memoria.

    Aqui hubo un paso 4b propio que servia los dos boosting por su cuenta. Sobra: hacia
    menos (dos modelos en vez de tres, sin ensemble ampliado) y por otra puerta. Se sirve
    todo por `modelos_equipo.py`, que es de Torgio y ya resuelve el contrato de columnas.
    """
    import inspect
    import guardar_predicciones as gp
    if "equipo" not in inspect.signature(gp.produccion).parameters:
        raise SystemExit(
            "  Tu `guardar_predicciones.produccion` no acepta `equipo`.\n"
            "  Es la version de antes del 1-sep. Trae los commits de Torgio primero:\n"
            "      git fetch origin && git merge origin/main --no-edit")
    _log("4 predecir", f"11 modelos + ensemble11 sobre {objetivo}")
    gp.produccion(con, desde=objetivo.isoformat(), hasta=objetivo.isoformat(),
                  matriz="produccion", verbose=True, equipo=True)


# ------------------------------------------------------------------ 5  la bateria
def campeon(con) -> str:
    """Quien decide el plan de la bateria.

    Sale de `models.estado`, no de una constante aqui: el campeon cambia cuando cambian
    las metricas, y si estuviera escrito en el codigo habria dos verdades. Si aun no hay
    ninguno declarado, manda el ensemble -- que es la eleccion conservadora, no la mejor.
    """
    with con.cursor() as cur:
        cur.execute("SELECT model FROM models WHERE estado = 'campeon' ORDER BY model LIMIT 1")
        r = cur.fetchone()
    return r[0] if r else "ensemble"


def planificar(con, objetivo: date, modelo: str) -> int:
    """El plan de carga y descarga de manana, hora a hora, decidido con la prediccion.

    `ingreso_eur` aqui es el ingreso ESPERADO, valorado a precio predicho: es lo que la
    bateria creeria que va a ganar. Lo que gane de verdad se liquida a precio real cuando
    OMIE lo publique, y eso va a `bess_result` -- otra tabla, a proposito, porque la
    diferencia entre las dos es exactamente la parte del error del modelo que cuesta
    dinero.
    """
    with con.cursor() as cur:
        cur.execute("""
            SELECT datetime, prediction FROM predictions
             WHERE model = %s AND source = 'production'
               AND (datetime AT TIME ZONE 'Europe/Madrid')::date = %s
             ORDER BY datetime""", (modelo, objetivo))
        filas = cur.fetchall()
    if len(filas) < 23:
        _log("5 bateria", f"no hay prediccion completa de {modelo} para {objetivo} "
                          f"({len(filas)} horas): no se planifica")
        return 0

    p = np.array([float(x[1]) for x in filas])
    orden = p.argsort()
    carga, descarga = set(orden[:HORAS].tolist()), set(orden[-HORAS:].tolist())

    soc, plan = 0.0, []
    for i, (ts, _) in enumerate(filas):
        c = POTENCIA_MW if i in carga else 0.0
        dsc = POTENCIA_MW if i in descarga else 0.0
        soc += c - dsc
        # El ingreso esperado paga la carga a precio predicho y cobra la descarga con la
        # eficiencia aplicada, igual que `arbitraje()` en evaluar_modelos: un solo
        # simulador para el backtest y para produccion, o las capturas no se comparan.
        # float() no es decorativo: `p` es un np.array, asi que `p[i]` es np.float64 y
        # psycopg2 no sabe adaptarlo -- lo interpola con su repr, que en NumPy 2 pasó a
        # ser "np.float64(0.0)", y Postgres se pone a buscar un esquema llamado `np`.
        plan.append((ts, modelo, float(c), float(dsc), float(soc),
                     float(EFICIENCIA * dsc * p[i] - c * p[i]), json.dumps(SIMULADOR)))

    with con.cursor() as cur:
        cur.executemany("""
            INSERT INTO bess_plan (datetime, model, carga_mw, descarga_mw, soc_mwh,
                                   ingreso_eur, simulador)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (datetime, model) DO UPDATE SET
                carga_mw    = EXCLUDED.carga_mw,
                descarga_mw = EXCLUDED.descarga_mw,
                soc_mwh     = EXCLUDED.soc_mwh,
                ingreso_eur = EXCLUDED.ingreso_eur,
                simulador   = EXCLUDED.simulador,
                updated_at  = now()""", plan)
    con.commit()

    hc = [f"{pd.Timestamp(filas[i][0]).tz_convert(TZ):%H}h" for i in sorted(carga)]
    hd = [f"{pd.Timestamp(filas[i][0]).tz_convert(TZ):%H}h" for i in sorted(descarga)]
    esperado = sum(r[5] for r in plan)
    _log("5 bateria", f"{modelo}: carga {' '.join(hc)} · descarga {' '.join(hd)} · "
                      f"esperado {esperado:,.2f} EUR/dia")
    return len(plan)


# ------------------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dia", help="el dia D de la pasada (por defecto hoy); se predice D+1")
    ap.add_argument("--simulacro", action="store_true",
                    help="construye y comprueba, pero no escribe nada en la base")
    ap.add_argument("--usar-cache", action="store_true")
    ap.add_argument("--revisar", action="store_true",
                    help="solo mira que hay ya en predictions y bess_plan; no escribe nada")
    ap.add_argument("--forzar", action="store_true",
                    help="escribir aunque el dia tenga filas del backfill de test")
    a = ap.parse_args()

    hoy = pd.Timestamp(a.dia).date() if a.dia else date.today()
    objetivo = hoy + timedelta(days=1)

    ahora = pd.Timestamp.now(tz=TZ)
    print(f"\n  pasada del {hoy}  ->  se predice {objetivo}")
    print(f"  bateria {POTENCIA_MW:g} MW / {CAPACIDAD_MWH:g} MWh · "
          f"rendimiento {EFICIENCIA:.0%} · 1 ciclo/dia\n")
    if not a.dia and ahora.hour >= CIERRE:
        print(f"  AVISO: son las {ahora:%H:%M} y la casacion de {objetivo} cerro a las "
              f"{CIERRE}:00.")
        print(f"  La prediccion se guarda igual y es valida como prevision, pero ya no se")
        print(f"  podria haber ofertado. El panel lo marca comparando `updated_at` con el")
        print(f"  cierre, asi que no se cuela como si hubiera llegado a tiempo.\n")

    if a.revisar:
        from guardar_predicciones import conexion
        con = conexion()
        try:
            revisar(con, objetivo)
        finally:
            con.close()
        return

    if not construir(objetivo, a.usar_cache):
        raise SystemExit(1)
    if not revisar_fila(objetivo):
        raise SystemExit(1)

    if a.simulacro:
        print("\n  (simulacro) hasta aqui llega sin tocar la base. Sin --simulacro seguiria")
        print("  con el paso 4 (predecir a `predictions`) y el 5 (planificar en `bess_plan`).")
        return

    from guardar_predicciones import conexion
    con = conexion()
    try:
        if not choque(con, objetivo, a.forzar):
            raise SystemExit(1)
        predecir(con, objetivo)
        con.commit()
        m = campeon(con)
        planificar(con, objetivo, m)
    finally:
        con.close()

    print(f"\n  pasada completa · {objetivo} predicho y planificado")
    print(f"  comprueba con:  python scripts/guardar_predicciones.py --resumen")


if __name__ == "__main__":
    main()
