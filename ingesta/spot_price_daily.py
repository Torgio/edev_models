"""
TFM Energia UCM - Carga DIARIA de precios en spot_price

Sustituye a los tres pipelines anteriores:
    spot_price_omie_daily.py
    spot_price_esios_daily.py
    spot_price_entsoe_daily.py

Que carga
---------
    es_esios  es_entsoe  es_omie      Espana, 3 fuentes -> validacion cruzada
    pt_entsoe pt_omie                 Portugal
    fr_entsoe                         Francia
    de_lu_entsoe                      Alemania-Luxemburgo
    it_nord_entsoe                    Italia Norte
    ch_entsoe                         Suiza

Como funciona
-------------
1. BACKFILL de los ultimos DIAS_BACKFILL dias: una sola pasada por columna
   incompleta. No reintenta en bucle; lo que falte se recoge manana.
2. OBJETIVO D+1: el precio de manana. Si ya esta completo, no llama a nadie.
   Si no, reintenta cada PAUSA_REINTENTO_MIN minutos hasta MAX_HORAS_REINTENTO.

Decisiones heredadas de la carga historica (no cambiar sin releer esto)
----------------------------------------------------------------------
* REDONDEO: Decimal ROUND_HALF_UP a 2 decimales. NUNCA round() de Python ni
  .round() de pandas: usan half-to-even y en los empates (.xx5) dan un valor
  distinto al oficial. Verificado contra el 14/08/2026: HALF_UP acierta 24/24,
  HALF_EVEN falla 3.
* OMIE: el fichero NO garantiza el orden de las columnas. El 10/02/2026 la
  cabecera traia ...H23Q1;H24Q1;H23Q2;... Agrupar por posicion metia el primer
  cuarto de una hora en la anterior (2.87/0.68 en vez de 2.60/0.95). Hay que
  agrupar por ETIQUETA leyendo la cabecera.
* HORAS DEL DIA: 23, 24 o 25 segun el calendario real, nunca un 23 fijo. Con
  el 23 fijo, un dia normal al que le falta una hora pasaba por completo.
* ESIOS con time_trunc=hour agrega por SUMA: desde MTU15 hay 4 cuartos por
  hora y hay que dividir entre 4.

Cron (servidor, hora Madrid):
    CRON_TZ=Europe/Madrid
    45 13 * * * .../spot_price_daily.py >> .../logs/cron_spot.log 2>&1
"""

import sys
import json
import time
import re
import argparse
import unicodedata
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

sys.path.append(str(Path(__file__).parent.parent))
from config import load_config

# ==========================================================================
TZ_MADRID = ZoneInfo("Europe/Madrid")
TABLA = "spot_price"
COL_TIEMPO = "datetime"

# ---------------------------------------------------------------------------
# LA TABLA MANDA
#
# Las zonas y fuentes NO estan cableadas: se deducen de las columnas que
# existan en spot_price. Nombre de columna = {zona}_{fuente}, en minusculas.
#     es_omie -> zona ES, fuente omie
#     de_lu_entsoe -> zona DE_LU, fuente entsoe   (el codigo lleva guion bajo)
# Anadir una zona es un ALTER TABLE ADD COLUMN; quitarla, un DROP COLUMN.
# El script no hay que tocarlo.
# ---------------------------------------------------------------------------
FUENTES = ("esios", "entsoe", "omie")

# geo_id del indicador 600 de ESIOS (verificados 14/08/2026 contra la API).
# ESIOS solo publica estas 6 zonas; para el resto no hay fuente ESIOS.
GEO_ESIOS = {"PT": 1, "FR": 2, "ES": 3, "DE_LU": 8826, "BE": 8827, "NL": 8828}

# OMIE solo publica ES y PT (las dos lineas del fichero INT_PBC_EV_H_1)
ZONAS_OMIE = ("ES", "PT")

# Solo para mostrar; una zona sin nombre aqui sale con su codigo
NOMBRES = {
    "ES": "Espana", "PT": "Portugal", "FR": "Francia",
    "DE_LU": "Alemania-Luxemburgo", "BE": "Belgica", "NL": "Paises Bajos",
    "AT": "Austria", "CH": "Suiza", "IT_NORD": "Italia Norte",
    "PL": "Polonia", "CZ": "Chequia", "SK": "Eslovaquia", "SI": "Eslovenia",
    "HU": "Hungria", "GB": "Reino Unido", "IE_SEM": "Irlanda (SEM)",
    "GR": "Grecia", "RO": "Rumania", "BG": "Bulgaria", "HR": "Croacia",
    "DK_1": "Dinamarca O", "DK_2": "Dinamarca E", "FI": "Finlandia",
    "NO_2": "Noruega 2", "SE_3": "Suecia 3",
    "EE": "Estonia", "LV": "Letonia", "LT": "Lituania",
}

# Estas son las que no pueden faltar: si faltan, se reintenta.
# El resto es contexto y no debe bloquear el precio espanol.
CRITICAS = {"es_omie", "es_esios", "es_entsoe"}

ESIOS_IND = 600
FECHA_MTU15 = date(2025, 10, 1)

MAX_HORAS_REINTENTO = 3
PAUSA_REINTENTO_MIN = 15
DIAS_BACKFILL = 7

Q2 = Decimal("0.01")
PRECIO_MIN, PRECIO_MAX = Decimal("-1000"), Decimal("6000")
TIMEOUT_S = 60
PAUSA_S = 0.8

HOSTS = ["https://www.omie.es", "http://www.omie.es", "https://omie.es"]
CLAVE_ES = "precio marginal en el sistema espanol"
CLAVE_PT = "precio marginal en el sistema portugues"


def leer_credenciales():
    """
    Busca credentials.json subiendo desde la carpeta del script.
    Asi funciona igual en ingesta/ que en ingesta/historic_load/, sin
    depender de cuantos niveles haya por encima.
    """
    aqui = Path(__file__).resolve().parent
    candidatos = [aqui / "credentials.json",
                  aqui.parent / "credentials.json",
                  aqui.parent.parent / "credentials.json"]
    for c in candidatos:
        if c.is_file():
            return json.load(open(c, encoding="utf-8")), c
    rutas = "\n    ".join(str(c) for c in candidatos)
    raise ErrorEstructural(
        "no se encuentra credentials.json. Buscado en:\n    " + rutas)


class ErrorEstructural(Exception):
    """No se arregla esperando: esquema, columna inexistente, token invalido."""


def col(z, f):
    return f"{z.lower()}_{f}"


def partir(nombre):
    """'de_lu_entsoe' -> ('DE_LU', 'entsoe'). None si no encaja el patron."""
    for f in FUENTES:
        suf = "_" + f
        if nombre.endswith(suf):
            z = nombre[:-len(suf)]
            if z:
                return z.upper(), f
    return None


def descubrir(conn):
    """
    Lee spot_price y devuelve (columnas_en_orden, zonas_ordenadas).
    zonas = {"ES": ["esios","entsoe","omie"], ...} en el orden de la tabla.
    """
    cur = conn.cursor()
    cur.execute("SELECT column_name FROM information_schema.columns "
                "WHERE table_name=%s ORDER BY ordinal_position", (TABLA,))
    todas = [r[0] for r in cur.fetchall()]
    cur.close()
    if not todas:
        raise ErrorEstructural(f"la tabla {TABLA} no existe")
    if COL_TIEMPO not in todas:
        raise ErrorEstructural(f"falta la columna {TABLA}.{COL_TIEMPO}")

    cols, zonas, ignoradas = [], {}, []
    for c in todas:
        if c == COL_TIEMPO:
            continue
        par = partir(c)
        if par is None:
            ignoradas.append(c)
            continue
        z, f = par
        if f == "esios" and z not in GEO_ESIOS:
            ignoradas.append(f"{c} (ESIOS no publica {z})")
            continue
        if f == "omie" and z not in ZONAS_OMIE:
            ignoradas.append(f"{c} (OMIE no publica {z})")
            continue
        cols.append(c)
        zonas.setdefault(z, []).append(f)

    if ignoradas:
        print(f"  columnas ignoradas: {', '.join(ignoradas)}")
    return cols, zonas


# --------------------------------------------------------------------------
# Calendario y numeros
# --------------------------------------------------------------------------
def horas_del_dia(f: date) -> int:
    ini = datetime(f.year, f.month, f.day, tzinfo=TZ_MADRID)
    s = f + timedelta(days=1)
    fin = datetime(s.year, s.month, s.day, tzinfo=TZ_MADRID)
    return round((fin.astimezone(timezone.utc) - ini.astimezone(timezone.utc)).total_seconds() / 3600)


def indice(f: date) -> pd.DatetimeIndex:
    ini = datetime(f.year, f.month, f.day, tzinfo=TZ_MADRID).astimezone(timezone.utc)
    return pd.date_range(start=ini, periods=horas_del_dia(f), freq="h", tz="UTC").tz_convert(TZ_MADRID)


def redondear(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    d = Decimal(str(v)).quantize(Q2, rounding=ROUND_HALF_UP)
    return d if PRECIO_MIN <= d <= PRECIO_MAX else None


# --------------------------------------------------------------------------
# OMIE
# --------------------------------------------------------------------------
def _norm(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()


def _num(tok):
    t = tok.strip()
    if not t or any(c.isalpha() for c in t):
        return None
    t = t.replace(".", "").replace(",", ".") if ("," in t and "." in t) else t.replace(",", ".")
    try:
        return Decimal(t)
    except Exception:
        return None


def _linea(texto, clave):
    for ln in texto.splitlines():
        s = ln.strip()
        if s and clave in _norm(s):
            v = [x for x in (_num(p) for p in s.split(";")) if x is not None]
            if v:
                return v
    return None


def _cabecera(texto):
    for ln in texto.splitlines():
        s = ln.strip()
        if not s.startswith(";"):
            continue
        e = [p.strip().upper() for p in s.split(";") if p.strip()]
        if e and (all(x.startswith("H") and "Q" in x for x in e) or all(x.isdigit() for x in e)):
            return e
    return None


def _fecha_fichero(texto):
    for tok in (texto.splitlines()[0] if texto else "").split(";"):
        t = tok.strip()
        if re.fullmatch(r"\d{2}/\d{2}/\d{4}", t):
            d, m, y = (int(x) for x in t.split("/"))
            try:
                return date(y, m, d)
            except ValueError:
                return None
    return None


def _agrupar(etq, vals, n_h, por_hora):
    if len(etq) != len(vals) or len(set(etq)) != len(etq):
        return None
    g = {}
    for e, v in zip(etq, vals):
        try:
            h = int(e[1:e.index("Q")]) if "Q" in e else int(e)
        except (ValueError, IndexError):
            return None
        g.setdefault(h, []).append(v)
    if sorted(g) != list(range(1, n_h + 1)):
        return None
    if any(len(v) != por_hora for v in g.values()):
        return None
    return g


def descargar_omie(f: date):
    """Devuelve DataFrame [datetime, es_omie, pt_omie] o (None, motivo)."""
    texto = None
    for host in HOSTS:
        d, m, y = f.day, f.month, f.year
        url = (f"{host}/sites/default/files/dados/AGNO_{y}/MES_{m:02d}/TXT/"
               f"INT_PBC_EV_H_1_{d:02d}_{m:02d}_{y}_{d:02d}_{m:02d}_{y}.TXT")
        try:
            r = requests.get(url, timeout=TIMEOUT_S)
            if r.status_code == 200 and len(r.text.strip()) > 10:
                r.encoding = "ISO-8859-1"
                texto = r.text
                break
            if r.status_code == 404:
                break
        except requests.RequestException:
            continue
    if texto is None:
        return None, "sin fichero"

    if _fecha_fichero(texto) != f:
        return None, f"fichero de otra fecha ({_fecha_fichero(texto)})"

    es = _linea(texto, CLAVE_ES)
    if not es:
        return None, "sin linea de precio ES"
    pt = _linea(texto, CLAVE_PT)
    hay_pt = bool(pt) and len(pt) == len(es)

    etq = _cabecera(texto)
    if etq is None or len(etq) != len(es):
        return None, "cabecera ausente o descuadrada"

    n_h, n = horas_del_dia(f), len(es)
    if n == n_h * 4:
        por_hora = 4
    elif n == n_h:
        por_hora = 1
    else:
        return None, f"desajuste: {n} valores, esperados {n_h} o {n_h*4}"

    g_es = _agrupar(etq, es, n_h, por_hora)
    if g_es is None:
        return None, "etiquetas de cabecera incoherentes"
    g_pt = _agrupar(etq, pt, n_h, por_hora) if hay_pt else None

    media = lambda v: (sum(v) / Decimal(len(v))).quantize(Q2, rounding=ROUND_HALF_UP)
    df = pd.DataFrame({
        COL_TIEMPO: indice(f),
        "es_omie": [media(g_es[h + 1]) for h in range(n_h)],
        "pt_omie": [media(g_pt[h + 1]) for h in range(n_h)] if g_pt else [None] * n_h,
    })
    return df, None


# --------------------------------------------------------------------------
# ESIOS
# --------------------------------------------------------------------------
def descargar_esios(f: date, geo_id: int, columna: str, headers):
    try:
        r = requests.get(f"https://api.esios.ree.es/indicators/{ESIOS_IND}",
                         headers=headers, timeout=TIMEOUT_S,
                         params={"start_date": f"{f}T00:00:00", "end_date": f"{f}T23:59:59",
                                 "time_trunc": "hour", "geo_ids[]": geo_id})
    except requests.RequestException as e:
        return None, str(e)
    if r.status_code in (401, 403):
        raise ErrorEstructural(f"ESIOS HTTP {r.status_code}: token invalido")
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"

    vals = r.json().get("indicator", {}).get("values", [])
    if not vals:
        return None, "sin valores"

    df = pd.DataFrame(vals)[["datetime", "value"]]
    df[COL_TIEMPO] = pd.to_datetime(df["datetime"], utc=True).dt.tz_convert(TZ_MADRID)
    # time_trunc=hour agrega por SUMA; desde MTU15 hay 4 cuartos por hora
    mask = df[COL_TIEMPO].dt.date >= FECHA_MTU15
    df.loc[mask, "value"] = df.loc[mask, "value"] / 4
    df[columna] = df["value"].map(redondear)
    df = df[df[columna].notna()]
    return (df[[COL_TIEMPO, columna]], None) if not df.empty else (None, "todo nulo")


# --------------------------------------------------------------------------
# ENTSO-E
# --------------------------------------------------------------------------
def descargar_entsoe(f: date, zona, client):
    t0 = pd.Timestamp(str(f), tz="Europe/Madrid")
    t1 = t0 + pd.Timedelta(days=1)
    try:
        s = client.query_day_ahead_prices(zona, start=t0, end=t1)
    except Exception as e:
        if "unauthorized" in str(e).lower() or "401" in str(e):
            raise ErrorEstructural(f"ENTSO-E token invalido: {e}")
        return None, type(e).__name__
    if s is None or s.empty:
        return None, "vacio"

    s = s[s.index < t1]
    if s.empty:
        return None, "vacio"

    # No promediar horas incompletas: con 3 cuartos saldria un precio
    # plausible pero incorrecto.
    g = s.groupby(pd.Grouper(freq="h"))
    modo = g.count().mode()
    esp = int(modo.iloc[0]) if len(modo) else 1
    validas = g.count()[g.count() == esp].index

    c = col(zona, "entsoe")
    df = g.mean().loc[validas].reset_index()
    df.columns = [COL_TIEMPO, c]
    df[c] = df[c].map(redondear)
    df = df[df[c].notna()]
    return (df, None) if not df.empty else (None, "todo nulo")


# --------------------------------------------------------------------------
# Base de datos
# --------------------------------------------------------------------------
def resumen_esquema(cols, zonas):
    print(f"Esquema leido de {TABLA}: {len(zonas)} zonas, {len(cols)} columnas de precio")
    for z, fs in zonas.items():
        print(f"  {z:<9} {NOMBRES.get(z, z):<22} {', '.join(fs)}")
    faltan_crit = CRITICAS - set(cols)
    if faltan_crit:
        print(f"  AVISO: faltan columnas criticas {sorted(faltan_crit)}; "
              f"se cargara lo que haya")


def estado_dia(conn, f: date, cols) -> dict:
    """Horas cargadas por columna para ese dia."""
    n_h = horas_del_dia(f)
    sel = ", ".join(f"COUNT({c})" for c in cols)
    cur = conn.cursor()
    cur.execute(f"SELECT {sel} FROM {TABLA} WHERE {COL_TIEMPO}::date = %s", (f,))
    fila = cur.fetchone()
    cur.close()
    return {c: (n if n is not None else 0) >= n_h for c, n in zip(cols, fila)}


def upsert(conn, df, cols) -> int:
    vistos = {}
    for _, r in df.iterrows():
        if all(r.get(c) is None for c in cols):
            continue
        ts = r[COL_TIEMPO].to_pydatetime()
        vistos[ts] = tuple([ts] + [r.get(c) for c in cols])
    rows = list(vistos.values())
    if not rows:
        return 0
    sets = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols)
    cur = conn.cursor()
    execute_values(cur,
                   f"INSERT INTO {TABLA} ({COL_TIEMPO}, {', '.join(cols)}) VALUES %s "
                   f"ON CONFLICT ({COL_TIEMPO}) DO UPDATE SET {sets}", rows)
    conn.commit()
    cur.close()
    return len(rows)


# --------------------------------------------------------------------------
def cargar_dia(conn, f: date, pendientes, zonas, headers, client, verbose=True):
    """Descarga SOLO las columnas pendientes. Devuelve dict {columna: ok}."""
    hecho = {}

    # --- OMIE: un solo fichero da ES y PT ---
    cols_omie = [col(z, "omie") for z in ZONAS_OMIE if "omie" in zonas.get(z, [])]
    if set(cols_omie) & pendientes:
        df, err = descargar_omie(f)
        if df is None:
            if verbose:
                print(f"      omie          {err}")
            for c in cols_omie:
                hecho[c] = False
        else:
            n = upsert(conn, df, cols_omie)
            if verbose:
                print(f"      omie          {n} filas ({', '.join(cols_omie)})")
            for c in cols_omie:
                hecho[c] = True

    # --- ESIOS: una llamada por zona, con su geo_id ---
    for z, fs in zonas.items():
        c = col(z, "esios")
        if "esios" not in fs or c not in pendientes:
            continue
        df, err = descargar_esios(f, GEO_ESIOS[z], c, headers)
        if df is None:
            if verbose:
                print(f"      {c:<13} {err}")
            hecho[c] = False
        else:
            n = upsert(conn, df, [c])
            if verbose:
                print(f"      {c:<13} {n} filas")
            hecho[c] = True
        time.sleep(PAUSA_S)

    # --- ENTSO-E: una llamada por zona ---
    for z, fs in zonas.items():
        c = col(z, "entsoe")
        if "entsoe" not in fs or c not in pendientes:
            continue
        df, err = descargar_entsoe(f, z, client)
        if df is None:
            if verbose:
                print(f"      {c:<13} {err}")
            hecho[c] = False
        else:
            n = upsert(conn, df, [c])
            if verbose:
                print(f"      {c:<13} {n} filas")
            hecho[c] = True
        time.sleep(PAUSA_S)

    return hecho


def main():
    p = argparse.ArgumentParser(description="Carga diaria de precios en spot_price")
    p.add_argument("--fecha", help="cargar un dia concreto (YYYY-MM-DD)")
    p.add_argument("--sin-reintentos", action="store_true",
                   help="una sola pasada para D+1, sin esperar")
    a = p.parse_args()

    print("Pipeline diario spot_price")
    print(f"Inicio: {datetime.now()}\n")

    headers, db = load_config()
    conn = psycopg2.connect(**db)

    try:
        cols, zonas = descubrir(conn)
        resumen_esquema(cols, zonas)

        from entsoe import EntsoePandasClient
        creds, ruta = leer_credenciales()
        print(f"  credenciales: {ruta}")
        client = EntsoePandasClient(api_key=creds["entsoe_token"])
        print()

        # ---- dia concreto ----
        if a.fecha:
            f = date.fromisoformat(a.fecha)
            print(f"--- Dia unico: {f} ---")
            est = estado_dia(conn, f, cols)
            pend = {c for c, ok in est.items() if not ok}
            print(f"  faltan {len(pend)}/{len(cols)} columnas"
                  + (f": {sorted(pend)}" if pend else ""))
            cargar_dia(conn, f, pend or set(cols), zonas, headers, client)
            est = estado_dia(conn, f, cols)
            sigue = sorted(c for c, ok in est.items() if not ok)
            print(f"\n  tras la carga faltan {len(sigue)}"
                  + (f": {sigue}" if sigue else ""))
            return

        # ---- 1. Backfill ----
        # Desde HOY (i=0), no desde ayer: el dia en curso se cargo ayer como
        # D+1, pero si el cron no corrio ese dia quedaria descubierto -- el
        # backfill empieza en hoy-1 y el objetivo va a hoy+1, dejando hoy en
        # tierra de nadie.
        print(f"--- Backfill (hoy y {DIAS_BACKFILL} dias atras) ---")
        hoy = date.today()
        algo = False
        for i in range(0, DIAS_BACKFILL + 1):
            f = hoy - timedelta(days=i)
            est = estado_dia(conn, f, cols)
            pend = {c for c, ok in est.items() if not ok}
            if not pend:
                continue
            algo = True
            print(f"  {f}: faltan {len(pend)} -> {sorted(pend)}")
            cargar_dia(conn, f, pend, zonas, headers, client)
        if not algo:
            print("  Todos los dias recientes estan completos.")
        print()

        # ---- 2. Objetivo D+1 ----
        manana = hoy + timedelta(days=1)
        criticas = CRITICAS & set(cols)
        print(f"--- Objetivo principal: {manana} ---")
        print(f"  criticas: {sorted(criticas)}")

        est = estado_dia(conn, manana, cols)
        if criticas and all(est[c] for c in criticas):
            ctx = {c for c, ok in est.items() if not ok}
            print("  Criticas completas.")
            if ctx:
                print(f"  Falta contexto: {sorted(ctx)} -> una pasada")
                cargar_dia(conn, manana, ctx, zonas, headers, client)
        else:
            intentos = 1 if a.sin_reintentos else int(MAX_HORAS_REINTENTO * 60 / PAUSA_REINTENTO_MIN)
            t0 = datetime.now()
            for i in range(1, intentos + 1):
                mins = (datetime.now() - t0).total_seconds() / 60
                print(f"  Intento {i}/{intentos} (t+{mins:.0f} min)")
                est = estado_dia(conn, manana, cols)
                pend = {c for c, ok in est.items() if not ok}
                cargar_dia(conn, manana, pend, zonas, headers, client)

                est = estado_dia(conn, manana, cols)
                if all(est[c] for c in criticas):
                    falta = sorted(c for c, ok in est.items() if not ok)
                    print(f"\n  Criticas completas en el intento {i}.")
                    if falta:
                        print(f"  Sin datos aun (contexto): {falta}")
                    break
                if i < intentos:
                    print(f"    faltan criticas, esperando {PAUSA_REINTENTO_MIN} min...")
                    time.sleep(PAUSA_REINTENTO_MIN * 60)
            else:
                print(f"\n  AGOTADOS los reintentos ({MAX_HORAS_REINTENTO}h) para {manana}")

        # ---- 3. Coherencia de las fuentes espanolas ----
        fuentes_es = [c for c in ("es_esios", "es_entsoe", "es_omie") if c in cols]
        if len(fuentes_es) >= 2:
            base = fuentes_es[-1]
            comp = ", ".join(
                f"COUNT(*) FILTER (WHERE ABS({c} - {base}) > 0.001)"
                for c in fuentes_es[:-1])
            cond = " AND ".join(f"{c} IS NOT NULL" for c in fuentes_es)
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*), {comp} FROM {TABLA} "
                        f"WHERE {COL_TIEMPO}::date = %s AND {cond}", (manana,))
            fila = cur.fetchone()
            cur.close()
            if fila[0]:
                det = "  ".join(f"{c}!={base}: {n}" for c, n in zip(fuentes_es[:-1], fila[1:]))
                print(f"\n  Coherencia {manana}: {fila[0]} horas con todas -- {det}")
                if any(fila[1:]):
                    print("    revisar: con HALF_UP no deberia haber diferencias")

    except ErrorEstructural as e:
        print(f"\nERROR ESTRUCTURAL: {e}")
        print("No se reintenta: esperar no arregla un fallo de esquema.")
        sys.exit(2)
    finally:
        conn.close()

    print(f"\nFin: {datetime.now()}")


if __name__ == "__main__":
    main()