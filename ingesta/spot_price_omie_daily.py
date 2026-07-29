"""
TFM Energia UCM — Carga diaria de precio OMIE en spot_price
1) Backfill: ultimos 7 dias, una sola llamada por dia si falta.
2) Objetivo principal: precio de MANANA (D+1). Si ya esta completo, se
   omite sin llamar a la API. Si no, reintenta cada 15 min hasta 3h.

Cron job (servidor, hora Madrid via CRON_TZ):
    CRON_TZ=Europe/Madrid
    45 13 * * * .../spot_price_omie_daily.py >> .../cron_spot_omie.log 2>&1
"""

import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
import unicodedata

sys.path.append(str(Path(__file__).parent.parent))
from config import load_config

TZ_MADRID = ZoneInfo("Europe/Madrid")
MAX_HORAS_REINTENTO = 3
PAUSA_REINTENTO_MIN = 15
DIAS_BACKFILL = 7
COLUMNA = "omie_price"
HORAS_ESPERADAS = 23


def filtrar_dia_madrid(df: pd.DataFrame, fecha: date, col_datetime="datetime") -> pd.DataFrame:
    if df is None or df.empty:
        return df
    df = df.copy()
    df[col_datetime] = pd.to_datetime(df[col_datetime], utc=True)
    df["_local"] = df[col_datetime].dt.tz_convert("Europe/Madrid")
    df = df[df["_local"].dt.date == fecha]
    return df.drop(columns=["_local"])


def dia_ya_completo(db_config, fecha: date, columna: str = COLUMNA, horas_esperadas: int = HORAS_ESPERADAS) -> bool:
    conn = psycopg2.connect(**db_config)
    cur = conn.cursor()
    cur.execute(
        f"SELECT COUNT(*) FROM spot_price WHERE datetime::date = %s AND {columna} IS NOT NULL",
        (fecha,)
    )
    count = cur.fetchone()[0]
    cur.close()
    conn.close()
    return count >= horas_esperadas




def _normalize_text(s: str) -> str:
    """Normaliza acentos y pasa a minúsculas para búsqueda robusta."""
    nf = unicodedata.normalize("NFKD", s)
    no_accents = "".join(ch for ch in nf if not unicodedata.combining(ch))
    return no_accents.lower()

def intentar_descargar_omie(fecha: date, intento_max_sufijo: int = 3):
    """
    Descarga y parsea el fichero INT_PBC...TXT (formato horario) para la fecha dada.
    Firma compatible con la función original: (fecha, intento_max_sufijo=3).
    - Usa hasta `intento_max_sufijo` hosts (en orden) para intentar la descarga.
    - Devuelve pd.DataFrame con columnas: "datetime" y "omie_price", o None si falla.
    """
    year = fecha.year
    month = fecha.month
    day = fecha.day

    filename = f"INT_PBC_EV_H_1_60M_{day:02d}_{month:02d}_{year}_{day:02d}_{month:02d}_{year}.TXT"
    path = f"/sites/default/files/dados/AGNO_{year}/MES_{month:02d}/TXT/{filename}"

    # Hosts a probar (ordenado). Usaremos como máximo intento_max_sufijo entradas.
    hosts = [
        "http://omie.es",
        "https://omie.es",
        "http://www.omie.es",
        "https://www.omie.es",
    ]
    # respetar intento_max_sufijo en el número de hosts probados (si es razonable)
    if isinstance(intento_max_sufijo, int) and intento_max_sufijo > 0:
        hosts_to_try = hosts[:min(len(hosts), intento_max_sufijo)]
    else:
        hosts_to_try = hosts

    resp = None
    for host in hosts_to_try:
        url = host + path
        try:
            r = requests.get(url, timeout=30)
            if r.status_code == 200 and len(r.text.strip()) > 10:
                resp = r
                print(f"    Archivo encontrado: {url}")
                break
            else:
                # mostrar código si no fue 200 para depuración
                print(f"    Intento {url} -> status {r.status_code}")
        except Exception as e:
            print(f"    Error descargando {url}: {e}")

    # Intentar variante con .txt en minúsculas si no hallado aún
    if resp is None:
        alt_path = path.replace(".TXT", ".txt")
        for host in hosts_to_try:
            url = host + alt_path
            try:
                r = requests.get(url, timeout=30)
                if r.status_code == 200 and len(r.text.strip()) > 10:
                    resp = r
                    print(f"    Archivo encontrado (alt): {url}")
                    break
                else:
                    print(f"    Intento {url} -> status {r.status_code}")
            except Exception as e:
                print(f"    Error descargando {url}: {e}")

    if resp is None:
        print(f"    No se encontro archivo para {fecha}")
        return None

    # Buscar la línea que contiene "Precio marginal en el sistema español"
    target_key = "precio marginal en el sistema espanol"
    found_values = None
    for linea in resp.text.splitlines():
        linea_stripped = linea.strip()
        if not linea_stripped:
            continue
        if target_key in _normalize_text(linea_stripped):
            # dividir por ';' y extraer valores numéricos
            partes = [p.strip() for p in linea_stripped.split(";")]
            valores = []
            for p in partes:
                if p == "" or any(c.isalpha() for c in p):
                    continue
                token = p
                # normalizar números: si tiene tanto '.' como ',' asumimos punto de miles y coma decimal
                if ',' in token and '.' in token:
                    token = token.replace('.', '').replace(',', '.')
                else:
                    token = token.replace(',', '.')
                try:
                    val = float(token)
                    valores.append(val)
                except Exception:
                    continue
            if valores:
                found_values = valores
                break

    if not found_values:
        print("    No se encontró la línea 'Precio marginal en el sistema español' con valores válidos.")
        return None

    # Asegurar 24 valores; truncar o rellenar con NaN si es necesario
    if len(found_values) != 24:
        print(f"    Atención: se encontraron {len(found_values)} valores (esperados 24). Ajustando...")
        if len(found_values) > 24:
            found_values = found_values[:24]
        else:
            from math import nan
            found_values = found_values + [nan] * (24 - len(found_values))

    # Obtener tzinfo de variable global TZ_MADRID si existe (compatibilidad con código previo)
    tzinfo = globals().get("TZ_MADRID", None)

    filas_out = []
    for h, precio in enumerate(found_values):
        if tzinfo is not None:
            dt = datetime(fecha.year, fecha.month, fecha.day, h, tzinfo=tzinfo)
        else:
            dt = datetime(fecha.year, fecha.month, fecha.day, h)
        filas_out.append({"datetime": dt, "omie_price": None if precio is None else round(float(precio), 2)})

    return pd.DataFrame(filas_out)


def descargar_con_reintentos(fecha: date):
    max_intentos = int((MAX_HORAS_REINTENTO * 60) / PAUSA_REINTENTO_MIN)
    inicio = datetime.now()

    for intento in range(1, max_intentos + 1):
        transcurrido = (datetime.now() - inicio).total_seconds() / 60
        print(f"  Intento {intento}/{max_intentos} (t+{transcurrido:.0f} min)...")

        df = intentar_descargar_omie(fecha)
        if df is not None:
            print(f"  Datos obtenidos en el intento {intento}")
            return df

        if intento < max_intentos:
            print(f"    Esperando {PAUSA_REINTENTO_MIN} min...")
            time.sleep(PAUSA_REINTENTO_MIN * 60)

    print(f"  AGOTADOS los reintentos ({MAX_HORAS_REINTENTO}h) para {fecha}")
    return None


def dias_incompletos_recientes(db_config, dias_atras: int = DIAS_BACKFILL):
    hoy = date.today()
    fechas = [hoy - timedelta(days=i) for i in range(1, dias_atras + 1)]

    conn = psycopg2.connect(**db_config)
    cur = conn.cursor()
    incompletos = []
    for f in fechas:
        cur.execute(
            f"SELECT COUNT(*) FROM spot_price WHERE datetime::date = %s AND {COLUMNA} IS NOT NULL",
            (f,)
        )
        count = cur.fetchone()[0]
        if count < HORAS_ESPERADAS:
            incompletos.append((f, count))
    cur.close()
    conn.close()
    return incompletos


def upsert_precio(db_config, df: pd.DataFrame) -> int:
    if df is None or df.empty:
        return 0
    conn = psycopg2.connect(**db_config)
    cur = conn.cursor()
    rows = [(row["datetime"], float(row[COLUMNA])) for _, row in df.iterrows()]
    sql = f"""
        INSERT INTO spot_price (datetime, {COLUMNA})
        VALUES %s
        ON CONFLICT (datetime) DO UPDATE SET {COLUMNA} = EXCLUDED.{COLUMNA}
    """
    execute_values(cur, sql, rows)
    conn.commit()
    cur.close()
    conn.close()
    return len(rows)


def main():
    print(f"Pipeline diario spot_price — OMIE")
    print(f"Inicio: {datetime.now()}\n")

    _, db_config = load_config()

    print(f"--- Backfill (ultimos {DIAS_BACKFILL} dias) ---")
    incompletos = dias_incompletos_recientes(db_config)

    if not incompletos:
        print("  Todos los dias recientes estan completos.\n")
    else:
        for fecha_f, count_actual in incompletos:
            print(f"  {fecha_f}: {count_actual}/{HORAS_ESPERADAS}+ horas -> reintentando (1 llamada)...")
            df = intentar_descargar_omie(fecha_f)
            df = filtrar_dia_madrid(df, fecha_f)
            if df is not None and not df.empty:
                n = upsert_precio(db_config, df)
                print(f"    {n} filas actualizadas para {fecha_f}")
            else:
                print(f"    Sigue sin datos para {fecha_f}, se reintentara mañana")
        print()

    fecha_manana = date.today() + timedelta(days=1)
    print(f"--- Objetivo principal: {fecha_manana} ---")

    if dia_ya_completo(db_config, fecha_manana):
        print(f"  {fecha_manana} ya esta completo ({COLUMNA}). Nada que hacer.")
        return

    df = descargar_con_reintentos(fecha_manana)
    df = filtrar_dia_madrid(df, fecha_manana)

    if df is None or df.empty:
        print(f"\nNo se pudieron obtener datos para {fecha_manana}")
        return

    n = upsert_precio(db_config, df)
    print(f"\n{n} filas insertadas/actualizadas para {fecha_manana}")


if __name__ == "__main__":
    main()