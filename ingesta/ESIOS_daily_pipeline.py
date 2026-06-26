"""
TFM Energia UCM — Pipeline de actualizacion diaria automatica v4
Descarga datos ESIOS del dia anterior y los carga en PostgreSQL.

Logica de completitud en dos niveles:
  - CRITICOS: price_eur_mwh, demanda_real_mw — si faltan, reintentar cada 10 min hasta 12h
  - NO CRITICOS: resto de indicadores — intentar siempre, si fallan marcar como parcial y continuar

Mejoras v4:
  - Correccion desfase UTC/hora española (ZoneInfo Europe/Madrid)
  - Peticion target-1 hasta target+1 en UTC para capturar todas las horas
  - Soporte dias de 23h (cambio a verano) y 25h (cambio a invierno)
  - Revision ultimos 7 dias para rellenar huecos
  - Indicadores fallidos no bloquean el pipeline

Cron job (servidor):
    0 19 * * * /home/ubuntu/tfm-env/bin/python /home/ubuntu/scripts/ingesta/ESIOS_daily_pipeline.py >> /home/ubuntu/scripts/logs/cron.log 2>&1
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

LOGS_DIR = Path(__file__).parent.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

INDICATORS = {
    "price_eur_mwh":         (600,   None),
    "demanda_real_mw":       (1293,  8741),
    "demanda_prev_mw":       (544,   8741),
    "gen_solar_mw":          (1295,  8741),
    "gen_solar_term_real_mw":(1294,  8741),
    "gen_wind_mw":           (551,   8741),
    "gen_hidro_real_mw":     (546,   8741),
    "gen_nuclear_real_mw":   (549,   8741),
    "gen_ciclocomb_real_mw": (550,   8741),
    "gen_coal_real_mw":      (547,   8741),
    "gen_cogen_real_mw":     (553,   8741),
    "resto_gen_real_mw":     (1297,  8741),
    "saldo_francia_mw":      (10045, None),
    "saldo_portugal_mw":     (557,   8741),
    "saldo_portugal_exp_mw": (561,   8741),
    "saldo_marruecos_mw":    (10046, None),
    "gen_solar_prev_mw":     (542,   8741),
    "gen_solar_term_prev_mw":(543,   8741),
    "precio_banda_sec_mwh":  (634,   8741),
    "gen_bombeo_turb_mw":    (1152,  None),
    "cons_bombeo_mw":        (1172,  None),
    "ntc_francia_imp_mw":    (488,   8741),
    "ntc_francia_exp_mw":    (492,   8741),
    "ntc_portugal_imp_mw":   (489,   8741),
    "ntc_portugal_exp_mw":   (493,   8741),
    "ntc_marruecos_imp_mw":  (490,   8741),
    "ntc_marruecos_exp_mw":  (494,   8741),
    "co2_real_t":            (10355, 8741),
    "gen_libre_co2_mw":      (10006, 8741),
    "pct_gen_libre_co2":     (10033, 8741),
}

# Indicadores criticos — si faltan, el dia NO se considera completo y se reintenta
CRITICOS = {"price_eur_mwh", "demanda_real_mw"}

ESIOS_BASE = "https://api.esios.ree.es"
ALL_COLS   = ["time_qh"] + list(INDICATORS.keys())

# ── Logger ─────────────────────────────────────────────────────────────────────

def setup_logger(target_date: date) -> logging.Logger:
    log_file = LOGS_DIR / f"pipeline_{target_date}.log"
    logger = logging.getLogger(f"pipeline_{target_date}")
    logger.setLevel(logging.DEBUG)
    logger.handlers = []
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    return logger

# ── Helpers UTC/hora española ──────────────────────────────────────────────────

def expected_hours_utc(target: date) -> set:
    """
    Timestamps UTC esperados para el dia target en hora española.
    Soporta 23h (cambio a verano), 24h y 25h (cambio a invierno).
    """
    start_spain = datetime(target.year, target.month, target.day, 0, 0, 0, tzinfo=TZ_SPAIN)
    end_spain   = datetime(target.year, target.month, target.day, 23, 0, 0, tzinfo=TZ_SPAIN)
    start_utc   = start_spain.astimezone(timezone.utc)
    end_utc     = end_spain.astimezone(timezone.utc)
    hours = set()
    current = start_utc
    while current <= end_utc:
        hours.add(current)
        current += timedelta(hours=1)
    return hours


def day_range_utc(target: date) -> tuple[datetime, datetime]:
    start_spain = datetime(target.year, target.month, target.day, 0, 0, 0, tzinfo=TZ_SPAIN)
    end_spain   = datetime(target.year, target.month, target.day, 23, 0, 0, tzinfo=TZ_SPAIN)
    return start_spain.astimezone(timezone.utc), end_spain.astimezone(timezone.utc)

# ── BD helpers ─────────────────────────────────────────────────────────────────

def get_day_status(conn, target: date, log) -> dict:
    """
    Analiza el estado del dia en BD.
    Retorna si los indicadores CRITICOS estan completos (determina si reintentar)
    y el estado de todos los indicadores (para log).
    """
    expected   = expected_hours_utc(target)
    n_expected = len(expected)
    start_utc, end_utc = day_range_utc(target)

    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) FROM marketdata_qh
            WHERE time_qh >= %s AND time_qh <= %s
        """, (start_utc, end_utc))
        total = cur.fetchone()[0]

        # Estado de indicadores criticos
        criticos_ok = True
        for col in CRITICOS:
            cur.execute(f"""
                SELECT COUNT(*) FROM marketdata_qh
                WHERE time_qh >= %s AND time_qh <= %s AND {col} IS NOT NULL
            """, (start_utc, end_utc))
            n = cur.fetchone()[0]
            if n < n_expected:
                criticos_ok = False
                log.warning(f"    [CRITICO] {col}: {n}/{n_expected}h")

        # Estado de indicadores no criticos (solo para log)
        no_criticos_nulls = {}
        for col in INDICATORS:
            if col not in CRITICOS:
                cur.execute(f"""
                    SELECT COUNT(*) FROM marketdata_qh
                    WHERE time_qh >= %s AND time_qh <= %s AND {col} IS NULL
                """, (start_utc, end_utc))
                nulls = cur.fetchone()[0]
                if nulls > 0:
                    no_criticos_nulls[col] = nulls

    es_completo = total >= n_expected and criticos_ok
    pct = total / n_expected * 100

    log.info(f"  [{target}] {total}/{n_expected}h | {pct:.0f}% | "
             f"criticos={'✅' if criticos_ok else '❌'} | "
             f"{'✅ COMPLETO' if es_completo else '⚠️ incompleto'}")

    if no_criticos_nulls:
        for col, nulls in no_criticos_nulls.items():
            log.debug(f"    [parcial] {col}: {nulls} nulls")

    return {
        "total": total,
        "n_expected": n_expected,
        "criticos_ok": criticos_ok,
        "es_completo": es_completo,
        "no_criticos_nulls": no_criticos_nulls,
        "pct": pct,
    }

# ── API ESIOS ──────────────────────────────────────────────────────────────────

def fetch_indicator(headers, indicator_id, geo_id, target: date, log) -> pd.Series | None:
    """
    Descarga un indicador ESIOS para el dia target en hora española.
    Pide target-1 hasta target+1 en UTC y filtra por hora española.
    """
    start_req = target - timedelta(days=1)
    end_req   = target + timedelta(days=1)

    url    = f"{ESIOS_BASE}/indicators/{indicator_id}"
    params = {
        "start_date": f"{start_req}T00:00:00",
        "end_date":   f"{end_req}T23:59:59",
        "time_trunc": "hour",
    }
    if geo_id is not None:
        params["geo_ids[]"] = geo_id

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=60)
        resp.raise_for_status()
        values = resp.json().get("indicator", {}).get("values", [])
        if not values:
            return None

        df = pd.DataFrame(values)[["datetime_utc", "value"]]
        df["datetime_utc"] = pd.to_datetime(df["datetime_utc"], utc=True)
        df = df.set_index("datetime_utc")["value"]
        df = df[~df.index.duplicated(keep="first")]

        # Filtrar solo timestamps del dia target en hora española
        expected = expected_hours_utc(target)
        df = df[df.index.isin(expected)]

        return df if not df.empty else None

    except Exception as e:
        log.warning(f"    Indicador {indicator_id} error: {e}")
        return None


def fetch_day(headers, target: date, log) -> pd.DataFrame | None:
    log.info(f"  Descargando {len(INDICATORS)} indicadores para {target}...")
    frames = {}
    ok = fail = 0
    criticos_fail = []

    for col, (ind_id, geo_id) in INDICATORS.items():
        serie = fetch_indicator(headers, ind_id, geo_id, target, log)
        if serie is not None:
            frames[col] = serie
            ok += 1
        else:
            fail += 1
            if col in CRITICOS:
                criticos_fail.append(col)
        time.sleep(PAUSA_INDICADOR_SEC)

    log.info(f"  OK: {ok} | Fallidos: {fail} | Criticos fallidos: {criticos_fail or 'ninguno'}")

    if not frames:
        return None

    df = pd.DataFrame(frames)
    df.index.name = "time_qh"
    return df.reset_index()

# ── INSERT + UPDATE ────────────────────────────────────────────────────────────

def upsert_day(conn, df: pd.DataFrame, target: date, log) -> tuple[int, int]:
    data_cols = list(INDICATORS.keys())
    ins, upd  = 0, 0
    start_utc, end_utc = day_range_utc(target)

    with conn.cursor() as cur:
        cur.execute("""
            SELECT time_qh FROM marketdata_qh
            WHERE time_qh >= %s AND time_qh <= %s
        """, (start_utc, end_utc))
        existing = {row[0] for row in cur.fetchall()}

    # INSERT horas nuevas
    df_new = df[~df["time_qh"].isin(existing)]
    if not df_new.empty:
        cols = [c for c in ALL_COLS if c in df_new.columns]
        records = [
            tuple(None if pd.isna(row.get(c)) else row.get(c) for c in cols)
            for _, row in df_new.iterrows()
        ]
        sql = f"INSERT INTO marketdata_qh ({', '.join(cols)}) VALUES %s ON CONFLICT (time_qh) DO NOTHING"
        with conn.cursor() as cur:
            execute_values(cur, sql, records, page_size=500)
        conn.commit()
        ins = len(records)
        log.info(f"  INSERT: {ins} filas nuevas")

    # UPDATE nulls en filas existentes
    df_exist = df[df["time_qh"].isin(existing)]
    if not df_exist.empty:
        cols_str = ", ".join([c for c in data_cols if c in df_exist.columns])
        with conn.cursor() as cur:
            for _, row in df_exist.iterrows():
                ts = row["time_qh"]
                cur.execute(f"SELECT {cols_str} FROM marketdata_qh WHERE time_qh = %s", (ts,))
                db_row = cur.fetchone()
                if not db_row:
                    continue
                cols_list = [c for c in data_cols if c in row.index]
                to_update = {col: row[col] for i, col in enumerate(cols_list)
                            if db_row[i] is None and not pd.isna(row.get(col))}
                if to_update:
                    set_clause = ", ".join([f"{c} = %s" for c in to_update])
                    cur.execute(f"UPDATE marketdata_qh SET {set_clause} WHERE time_qh = %s",
                               list(to_update.values()) + [ts])
                    upd += 1
        conn.commit()
        if upd > 0:
            log.info(f"  UPDATE: {upd} filas con nulls rellenados")

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
    log.info(f"{'='*60}")
    log.info(f"INTENTO {intento} — {target} — {datetime.now().strftime('%H:%M:%S')}")
    log.info(f"{'='*60}")

    try:
        conn = psycopg2.connect(**db_config)
    except Exception as e:
        log.error(f"  Error conexion BD: {e}")
        return False

    status_ini = get_day_status(conn, target, log)
    if status_ini["es_completo"]:
        log.info(f"  Dia {target} ya completo — nada que hacer")
        conn.close()
        return True

    df = fetch_day(headers, target, log)
    ins, upd = 0, 0
    if df is not None and not df.empty:
        try:
            ins, upd = upsert_day(conn, df, target, log)
        except Exception as e:
            log.error(f"  Error upsert: {e}")
            conn.rollback()

    status_fin  = get_day_status(conn, target, log)
    duracion    = time.time() - t0
    es_completo = status_fin["es_completo"]
    n_exp       = status_fin["n_expected"]

    # Indicadores no criticos con nulls — informativo pero no bloquea
    if status_fin["no_criticos_nulls"]:
        log.info(f"  Indicadores no criticos con nulls: {list(status_fin['no_criticos_nulls'].keys())}")

    estado_str = "ok" if es_completo else ("parcial" if status_fin["criticos_ok"] else "incompleto")
    mensaje = (f"Intento {intento}: {ins} insertadas, {upd} actualizadas, "
              f"{status_fin['total']}/{n_exp}h, criticos={'ok' if status_fin['criticos_ok'] else 'KO'}")

    log_pipeline_db(conn, target, intento, ins, upd, estado_str, mensaje, duracion, log)
    conn.close()

    if es_completo:
        log.info(f"  ✅ Dia {target} COMPLETO tras intento {intento}")
        return True
    elif status_fin["criticos_ok"] and not status_fin["no_criticos_nulls"]:
        log.info(f"  ✅ Dia {target} completo (indicadores criticos OK, sin nulls pendientes)")
        return True
    elif status_fin["criticos_ok"]:
        # Criticos OK pero hay no-criticos con nulls — reintentar para intentar rellenarlos
        log.warning(f"  ⚠️ Criticos OK pero {len(status_fin['no_criticos_nulls'])} indicadores no criticos incompletos — reintentando")
        return False
    else:
        log.warning(f"  ❌ Indicadores criticos incompletos — reintentando en {PAUSA_REINTENTO_MIN} min")
        return False


def revisar_semana(headers: dict, db_config: dict, log):
    hoy = date.today()
    log.info(f"\n--- Revision ultimos {DIAS_REVISION} dias ---")

    try:
        conn = psycopg2.connect(**db_config)
    except Exception as e:
        log.error(f"  Error BD: {e}")
        return

    for i in range(2, DIAS_REVISION + 1):
        dia = hoy - timedelta(days=i)
        status = get_day_status(conn, dia, log)
        if status["es_completo"] and not status["no_criticos_nulls"]:
            continue
        log.info(f"  Rellenando {dia}...")
        df = fetch_day(headers, dia, log)
        if df is not None and not df.empty:
            try:
                ins, upd = upsert_day(conn, df, dia, log)
                log.info(f"  {dia}: {ins} insert, {upd} update")
            except Exception as e:
                log.error(f"  Error {dia}: {e}")
                conn.rollback()

    conn.close()
    log.info("--- Revision semanal completada ---\n")


def run(target: date):
    log = setup_logger(target)
    log.info(f"Pipeline ESIOS diario v4 — {target}")
    log.info(f"UTC/hora española | 23/24/25h | Criticos: {CRITICOS} | Revision {DIAS_REVISION}d")

    try:
        headers, db_config = load_config()
    except Exception as e:
        log.error(f"Error cargando credenciales: {e}")
        return

    max_intentos = (MAX_HORAS_REINTENTO * 60) // PAUSA_REINTENTO_MIN
    intento = 1

    while intento <= max_intentos:
        completo = ejecutar_intento(target, intento, headers, db_config, log)
        if completo:
            log.info(f"Pipeline finalizado con exito tras {intento} intento(s)")
            break
        if intento >= max_intentos:
            log.warning(f"Max intentos alcanzado — algunos indicadores pueden estar incompletos")
            break
        log.info(f"Esperando {PAUSA_REINTENTO_MIN} minutos...")
        time.sleep(PAUSA_REINTENTO_MIN * 60)
        intento += 1

    revisar_semana(headers, db_config, log)
    log.info("Pipeline ESIOS diario finalizado")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Pipeline diario ESIOS v4")
    parser.add_argument("--fecha", help="Fecha concreta YYYY-MM-DD (default: ayer)")
    parser.add_argument("--dias",  type=int, default=1, help="Numero de dias hacia atras")
    args = parser.parse_args()

    if args.fecha:
        fechas = [date.fromisoformat(args.fecha)]
    else:
        fechas = [date.today() - timedelta(days=i) for i in range(1, args.dias + 1)]

    for f in fechas:
        run(f)
