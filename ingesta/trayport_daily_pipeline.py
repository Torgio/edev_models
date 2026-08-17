#!/usr/bin/env python3
"""
TFM Energia UCM — Trayport Daily Pipeline v2
============================================
Mantiene al dia las dos series continuas de futuros en `commodities`:
    co2_eua_dec  — EUA front December (EUR/t)
    gas_ttf_m1   — TTF M+1            (EUR/MWh)

QUE CAMBIA RESPECTO A LA v1 (17/08/2026)
La v1 usaba /api/snapshots y era una PRUEBA declarada: los tests 401-412
demostraron que ese endpoint devuelve siempre el ultimo valor conocido y no
varia con snapshotDate. La hipotesis que quedaba por probar era si capturando
cada dia en el momento la serie si variaba.

Esa via se abandona porque ya no hace falta: /api/trades SI devuelve las
operaciones individuales con su fecha real, y es la fuente con la que se
construyo todo el historico 2020-2026. Usar snapshots para el presente y
trades para el pasado dejaria una discontinuidad metodologica justo en el
punto donde el modelo predice — el peor sitio posible.

La tabla `trayport_daily` deja de alimentarse. No se elimina: queda como
registro de la prueba y de su resultado. La logica de la v1 sigue en el
historial de git si alguna vez hiciera falta.

COMO FUNCIONA
Tres pasos, todos idempotentes:

  1. DESCARGA una ventana movil de los ultimos VENTANA_DIAS dias a
     `trayport_trades`. El trade_id es unico e inmutable, asi que reprocesar
     los mismos dias nunca duplica (ON CONFLICT DO NOTHING). La ventana es lo
     que hace el pipeline AUTORREPARABLE: si falla un dia, al siguiente lo
     recupera solo. Es el mismo patron que ya evita huecos permanentes en las
     cargas de capacity.

  2. RECALCULA `trayport_daily_ohlc` desde las operaciones.

  3. RECONSTRUYE las series continuas y actualiza `commodities`.

REGLA DE EMPALME — la misma que el historico, importada, no reescrita
Las funciones vienen de historic_load/: `bajar_trades`, `limpiar`,
`guardar_trades`, `recalcular_ohlc` de trayport_history.py, y `construir` /
`escribir` de commodities_futuros_history.py.

Eso es deliberado. Si la regla del vencimiento mas proximo estuviera escrita
dos veces —una en el historico y otra aqui— acabarian divergiendo, y la serie
tendria un criterio distinto antes y despues de la fecha de despliegue. Un
salto de metodologia en medio de la serie es invisible en un EDA y fatal en un
modelo.

QUE CONTRATOS SE PIDEN
  EUA: los diciembres vivos (el del anio en curso y los siguientes). Hacen
       falta dos para que el relevo funcione: cuando el Dic-N vence a mediados
       de diciembre, la serie pasa al Dic-N+1 y ese ya debe estar descargado.
  TTF: los mensuales de la curva corta (M+1 en adelante). Con CURVA_TTF=3 hay
       margen de sobra para el relevo al M+2 cuando el M+1 vence.

FUENTE DEL PRECIO
Se usa `cierre` (ultimo precio negociado del dia), no settlement:
trayport_daily_ohlc no tiene columna de liquidacion oficial de camara.

CRON (servidor)
    CRON_TZ=Europe/Madrid
    0 8 * * * /home/ubuntu/tfm-env/bin/python /home/ubuntu/scripts/ingesta/trayport_daily_pipeline.py >> /home/ubuntu/scripts/logs/cron_trayport.log 2>&1

  A las 08:00 y no por la tarde: los mercados europeos de gas cierran hacia
  las 17-18h, asi que a las 08:00 la sesion de AYER esta completa y cerrada.
  Pedir el dia en curso daria una sesion a medias que habria que reescribir.

USO
    python trayport_daily_pipeline.py                 # ventana por defecto
    python trayport_daily_pipeline.py --dias 30       # ventana mas amplia
    python trayport_daily_pipeline.py --simular       # no escribe nada
    python trayport_daily_pipeline.py --commodity EUA
"""

import argparse
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import psycopg2

AQUI = Path(__file__).resolve().parent
sys.path.append(str(AQUI))
sys.path.append(str(AQUI / "historic_load"))

from config import load_config

# Logica compartida con el historico. NO se reimplementa aqui a proposito:
# ver "REGLA DE EMPALME" en el docstring.
import trayport_history as th
import commodities_futuros_history as cf

# ── Configuracion ─────────────────────────────────────────────────────────────

# Ventana movil. 10 dias cubre un puente largo mas margen: si el cron falla
# una semana, la siguiente ejecucion lo recupera sin intervencion.
VENTANA_DIAS = 10

# Vencimientos de la curva de TTF a descargar. 3 basta para el M+1 con relevo
# al M+2; mas contratos permiten medir el contango pero cuestan peticiones.
CURVA_TTF = 3

PRECIO_COL = "cierre"

LOGS_DIR = AQUI.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)


def fmt(s: float) -> str:
    return f"{int(s // 60)}m {int(s % 60):02d}s" if s >= 60 else f"{s:.1f}s"


def setup_logger() -> logging.Logger:
    log = logging.getLogger("trayport_daily")
    log.setLevel(logging.INFO)
    log.handlers = []
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(LOGS_DIR / f"trayport_daily_{date.today()}.log",
                             encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    log.addHandler(ch)
    return log


# ── Paso 1: descarga de operaciones ───────────────────────────────────────────

def descargar(conn, headers, desde: date, hasta: date, commodities, log):
    """
    Descarga las operaciones de la ventana. Devuelve (n_ops, n_errores).

    Un contrato sin operaciones NO es un error: los vencimientos lejanos
    apenas se negocian y los ya vencidos no se negocian en absoluto. Solo
    cuenta como error un fallo de la API.
    """
    total = errores = vacios = 0

    for com in commodities:
        cfg = th.EUA if com == "EUA" else th.TTF
        if com == "EUA":
            contratos = th.contratos_eua(desde, hasta)
        else:
            contratos = th.contratos_ttf(desde, hasta, CURVA_TTF)

        log.info(f"  {com}: {len(contratos)} contratos en la ventana")
        for item, periodo, ini, fin in contratos:
            trades, err = th.bajar_trades(cfg, item, ini, fin, headers)
            if trades is None:
                errores += 1
                log.warning(f"    {periodo} (item {item}): {err}")
                continue
            if not trades:
                # A info, no a debug: si no se ve, el recuento de contratos no
                # cuadra con las lineas listadas y el log parece incompleto.
                # Un vencimiento ya expirado o muy lejano no negocia: normal.
                vacios += 1
                log.info(f"    {periodo:<8} item {item:<4} {'sin operaciones':>15}")
                continue
            filas, malas = th.limpiar(trades, com, periodo, item)
            n = th.guardar_trades(conn, filas)
            total += n
            log.info(f"    {periodo:<8} item {item:<4} {n:>6} ops"
                     + (f"  ({malas} descartadas)" if malas else ""))

    return total, errores, vacios


# ── pipeline_log ──────────────────────────────────────────────────────────────

def registrar(conn, dia, registros, estado, mensaje, duracion, log):
    """
    Deja constancia en pipeline_log. Es lo que permite a un panel de salud
    distinguir "el dato llega hasta aqui" de "el cron esta muerto": sin esta
    fila, un MAX(fecha) antiguo puede significar festivo o pipeline caido, y
    no hay forma de saber cual.
    """
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO pipeline_log
                    (pipeline, fecha_inicio, fecha_fin, registros, estado,
                     mensaje, duracion_seg)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, (f"trayport_daily_{dia}", dia, dia, registros, estado,
                  mensaje, round(duracion, 2)))
        conn.commit()
    except Exception as e:
        log.warning(f"  pipeline_log no disponible: {e}")
        conn.rollback()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="Trayport diario -> commodities")
    p.add_argument("--dias", type=int, default=VENTANA_DIAS,
                   help=f"ventana movil en dias (default {VENTANA_DIAS})")
    p.add_argument("--commodity", choices=["EUA", "TTF", "ambas"],
                   default="ambas")
    p.add_argument("--simular", action="store_true",
                   help="descarga y recalcula, pero NO escribe en commodities")
    p.add_argument("--precio", choices=["cierre", "vwap"], default=PRECIO_COL)
    args = p.parse_args()

    log = setup_logger()
    t0 = time.time()

    # Hasta AYER: la sesion de hoy no ha cerrado y daria un cierre parcial
    # que habria que reescribir mañana.
    hasta = date.today() - timedelta(days=1)
    desde = hasta - timedelta(days=args.dias)
    coms = ["EUA", "TTF"] if args.commodity == "ambas" else [args.commodity]

    log.info("=" * 66)
    log.info(f"Trayport Daily v2 — ventana {desde} .. {hasta} ({args.dias}d)")
    log.info(f"Commodities: {', '.join(coms)}   precio: {args.precio}")
    log.info(f"Modo: {'SIMULACION' if args.simular else 'ESCRITURA'}")
    log.info("=" * 66)

    try:
        # credenciales() devuelve (headers, ruta_del_fichero)
        headers, ruta_creds = th.credenciales()
        log.info(f"Credenciales: {ruta_creds}")
    except Exception as e:
        log.error(f"Credenciales: {e}")
        sys.exit(1)

    _, db = load_config()
    conn = psycopg2.connect(**db)

    try:
        log.info("\nPASO 1 — descarga de operaciones")
        n_ops, n_err, n_vac = descargar(conn, headers, desde, hasta, coms, log)
        log.info(f"  {n_ops} operaciones nuevas | {n_vac} contratos sin "
                 f"operaciones (normal) | {n_err} errores de API")

        log.info("\nPASO 2 — recalculo del OHLC diario")
        for com in coms:
            n = th.recalcular_ohlc(conn, com)
            log.info(f"  {com}: {n} filas de OHLC")

        log.info("\nPASO 3 — series continuas en commodities")
        escritas = 0
        for com in coms:
            cfg = cf.SERIES[com]
            serie, rolls = cf.construir(conn, com, args.precio)
            if not serie:
                log.warning(f"  {com}: serie vacia, nada que escribir")
                continue
            # Solo la ventana: reescribir las ~1.700 filas de la serie cada
            # dia funciona, pero hace ilegible el pipeline_log y esconde si un
            # dia concreto entro. Para reescribir todo esta la carga historica.
            n, huerf = cf.escribir(conn, cfg["columna"], serie,
                                   ejecutar=not args.simular, desde=desde)
            escritas += n
            verbo = "se actualizarian" if args.simular else "actualizadas"
            log.info(f"  {com} ({cfg['descripcion']}): {len(serie)} dias, "
                     f"{serie[-1][0]} el ultimo | {n} filas {verbo}"
                     + (f" | {huerf} fechas sin fila en commodities"
                        if huerf else ""))
            # Un cambio de contrato en la ventana merece quedar en el log: es
            # el dia en que la serie salta de vencimiento y el unico momento
            # en que el salto no es movimiento de mercado.
            for f_a, _, p_a, f_b, _v, p_b, prima in rolls:
                if f_b >= desde:
                    pm = f"{prima:+.2f}" if prima is not None else "no medible"
                    log.info(f"    ROLL {f_b}: {p_a} -> {p_b} "
                             f"(prima {pm})")

        dur = time.time() - t0
        estado = "ok" if n_err == 0 else "parcial"
        registrar(conn, hasta, escritas, estado,
                  f"{n_ops} ops, {escritas} filas, {n_err} errores", dur, log)

        log.info("\n" + "=" * 66)
        if args.simular:
            log.info(f"SIMULACION: nada escrito en commodities ({fmt(dur)})")
        else:
            log.info(f"Completado en {fmt(dur)}: {escritas} filas en commodities")
        if n_err:
            log.warning(f"{n_err} contratos fallaron: la ventana movil los "
                        f"recuperara en la proxima ejecucion")

    finally:
        conn.close()


if __name__ == "__main__":
    main()