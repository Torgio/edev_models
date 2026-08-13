"""
TFM Energia UCM — Pipeline de actualizacion diaria automatica v7
Descarga de ESIOS el precio y las metricas de CO2, y las carga en PostgreSQL.

QUE CAMBIA EN v6 (13-ago-2026) — CORRECCION DE UN FALLO QUE PARO 13 DIAS
  El diccionario INDICATORS pedia 30 indicadores, pero once de esas columnas
  ya no existen en esios_marketdata: se movieron a esios_gen cuando se creo
  esa tabla. Como el upsert nombra todas las columnas en un unico INSERT, una
  sola columna inexistente tumbaba la sentencia entera — incluido el precio,
  que si tenia columna.
  Sintoma exacto en el log: "OK: 30 | Fallidos: 0" seguido de
  "Error upsert: column gen_solar_mw of relation esios_marketdata does not
  exist". La descarga funcionaba; lo que fallaba era el destino.
  Resultado: esios_marketdata se quedo en 2026-07-31 mientras esios_gen y
  esios_load_inter llegaban al 2026-08-13, y price_eur_mwh — la variable
  objetivo del TFM — estuvo trece dias sin cargarse.

  Se elimina ademas un error de mapeo: "gen_cogen_real_mw": (553, ...). El
  indicador 553 NO es cogeneracion, es el saldo total de interconexion
  (verificado al construir esios_load_inter, donde se llama ree_netflow_total).

QUE CARGA AHORA — 5 indicadores
  price_eur_mwh          600     Precio mercado diario. VARIABLE OBJETIVO.
  precio_banda_sec_mwh   634     Precio de la banda de regulacion secundaria.
  co2_real_t           10355     Emisiones de CO2.
  gen_libre_co2_mw     10006     Generacion libre de CO2.
  pct_gen_libre_co2    10033     Porcentaje de generacion libre de CO2.

  El resto de columnas de la tabla estan DUPLICADAS en otras tablas, que si
  se cargan al dia, y se eliminaran cuando se recree esios_marketdata:
    demanda_real_mw, saldo_total_int_mw, saldo_francia/portugal/marruecos_mw,
    las seis ntc_* y saldo_portugal_exp_mw  -> esios_load_inter
    demanda_prev_mw, gen_solar_prev_mw, gen_solar_term_prev_mw
                                            -> esios_forecast_da
  Verificado con 0 discrepancias sobre las 57.688 horas comunes, asi que
  dejar de escribirlas no pierde nada.

QUE CAMBIA EN v7 — LOS ESPORADICOS NUNCA SE REINTENTABAN
  Dos fallos hermanos, heredados de la version anterior, con la misma causa:
  una columna esporadica ausente no se volvia a pedir nunca.
    a) ejecutar_intento() salia con "nada que hacer" en cuanto los criticos
       estaban completos, sin mirar si los esporadicos seguian vacios.
    b) revisar_semana() descargaba solo nulls_recuperables e ignoraba los
       esporadicos, registrando "Solo esporadicos con nulls — OK".
  Sintoma: los dias 1-6 y 13 de agosto tenian CO2 (se cargaron con la tabla
  vacia, cuando el precio si estaba en nulls_recuperables) y los dias 7-12 no
  (llegaron por la revision semanal, que nunca pidio el CO2). Corte limpio.
  Un esporadico es justamente lo que necesita reintento: publicacion
  irregular que se resuelve en pasadas posteriores. Ahora se pide siempre, y
  no genera bucle porque no entra en criticos_ok ni en los pendientes.

DOS SALVAGUARDAS
  1. Verificacion previa contra information_schema. Si un indicador del
     diccionario no tiene columna en la tabla, el script aborta ANTES de
     descargar nada y dice exactamente cual. Este chequeo habria convertido
     trece dias de silencio en un error visible la primera noche.
  2. Un error estructural de SQL (columna inexistente, tipo incompatible) ya
     no entra en el bucle de reintentos. Esperar diez minutos no arregla un
     "column does not exist"; el bucle solo tiene sentido cuando ESIOS aun no
     ha publicado. Ahora aborta de inmediato con codigo de salida 2.

Cron job (servidor):
    0 19 * * * /home/ubuntu/tfm-env/bin/python \
      /home/ubuntu/scripts/ingesta/esios_daily_marketdata.py \
      >> /home/ubuntu/scripts/logs/cron.log 2>&1

Recuperar el hueco de agosto:
    python -u esios_daily_marketdata.py --fecha 2026-08-01
    ... o bien:
    python -u esios_daily_marketdata.py --dias 14
"""

import time
import logging
import sys
from datetime import date, timedelta, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from config import load_config

# ── Configuracion ──────────────────────────────────────────────────────────────

MAX_HORAS_REINTENTO = 12
PAUSA_REINTENTO_MIN = 10
PAUSA_INDICADOR_SEC = 0.5
DIAS_REVISION       = 7
TZ_SPAIN            = ZoneInfo("Europe/Madrid")
TABLA               = "esios_marketdata"

LOGS_DIR = Path(__file__).parent.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# Solo lo que esta tabla sigue siendo responsable de cargar. Ver docstring.
INDICATORS = {
    "price_eur_mwh":         (600,   3),
    "precio_banda_sec_mwh":  (634,   8741),
    "co2_real_t":            (10355, 8741),
    "gen_libre_co2_mw":      (10006, 8741),
    "pct_gen_libre_co2":     (10033, 8741),
}

# Si falta, el dia NO se da por completo y se reintenta.
CRITICOS = {"price_eur_mwh"}

# Null es valido: no significa dato faltante.
# Las metricas de CO2 no siempre se publican a la vez que el precio; si se
# marcaran como criticas, un retraso suyo bloquearia el dia entero aunque el
# precio — lo unico verdaderamente imprescindible — ya estuviera cargado.
ESPORADICOS = {"co2_real_t", "gen_libre_co2_mw", "pct_gen_libre_co2"}

ESIOS_BASE = "https://api.esios.ree.es"
ALL_COLS   = ["time_qh"] + list(INDICATORS.keys())


class ErrorEstructural(Exception):
    """
    Fallo que NO se arregla esperando: columna inexistente, tipo incompatible,
    tabla ausente. Distinguirlo de un retraso de publicacion es la diferencia
    entre un error visible esta noche y trece dias de silencio.
    """


# ── Logger ─────────────────────────────────────────────────────────────────────

def setup_logger(target_date: date) -> logging.Logger:
    log_file = LOGS_DIR / f"pipeline_{target_date}.log"
    logger = logging.getLogger(f"pipeline_{target_date}")
    logger.setLevel(logging.DEBUG)
    logger.handlers = []
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    return logger


# ── Salvaguarda 1: el diccionario contra la tabla real ─────────────────────────

def verificar_esquema(db_config: dict, log) -> None:
    """
    Comprueba que cada indicador tiene columna en la tabla ANTES de descargar.
    Es la comprobacion que faltaba: el desfase entre INDICATORS y el esquema
    real es lo que dejo la tabla trece dias sin actualizar.
    """
    conn = psycopg2.connect(**db_config)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = %s
            """, (TABLA,))
            reales = {r[0] for r in cur.fetchall()}
    finally:
        conn.close()

    if not reales:
        raise ErrorEstructural(f"La tabla {TABLA} no existe")

    faltan = [c for c in ALL_COLS if c not in reales]
    if faltan:
        raise ErrorEstructural(
            f"Estas columnas estan en INDICATORS pero no en {TABLA}: {faltan}. "
            f"Corrige el diccionario antes de ejecutar.")

    huerfanas = sorted(reales - set(ALL_COLS) - {"updated_at"})
    if huerfanas:
        log.info(f"  Columnas de la tabla que este pipeline NO carga "
                 f"(estan en otras tablas): {huerfanas}")
    log.info(f"  Esquema verificado: {len(INDICATORS)} indicadores, "
             f"{len(reales)} columnas en {TABLA}")


# ── Helpers UTC/hora española ──────────────────────────────────────────────────

def expected_hours_utc(target: date) -> set:
    """
    Timestamps UTC esperados para el dia target en hora española.
    Soporta 23h (cambio a verano), 24h y 25h (cambio a invierno).
    """
    start_spain = datetime(target.year, target.month, target.day, 0, tzinfo=TZ_SPAIN)
    end_spain   = datetime(target.year, target.month, target.day, 23, tzinfo=TZ_SPAIN)
    hours, current = set(), start_spain.astimezone(timezone.utc)
    end_utc = end_spain.astimezone(timezone.utc)
    while current <= end_utc:
        hours.add(current)
        current += timedelta(hours=1)
    return hours


def day_range_utc(target: date) -> tuple[datetime, datetime]:
    start_spain = datetime(target.year, target.month, target.day, 0, tzinfo=TZ_SPAIN)
    end_spain   = datetime(target.year, target.month, target.day, 23, tzinfo=TZ_SPAIN)
    return start_spain.astimezone(timezone.utc), end_spain.astimezone(timezone.utc)


# ── BD helpers ─────────────────────────────────────────────────────────────────

def get_cols_with_nulls(conn, target: date) -> tuple[dict, dict]:
    """
    Devuelve (nulls_recuperables, nulls_esporadicos), solo columnas con nulls.
    Una sola consulta en lugar de una por columna.
    """
    start_utc, end_utc = day_range_utc(target)
    cols = list(INDICATORS)
    conteos = ", ".join(f"count({c})" for c in cols)

    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*), {conteos} FROM {TABLA} "
                    f"WHERE time_qh >= %s AND time_qh <= %s", (start_utc, end_utc))
        fila = cur.fetchone()

    total = fila[0]
    if total == 0:
        # Dia vacio: hay que pedir TODO, tambien los esporadicos. Si solo se
        # pidieran los criticos, los demas no se descargarian nunca.
        return ({c: 999 for c in cols if c not in ESPORADICOS},
                {c: 999 for c in ESPORADICOS})

    rec, esp = {}, {}
    for i, c in enumerate(cols):
        n = total - fila[i + 1]
        if n > 0:
            (esp if c in ESPORADICOS else rec)[c] = n
    return rec, esp


def get_day_status(conn, target: date, log) -> dict:
    expected   = expected_hours_utc(target)
    n_expected = len(expected)
    start_utc, end_utc = day_range_utc(target)
    marca = ""
    if n_expected == 23:
        marca = "  (cambio de hora: 23h)"
    elif n_expected == 25:
        marca = "  (cambio de hora: 25h)"

    cols_cri = sorted(CRITICOS)
    conteos = ", ".join(f"count({c})" for c in cols_cri)
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*), {conteos} FROM {TABLA} "
                    f"WHERE time_qh >= %s AND time_qh <= %s", (start_utc, end_utc))
        fila = cur.fetchone()

    total = fila[0]
    criticos_ok = True
    for i, col in enumerate(cols_cri):
        n = fila[i + 1]
        if n < n_expected:
            criticos_ok = False
            log.warning(f"    [CRITICO] {col}: {n}/{n_expected}h")

    rec, esp = get_cols_with_nulls(conn, target)
    es_completo = total >= n_expected and criticos_ok
    pct = total / n_expected * 100 if n_expected else 0

    log.info(f"  [{target}] {total}/{n_expected}h{marca} | {pct:.0f}% | "
             f"criticos={'OK' if criticos_ok else 'KO'} | "
             f"{'COMPLETO' if es_completo else 'incompleto'}")
    if rec:
        log.warning(f"    [nulls recuperables] {sorted(rec)}")
    if esp:
        log.debug(f"    [nulls esporadicos — OK] {sorted(esp)}")

    return {"total": total, "n_expected": n_expected, "criticos_ok": criticos_ok,
            "es_completo": es_completo, "nulls_recuperables": rec,
            "nulls_esporadicos": esp, "pct": pct}


# ── API ESIOS ──────────────────────────────────────────────────────────────────

def fetch_indicator(headers, indicator_id, geo_id, target: date, log):
    """
    Descarga un indicador para el dia target en hora española.
    Pide de target-1 a target+1 y filtra: las 00:00 españolas son las 22:00 UTC
    del dia anterior en verano, y sin margen esas horas frontera se perdian.

    Los cinco indicadores que quedan son nativos de 5 minutos y necesitan
    time_agg=average explicito. Sin el, ESIOS SUMA por defecto y los infla ×12
    (bug confirmado el 27 de julio de 2026).
    El precio (600) es la excepcion: mantiene su logica ya validada de dividir
    entre 4 desde el 01-oct-2025, sin time_agg.
    """
    start_req = target - timedelta(days=1)
    end_req   = target + timedelta(days=1)
    params = {"start_date": f"{start_req}T00:00:00",
              "end_date":   f"{end_req}T23:59:59",
              "time_trunc": "hour"}
    if indicator_id != 600:
        params["time_agg"] = "average"
    if geo_id is not None:
        params["geo_ids[]"] = geo_id

    try:
        resp = requests.get(f"{ESIOS_BASE}/indicators/{indicator_id}",
                            headers=headers, params=params, timeout=60)
        resp.raise_for_status()
        values = resp.json().get("indicator", {}).get("values", [])
        if not values:
            return None

        df = pd.DataFrame(values)[["datetime_utc", "value"]]
        df["datetime_utc"] = pd.to_datetime(df["datetime_utc"], utc=True)
        df = df.set_index("datetime_utc")["value"]
        df = df[~df.index.duplicated(keep="first")]
        df = df[df.index.isin(expected_hours_utc(target))]

        if indicator_id == 600 and target >= date(2025, 10, 1):
            df = (df / 4).round(2)
        else:
            df = df.round(2)
        return df if not df.empty else None

    except Exception as e:
        log.warning(f"    Indicador {indicator_id} error: {e}")
        return None


def fetch_indicators_selective(headers, target: date, cols_needed: set, log):
    if not cols_needed:
        return None, set()

    log.info(f"  Descargando {len(cols_needed)} indicadores para {target}: "
             f"{sorted(cols_needed)}")
    frames, descargadas, fallidos = {}, set(), []
    for col in sorted(cols_needed):
        if col not in INDICATORS:
            continue
        ind_id, geo_id = INDICATORS[col]
        serie = fetch_indicator(headers, ind_id, geo_id, target, log)
        if serie is not None:
            frames[col] = serie
            descargadas.add(col)
        else:
            fallidos.append(col)
        time.sleep(PAUSA_INDICADOR_SEC)

    log.info(f"  OK: {len(descargadas)} | Fallidos: {len(fallidos)}")
    if fallidos:
        cri = [c for c in fallidos if c in CRITICOS]
        if cri:
            log.warning(f"  [CRITICOS fallidos]: {cri}")
        noc = [c for c in fallidos if c not in CRITICOS]
        if noc:
            log.warning(f"  [no criticos fallidos]: {noc}")

    if not frames:
        return None, descargadas
    df = pd.DataFrame(frames)
    df.index.name = "time_qh"
    return df.reset_index(), descargadas


# ── INSERT + UPDATE ────────────────────────────────────────────────────────────

def upsert_day(conn, df: pd.DataFrame, target: date, log) -> tuple[int, int]:
    """
    Inserta horas nuevas y rellena nulls en las existentes.
    Un psycopg2.errors.UndefinedColumn se reetiqueta como ErrorEstructural
    para que no acabe en el bucle de reintentos.
    """
    data_cols = list(INDICATORS)
    ins = upd = 0
    start_utc, end_utc = day_range_utc(target)

    with conn.cursor() as cur:
        cur.execute(f"SELECT time_qh FROM {TABLA} "
                    f"WHERE time_qh >= %s AND time_qh <= %s", (start_utc, end_utc))
        existing = {r[0] for r in cur.fetchall()}

    try:
        df_new = df[~df["time_qh"].isin(existing)]
        if not df_new.empty:
            cols = [c for c in ALL_COLS if c in df_new.columns]
            records = [tuple(None if pd.isna(row.get(c)) else row.get(c) for c in cols)
                       for _, row in df_new.iterrows()]
            sql = (f"INSERT INTO {TABLA} ({', '.join(cols)}) VALUES %s "
                   f"ON CONFLICT (time_qh) DO NOTHING")
            with conn.cursor() as cur:
                execute_values(cur, sql, records, page_size=500)
            conn.commit()
            ins = len(records)
            log.info(f"  INSERT: {ins} filas nuevas")

        df_exist = df[df["time_qh"].isin(existing)]
        if not df_exist.empty:
            cols_list = [c for c in data_cols if c in df_exist.columns]
            cols_str = ", ".join(cols_list)
            with conn.cursor() as cur:
                for _, row in df_exist.iterrows():
                    ts = row["time_qh"]
                    cur.execute(f"SELECT {cols_str} FROM {TABLA} WHERE time_qh = %s", (ts,))
                    db_row = cur.fetchone()
                    if not db_row:
                        continue
                    to_update = {c: row[c] for i, c in enumerate(cols_list)
                                 if db_row[i] is None and not pd.isna(row.get(c))}
                    if to_update:
                        set_clause = ", ".join(f"{c} = %s" for c in to_update)
                        cur.execute(f"UPDATE {TABLA} SET {set_clause} WHERE time_qh = %s",
                                    list(to_update.values()) + [ts])
                        upd += 1
            conn.commit()
            if upd:
                log.info(f"  UPDATE: {upd} filas con nulls rellenados")

    except psycopg2.errors.UndefinedColumn as e:
        conn.rollback()
        raise ErrorEstructural(f"Columna inexistente en {TABLA}: {e}") from e
    except psycopg2.errors.UndefinedTable as e:
        conn.rollback()
        raise ErrorEstructural(f"Tabla inexistente: {e}") from e

    return ins, upd


def log_pipeline_db(conn, target, intento, ins, upd, status, mensaje, duracion, log):
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO pipeline_log
                    (pipeline, fecha_inicio, fecha_fin, registros, estado, mensaje, duracion_seg)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, (f"esios_diario_{target}_intento_{intento}", target, target,
                  ins + upd, status, mensaje, round(duracion, 2)))
        conn.commit()
    except Exception as e:
        log.warning(f"  pipeline_log error: {e}")
        conn.rollback()


# ── Logica principal ───────────────────────────────────────────────────────────

def ejecutar_intento(target: date, intento: int, headers: dict, db_config: dict, log) -> bool:
    t0 = time.time()
    log.info("=" * 60)
    log.info(f"INTENTO {intento} — {target} — {datetime.now().strftime('%H:%M:%S')}")
    log.info("=" * 60)

    try:
        conn = psycopg2.connect(**db_config)
    except Exception as e:
        log.error(f"  Error conexion BD: {e}")
        return False

    try:
        status = get_day_status(conn, target, log)
        nulls_antes = set(status["nulls_recuperables"])

        # Los esporadicos TAMBIEN cuentan para decidir si hay algo que pedir.
        # Si solo se mirasen los recuperables, un esporadico ausente no se
        # reintentaria JAMAS: en cuanto el precio esta cargado, el dia se daba
        # por bueno y las tres columnas de CO2 se quedaban vacias para siempre.
        # No genera bucle: los esporadicos no entran en criticos_ok ni en
        # nulls_recuperables_pendientes, asi que el dia se cierra igual aunque
        # sigan vacios tras pedirlos.
        if (status["es_completo"] and not status["nulls_recuperables"]
                and not status["nulls_esporadicos"]):
            log.info(f"  Dia {target} completo — nada que hacer")
            return True

        cols_needed = nulls_antes | set(status["nulls_esporadicos"])
        df, descargadas = fetch_indicators_selective(headers, target, cols_needed, log)

        ins = upd = 0
        if df is not None and not df.empty:
            ins, upd = upsert_day(conn, df, target, log)

        status_fin = get_day_status(conn, target, log)
        nulls_despues = set(status_fin["nulls_recuperables"])
        n_exp = status_fin["n_expected"]

        estado = ("ok" if status_fin["es_completo"]
                  else "parcial" if status_fin["criticos_ok"] else "incompleto")
        log_pipeline_db(conn, target, intento, ins, upd, estado,
                        f"Intento {intento}: {ins} ins, {upd} upd, "
                        f"{status_fin['total']}/{n_exp}h, "
                        f"criticos={'ok' if status_fin['criticos_ok'] else 'KO'}",
                        time.time() - t0, log)

        if not status_fin["criticos_ok"]:
            log.warning(f"  Criticos incompletos — reintentando en "
                        f"{PAUSA_REINTENTO_MIN} min")
            return False

        sin_datos_api = (nulls_antes & nulls_despues) - descargadas
        pendientes = nulls_despues - sin_datos_api
        if pendientes:
            log.warning(f"  Nulls recuperables pendientes: {sorted(pendientes)} "
                        f"— reintentando")
            return False

        if sin_datos_api:
            log.warning(f"  La API no publica datos para: {sorted(sin_datos_api)}")
        log.info(f"  Dia {target} completo — criticos OK")
        return True

    finally:
        conn.close()


def revisar_semana(headers: dict, db_config: dict, log):
    hoy = date.today()
    log.info(f"\n--- Revision de los ultimos {DIAS_REVISION} dias ---")
    try:
        conn = psycopg2.connect(**db_config)
    except Exception as e:
        log.error(f"  Error BD: {e}")
        return

    try:
        for i in range(2, DIAS_REVISION + 1):
            dia = hoy - timedelta(days=i)
            rec, esp = get_cols_with_nulls(conn, dia)
            if not rec and not esp:
                log.info(f"  [{dia}] Completo")
                continue

            # Se piden recuperables Y esporadicos. Un esporadico es justamente
            # lo que necesita reintento en pasadas posteriores: publicacion
            # irregular que se resuelve dias despues. Pedir solo los
            # recuperables dejaba el CO2 vacio de forma permanente.
            log.info(f"  [{dia}] Nulls: recuperables={sorted(rec)} "
                     f"esporadicos={sorted(esp)} — descargando")
            df, _ = fetch_indicators_selective(headers, dia, set(rec) | set(esp), log)
            if df is not None and not df.empty:
                ins, upd = upsert_day(conn, df, dia, log)
                log.info(f"  [{dia}] {ins} insert, {upd} update"
                         if ins + upd else
                         f"  [{dia}] Sin cambios — la API no devuelve esos indicadores")
    finally:
        conn.close()
    log.info("--- Revision completada ---\n")


def run(target: date) -> int:
    log = setup_logger(target)
    log.info(f"Pipeline ESIOS marketdata v7 — {target}")
    log.info(f"  Indicadores : {len(INDICATORS)}  |  Criticos: {sorted(CRITICOS)}")

    try:
        headers, db_config = load_config()
    except Exception as e:
        log.error(f"Error cargando credenciales: {e}")
        return 1

    # Salvaguarda: el esquema antes que nada
    try:
        verificar_esquema(db_config, log)
    except ErrorEstructural as e:
        log.error(f"ESQUEMA INCOHERENTE — abortando sin descargar nada")
        log.error(f"  {e}")
        return 2
    except Exception as e:
        log.error(f"No se pudo verificar el esquema: {e}")
        return 1

    max_intentos = (MAX_HORAS_REINTENTO * 60) // PAUSA_REINTENTO_MIN
    intento = 1
    while intento <= max_intentos:
        try:
            if ejecutar_intento(target, intento, headers, db_config, log):
                log.info(f"Pipeline finalizado con exito tras {intento} intento(s)")
                break
        except ErrorEstructural as e:
            # No se arregla esperando: abortar en lugar de reintentar 72 veces
            log.error(f"ERROR ESTRUCTURAL — abortando sin reintentar")
            log.error(f"  {e}")
            return 2
        if intento >= max_intentos:
            log.warning("Max intentos alcanzado — quedan indicadores incompletos")
            break
        log.info(f"Esperando {PAUSA_REINTENTO_MIN} minutos...")
        time.sleep(PAUSA_REINTENTO_MIN * 60)
        intento += 1

    try:
        revisar_semana(headers, db_config, log)
    except ErrorEstructural as e:
        log.error(f"ERROR ESTRUCTURAL en la revision semanal: {e}")
        return 2

    log.info("Pipeline ESIOS marketdata finalizado")
    return 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Pipeline diario ESIOS marketdata v7")
    parser.add_argument("--fecha", help="Fecha concreta YYYY-MM-DD (por defecto: ayer)")
    parser.add_argument("--dias", type=int, default=1,
                        help="Numero de dias hacia atras desde ayer")
    args = parser.parse_args()

    if args.fecha:
        fechas = [date.fromisoformat(args.fecha)]
    else:
        fechas = [date.today() - timedelta(days=i) for i in range(1, args.dias + 1)]

    rc = 0
    for f in sorted(fechas):
        r = run(f)
        if r == 2:
            # Estructural: no tiene sentido seguir con los demas dias
            sys.exit(2)
        rc = max(rc, r)
    sys.exit(rc)
