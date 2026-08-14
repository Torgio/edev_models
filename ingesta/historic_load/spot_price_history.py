"""
TFM Energia UCM - Carga HISTORICA de precios en spot_price
Fuentes: OMIE (ES + PT + cuarto-horario), ESIOS y ENTSO-E

SIN modo dry-run: este script SIEMPRE escribe.

Que carga
---------
  spot_price.omie_price       precio ES horario   (OMIE, fuente primaria)
  spot_price.omie_price_pt    precio PT horario   -> spread iberico
  spot_price.esios_price      precio ES horario   (ESIOS ind. 600 geo 3)
  spot_price.entsoe_price     precio ES horario   (entsoe-py, zona ES)

Granularidad de OMIE
--------------------
El fichero INT_PBC_EV_H_1 existe desde 2020 y cambia de formato:
    formato viejo   cabecera ;1;2;...;24;        -> 24 valores horarios
    formato nuevo   cabecera ;H1Q1;...;H24Q4;    -> 96 valores cuarto-horarios
Se detecta por el NUMERO de valores, no por fecha, asi que el corte exacto
da igual. Los cuartos se promedian a hora; el cuarto-horario nativo se
promedian a hora.

NOTA PARA LA MEMORIA: agregar los cuartos a hora borra ~11% del spread
intradiario (verificado el 14/08/2026: 222.50 -> 198.16 EUR/MWh en un solo
dia). Ese margen es justo lo que gana una bateria arbitrando, asi que el
valor calculado en el capitulo de optimizacion queda subestimado. El
cuarto-horario solo existe desde finales de 2025, sin serie larga para
modelar; candidato a "lineas de mejora futura".

Redondeo
--------
2 decimales con Decimal ROUND_HALF_UP. NUNCA round() de Python ni .round()
de pandas: ambos usan half-to-even y en los empates (.xx5) dan un valor
distinto al oficial. Verificado contra las 24 horas del 14/08/2026:
    HALF_UP 24/24 aciertos | HALF_EVEN falla 3 | HALF_DOWN falla 6

Uso
---
  python spot_price_history.py                      # usa la CONFIGURACION
  python spot_price_history.py --desde 2020-01-01 --hasta 2026-08-14
  python spot_price_history.py --fuente omie
  python spot_price_history.py --solo-huecos        # no repite lo ya cargado
  python spot_price_history.py --validar            # solo comparar, no cargar
"""

import sys
import json
import time
import signal
import argparse
import re
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
#  CONFIGURACION - EDITA AQUI Y EJECUTA (boton play / F5)
# ==========================================================================
FECHA_DESDE = "2020-01-01"
FECHA_HASTA = "2020-01-01"
#FECHA_HASTA = "2026-08-14"

FUENTE      = "todas"   # "omie" | "esios" | "entsoe" | "eu" | "todas"

# Zonas europeas a cargar en spot_price_eu (formato largo).
#   "fronteras" -> solo FR y PT, las unicas fronteras fisicas de Espana
#   "core"      -> + mercados acoplados que mueven el precio frances
#   "todas"     -> todas las zonas de oferta del SDAC (~40)

SOLO_HUECOS = False     # False = recarga TODO el rango (corrige de paso el
                        #         redondeo de las filas viejas escritas con
                        #         pandas .round, half-even)
                        # True  = salta los dias ya completos (reanudar)

# ==========================================================================

TZ_MADRID = ZoneInfo("Europe/Madrid")
TABLA = "spot_price"
COL_TIEMPO = "datetime"
COL_OMIE = "es_omie"
COL_PT = "pt_omie"
COL_ESIOS = "es_esios"
COL_ENTSOE = "es_entsoe"

ESIOS_INDICADOR = 600

# Desde esta fecha ESIOS sirve 4 valores por hora y time_trunc=hour los SUMA,
# asi que hay que dividir entre 4. Es la misma constante que usan los
# pipelines diarios; no cambiar sin recalcular el historico.
FECHA_MTU15 = date(2025, 10, 1)

# ---------------------------------------------------------------------------
# LA TABLA MANDA
#
# Las zonas NO estan cableadas: se deducen de las columnas de spot_price.
# Nombre de columna = {zona}_{fuente} en minusculas: es_omie, de_lu_entsoe.
# Anadir zona = ALTER TABLE ADD COLUMN. Quitarla = DROP COLUMN. Sin tocar esto.
# ---------------------------------------------------------------------------
FUENTES = ("esios", "entsoe", "omie")

# geo_id del indicador 600 (verificados 14/08/2026 contra la API).
# ESIOS solo publica estas 6 zonas.
GEO_ESIOS = {"PT": 1, "FR": 2, "ES": 3, "DE_LU": 8826, "BE": 8827, "NL": 8828}

# OMIE solo publica ES y PT (las dos lineas del fichero INT_PBC_EV_H_1)
ZONAS_OMIE = ("ES", "PT")

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


def col(zona: str, fuente: str) -> str:
    return f"{zona.lower()}_{fuente}"


def partir(nombre):
    """'de_lu_entsoe' -> ('DE_LU', 'entsoe'). None si no encaja."""
    for f in FUENTES:
        suf = "_" + f
        if nombre.endswith(suf):
            z = nombre[:-len(suf)]
            if z:
                return z.upper(), f
    return None


def descubrir(conn):
    """Devuelve (columnas_en_orden, {zona: [fuentes]}) leyendo la tabla."""
    cur = conn.cursor()
    cur.execute("SELECT column_name FROM information_schema.columns "
                "WHERE table_name=%s ORDER BY ordinal_position", (TABLA,))
    todas = [r[0] for r in cur.fetchall()]
    cur.close()
    if not todas:
        raise ErrorEstructural(f"la tabla {TABLA} no existe")
    if COL_TIEMPO not in todas:
        raise ErrorEstructural(f"falta {TABLA}.{COL_TIEMPO}")

    cols, zonas, ign = [], {}, []
    for c in todas:
        if c == COL_TIEMPO:
            continue
        par = partir(c)
        if par is None:
            ign.append(c)
            continue
        z, f = par
        if f == "esios" and z not in GEO_ESIOS:
            ign.append(f"{c} (ESIOS no publica {z})")
            continue
        if f == "omie" and z not in ZONAS_OMIE:
            ign.append(f"{c} (OMIE no publica {z})")
            continue
        cols.append(c)
        zonas.setdefault(z, []).append(f)

    if ign:
        print(f"  columnas ignoradas: {', '.join(ign)}")
    return cols, zonas

Q2 = Decimal("0.01")

# Limites de casacion del SDAC. Un valor fuera de rango no es un precio raro:
# es un fichero corrupto o mal parseado. El suelo armonizado paso a -600
# EUR/MWh el 28/05/2026; antes era -500. Se usa el mas laxo con margen.
PRECIO_MIN = Decimal("-1000")
PRECIO_MAX = Decimal("6000")
TIMEOUT_S = 60
PAUSA_OMIE_S = 0.35
PAUSA_TRAMO_S = 1.0
REINTENTOS = 2
LOTE_COMMIT = 30

HOSTS = ["https://www.omie.es", "http://www.omie.es", "https://omie.es"]
CLAVE_ES = "precio marginal en el sistema espanol"
CLAVE_PT = "precio marginal en el sistema portugues"

_parar = False


def _sigint(sig, frm):
    global _parar
    if _parar:
        sys.exit(130)
    _parar = True
    print("\nCtrl+C: cerrando el lote y guardando progreso...")


signal.signal(signal.SIGINT, _sigint)


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
    """No se arregla reintentando."""


# --------------------------------------------------------------------------
# Calendario y numeros
# --------------------------------------------------------------------------
def horas_del_dia(f: date) -> int:
    ini = datetime(f.year, f.month, f.day, tzinfo=TZ_MADRID)
    s = f + timedelta(days=1)
    fin = datetime(s.year, s.month, s.day, tzinfo=TZ_MADRID)
    return round((fin.astimezone(timezone.utc) - ini.astimezone(timezone.utc)).total_seconds() / 3600)


def indice(f: date, freq: str) -> pd.DatetimeIndex:
    """Construido en UTC (sin saltos) y convertido: DST correcto."""
    ini = datetime(f.year, f.month, f.day, tzinfo=TZ_MADRID).astimezone(timezone.utc)
    n = horas_del_dia(f) * (1 if freq == "h" else 4)
    return pd.date_range(start=ini, periods=n, freq=freq, tz="UTC").tz_convert(TZ_MADRID)


def redondear(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return Decimal(str(v)).quantize(Q2, rounding=ROUND_HALF_UP)


def tramos_mensuales(desde: date, hasta: date):
    out, ini = [], desde
    while ini <= hasta:
        sig = date(ini.year + 1, 1, 1) if ini.month == 12 else date(ini.year, ini.month + 1, 1)
        out.append((ini, min(sig - timedelta(days=1), hasta)))
        ini = sig
    return out


def fmt(s: float) -> str:
    s = int(s)
    return f"{s//3600}h {(s%3600)//60:02d}m" if s >= 3600 else f"{s//60}m {s%60:02d}s"


# --------------------------------------------------------------------------
# OMIE
# --------------------------------------------------------------------------
def _norm(s: str) -> str:
    nf = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nf if not unicodedata.combining(c)).lower()


def url_omie(f: date, host: str) -> str:
    d, m, y = f.day, f.month, f.year
    return (f"{host}/sites/default/files/dados/AGNO_{y}/MES_{m:02d}/TXT/"
            f"INT_PBC_EV_H_1_{d:02d}_{m:02d}_{y}_{d:02d}_{m:02d}_{y}.TXT")


def _num(tok: str):
    t = tok.strip()
    if not t or any(c.isalpha() for c in t):
        return None
    t = t.replace(".", "").replace(",", ".") if ("," in t and "." in t) else t.replace(",", ".")
    try:
        return Decimal(t)
    except Exception:
        return None


def fecha_del_fichero(texto: str):
    """
    Fecha de entrega declarada en la primera linea del fichero.
        OMIE - Mercado de electricidad;Fecha Emision :13/08/2026 - 13:49;;14/08/2026;...
    El campo de emision lleva hora, asi que no casa con el patron exacto;
    el unico token DD/MM/YYYY puro es la fecha de entrega.

    Sirve para detectar que el servidor sirve un fichero que no corresponde
    a la fecha pedida (redireccion, cache, fichero mal nombrado). Sin esta
    comprobacion ese dia se cargaria con los precios de otro.
    """
    primera = texto.splitlines()[0] if texto else ""
    for tok in primera.split(";"):
        t = tok.strip()
        if re.fullmatch(r"\d{2}/\d{2}/\d{4}", t):
            try:
                d, m, y = (int(x) for x in t.split("/"))
                return date(y, m, d)
            except ValueError:
                return None
    return None


def leer_cabecera(texto: str):
    """
    Devuelve la lista de etiquetas de columna: ["H1Q1", ...] o ["1","2",...].

    CRITICO: OMIE NO garantiza el orden. Ejemplo real, 10/02/2026:
        ...;H16Q1;H17Q1;H16Q2;H16Q3;H16Q4;H17Q2;...
        ...;H23Q1;H24Q1;H23Q2;H23Q3;H23Q4;H24Q2;...
    Agrupar de 4 en 4 por POSICION mete el primer cuarto de una hora en la
    anterior. Ese dia daba 2.87/0.68 en las horas 22 y 23, cuando el valor
    correcto es 2.60/0.95 (confirmado contra ESIOS y ENTSO-E).
    Hay que agrupar por ETIQUETA, nunca por posicion.
    """
    for ln in texto.splitlines():
        s = ln.strip()
        if not s.startswith(";"):
            continue
        etq = [p.strip() for p in s.split(";") if p.strip()]
        if not etq:
            continue
        if all(e.upper().startswith("H") and "Q" in e.upper() for e in etq):
            return [e.upper() for e in etq]          # cuarto-horario
        if all(e.isdigit() for e in etq):
            return etq                                # horario antiguo
    return None


def agrupar_por_hora(etiquetas, valores, n_h, por_hora):
    """
    Empareja cada valor con su hora segun la ETIQUETA, no por posicion.

    Comprueba, ademas de la correspondencia:
      - que estan TODAS las horas 1..n_h y ninguna de mas
      - que cada hora tiene EXACTAMENTE `por_hora` valores (4 o 1)
      - que no hay etiquetas repetidas
    Un cuarto que falte haria que la media se calculara sobre 3 valores y
    saliera un precio plausible pero incorrecto: hay que rechazarlo, no
    promediar lo que haya.

    Devuelve (dict {hora: [valores]}, None) o (None, motivo).
    """
    if len(etiquetas) != len(valores):
        return None, f"cabecera {len(etiquetas)} vs {len(valores)} valores"
    if len(set(etiquetas)) != len(etiquetas):
        rep = [e for e in set(etiquetas) if etiquetas.count(e) > 1]
        return None, f"etiquetas repetidas: {sorted(rep)[:5]}"

    g = {}
    for e, v in zip(etiquetas, valores):
        try:
            h = int(e[1:e.index("Q")]) if "Q" in e else int(e)
        except (ValueError, IndexError):
            return None, f"etiqueta ilegible: {e!r}"
        g.setdefault(h, []).append(v)

    if sorted(g) != list(range(1, n_h + 1)):
        faltan = sorted(set(range(1, n_h + 1)) - set(g))
        sobran = sorted(set(g) - set(range(1, n_h + 1)))
        return None, f"horas faltan={faltan[:5]} sobran={sobran[:5]}"

    malas = [h for h, v in g.items() if len(v) != por_hora]
    if malas:
        return None, f"horas con != {por_hora} valores: {sorted(malas)[:5]}"
    return g, None


def _linea(texto: str, clave: str):
    for ln in texto.splitlines():
        s = ln.strip()
        if s and clave in _norm(s):
            vals = [v for v in (_num(p) for p in s.split(";")) if v is not None]
            if vals:
                return vals
    return None


def bajar_omie(f: date):
    for host in HOSTS:
        try:
            r = requests.get(url_omie(f, host), timeout=TIMEOUT_S)
            if r.status_code == 200 and len(r.text.strip()) > 10:
                r.encoding = "ISO-8859-1"
                return r.text
            if r.status_code == 404:
                break
        except requests.RequestException:
            continue
    return None


def parsear_omie(texto: str, f: date):
    """Devuelve (df_horario, modo) o (None, motivo)."""
    # 1. El fichero debe ser del dia que pedimos
    f_fich = fecha_del_fichero(texto)
    if f_fich is None:
        return None, "no se pudo leer la fecha del fichero"
    if f_fich != f:
        return None, f"fichero de {f_fich}, se pidio {f}"

    # 2. Linea de precio ES (la PT comparte prefijo y va justo debajo)
    es = _linea(texto, CLAVE_ES)
    if not es:
        return None, "sin linea de precio ES"
    pt = _linea(texto, CLAVE_PT)
    hay_pt = bool(pt) and len(pt) == len(es)

    # 3. Rango de cordura: fuera de esto es fichero corrupto, no precio raro
    fuera = [v for v in es if not (PRECIO_MIN <= v <= PRECIO_MAX)]
    if fuera:
        return None, f"precios fuera de rango: {fuera[:3]}"

    n_h, n = horas_del_dia(f), len(es)

    # 4. Cabecera obligatoria: sin ella no se puede saber que valor es de que
    #    hora, y OMIE NO garantiza el orden (ver leer_cabecera)
    etq = leer_cabecera(texto)
    if etq is None:
        return None, "sin cabecera de columnas"
    if len(etq) != n:
        return None, f"cabecera {len(etq)} etiquetas vs {n} valores"

    if n == n_h * 4:
        modo, por_hora = "qh", 4
    elif n == n_h:
        modo, por_hora = "h", 1
    else:
        return None, f"desajuste: {n} valores, esperados {n_h} o {n_h * 4}"

    g_es, err = agrupar_por_hora(etq, es, n_h, por_hora)
    if g_es is None:
        return None, f"ES {err}"

    g_pt = None
    if hay_pt:
        g_pt, err_pt = agrupar_por_hora(etq, pt, n_h, por_hora)
        if g_pt is None:
            hay_pt = False          # se pierde PT, pero ES es valido

    media = lambda v: (sum(v) / Decimal(len(v))).quantize(Q2, rounding=ROUND_HALF_UP)
    es_h = [media(g_es[h + 1]) for h in range(n_h)]
    pt_h = [media(g_pt[h + 1]) for h in range(n_h)] if g_pt else [None] * n_h


    df_h = pd.DataFrame({COL_TIEMPO: indice(f, "h"), COL_OMIE: es_h, COL_PT: pt_h})
    return df_h, modo


# --------------------------------------------------------------------------
# ESIOS
# --------------------------------------------------------------------------
def bajar_esios(ini: date, fin: date, geo_id: int, columna: str, headers: dict):
    params = {"start_date": f"{ini}T00:00:00", "end_date": f"{fin}T23:59:59",
              "time_trunc": "hour", "geo_ids[]": geo_id}
    r = None
    for intento in range(REINTENTOS + 1):
        try:
            r = requests.get(f"https://api.esios.ree.es/indicators/{ESIOS_INDICADOR}",
                             headers=headers, params=params, timeout=TIMEOUT_S)
            if r.status_code == 200:
                break
            if r.status_code in (401, 403):
                raise ErrorEstructural(f"ESIOS HTTP {r.status_code}: token invalido")
        except ErrorEstructural:
            raise
        except requests.RequestException:
            pass
        if intento < REINTENTOS:
            time.sleep(2 * (intento + 1))
    if r is None or r.status_code != 200:
        return None

    vals = r.json().get("indicator", {}).get("values", [])
    if not vals:
        return None

    df = pd.DataFrame(vals)[["datetime", "value"]]
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True).dt.tz_convert(TZ_MADRID)
    # time_trunc=hour agrega por SUMA; desde MTU15 hay 4 cuartos por hora
    mask = df["datetime"].dt.date >= FECHA_MTU15
    df.loc[mask, "value"] = df.loc[mask, "value"] / 4
    df[columna] = df["value"].map(redondear)
    df = df[df[columna].notna()]
    df = df[df[columna].map(lambda v: PRECIO_MIN <= v <= PRECIO_MAX)]
    if df.empty:
        return None
    return df[["datetime", columna]].rename(columns={"datetime": COL_TIEMPO})


# --------------------------------------------------------------------------
# ENTSO-E
# --------------------------------------------------------------------------
def bajar_zona(ini: date, fin: date, zona: str, columna: str, client):
    """
    Precio day-ahead de una zona de oferta. Devuelve DataFrame o None.

    Cada zona tiene su propia hora local; entsoe-py devuelve indice tz-aware
    y se guarda como timestamptz, o sea el instante absoluto. Comparar entre
    zonas es correcto sin conversiones.
    """
    t0 = pd.Timestamp(str(ini), tz="Europe/Madrid")
    t1 = pd.Timestamp(str(fin), tz="Europe/Madrid") + pd.Timedelta(days=1)
    serie = None
    for intento in range(REINTENTOS + 1):
        try:
            serie = client.query_day_ahead_prices(zona, start=t0, end=t1)
            break
        except Exception as e:
            msg = str(e).lower()
            if "unauthorized" in msg or "401" in msg:
                raise ErrorEstructural(f"ENTSO-E: token invalido ({e})")
            if "nomatchingdata" in type(e).__name__.lower() or "no matching data" in msg:
                return None          # zona sin datos en ese tramo: normal
            if intento < REINTENTOS:
                time.sleep(2 * (intento + 1))
    if serie is None or serie.empty:
        return None

    serie = serie[serie.index < t1]
    if serie.empty:
        return None

    g = serie.groupby(pd.Grouper(freq="h"))
    cuentas = g.count()
    modo = cuentas.mode()
    esperado = int(modo.iloc[0]) if len(modo) else 1
    validas = cuentas[cuentas == esperado].index

    df = g.mean().loc[validas].reset_index()
    df.columns = [COL_TIEMPO, columna]          # nombre final, no "price"
    df = df[df[columna].notna()]
    df[columna] = df[columna].map(redondear)
    df = df[df[columna].notna()]
    return df if not df.empty else None


# --------------------------------------------------------------------------
# Base de datos
# --------------------------------------------------------------------------
def resumen_esquema(cols, zonas):
    print(f"Esquema leido de {TABLA}: {len(zonas)} zonas, {len(cols)} columnas")
    for z, fs in zonas.items():
        print(f"  {z:<9} {NOMBRES.get(z, z):<22} {', '.join(fs)}")


def dias_completos(conn, col, desde, hasta) -> set:
    cur = conn.cursor()
    cur.execute(f"SELECT {COL_TIEMPO}::date, COUNT(*) FROM {TABLA} "
                f"WHERE {COL_TIEMPO}::date BETWEEN %s AND %s AND {col} IS NOT NULL GROUP BY 1",
                (desde, hasta))
    filas = cur.fetchall()
    cur.close()
    return {d for d, n in filas if n >= horas_del_dia(d)}


def upsert(cur, df, cols) -> int:
    """
    Deduplica por timestamp: PostgreSQL aborta el INSERT entero si la misma
    clave aparece dos veces en el mismo VALUES ("cannot affect row a second
    time"). Se queda con la ultima aparicion.
    """
    vistos = {}
    for _, r in df.iterrows():
        if all(r[c] is None for c in cols):
            continue
        ts = r[COL_TIEMPO].to_pydatetime()
        vistos[ts] = tuple([ts] + [r[c] for c in cols])
    rows = list(vistos.values())
    if not rows:
        return 0
    sets = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols)
    execute_values(cur,
                   f"INSERT INTO {TABLA} ({COL_TIEMPO}, {', '.join(cols)}) VALUES %s "
                   f"ON CONFLICT ({COL_TIEMPO}) DO UPDATE SET {sets}", rows)
    return len(rows)


def cargar_omie(conn, desde, hasta, solo_huecos, cols_omie):
    fechas = [desde + timedelta(days=i) for i in range((hasta - desde).days + 1)]
    destino = " + ".join(cols_omie)
    print(f"\n{'='*64}\n  OMIE  ->  {destino}\n"
          f"  {desde} a {hasta}   {len(fechas)} dias\n{'='*64}")

    ya = dias_completos(conn, COL_OMIE, desde, hasta) if solo_huecos else set()
    if solo_huecos:
        print(f"  dias ya completos: {len(ya)} (se saltan)")
    else:
        print("  RECARGA COMPLETA: no se salta ningun dia")

    cols = list(cols_omie)
    ok = fallo = salt = fh = fq = 0
    modos = {"h": 0, "qh": 0}
    fallidos, avisos = [], []
    t0 = time.time()
    cur = conn.cursor()
    pend = 0

    for i, f in enumerate(fechas, 1):
        if _parar:
            print(f"\n  Interrumpido en {f} ({i}/{len(fechas)})")
            break
        if f in ya:
            salt += 1
            continue

        texto = bajar_omie(f)
        if texto is None:
            fallo += 1
            fallidos.append(f)
        else:
            df_h, modo = parsear_omie(texto, f)
            if df_h is None:
                fallo += 1
                fallidos.append(f)
                avisos.append(f"{f}: {modo}")
            else:
                modos[modo] += 1
                fh += upsert(cur, df_h, cols)
                ok += 1
                pend += 1
                if pend >= LOTE_COMMIT:
                    conn.commit()
                    pend = 0
            time.sleep(PAUSA_OMIE_S)

        if i % 100 == 0 or i == len(fechas):
            el = time.time() - t0
            hechos = ok + fallo
            eta = (el / hechos) * (len(fechas) - i) if hechos else 0
            print(f"  [{i}/{len(fechas)}] {f}  ok={ok} fallo={fallo} salt={salt}  ETA {fmt(eta)}")

    if pend:
        conn.commit()
    cur.close()

    print(f"\n  --- omie ({fmt(time.time()-t0)}) ---")
    print(f"  dias ok       : {ok}  (horario {modos['h']}, cuarto-horario {modos['qh']})")
    print(f"  dias saltados : {salt}")
    print(f"  dias sin datos: {fallo}")
    print(f"  filas horarias: {fh}")
    if avisos:
        print(f"  avisos de parseo: {len(avisos)}")
        for a in avisos[:10]:
            print(f"    {a}")
    if fallidos:
        nom = f"fallidos_omie_{datetime.now():%Y%m%d_%H%M}.txt"
        Path(nom).write_text("\n".join(str(x) for x in fallidos), encoding="utf-8")
        print(f"  fechas sin datos -> {nom}")
        print(f"  primera: {fallidos[0]}   ultima: {fallidos[-1]}")


def cargar_serie(conn, etiqueta, columna, bajar, desde, hasta, solo_huecos):
    """Carga por tramos mensuales una columna cualquiera. `bajar(a,b)->df|None`."""
    tramos = tramos_mensuales(desde, hasta)
    print(f"\n{'='*64}\n  {etiqueta}  ->  {columna}\n"
          f"  {desde} a {hasta}   {len(tramos)} tramos mensuales\n{'='*64}")

    ya = dias_completos(conn, columna, desde, hasta) if solo_huecos else set()
    print(f"  dias ya completos: {len(ya)}" if solo_huecos
          else "  RECARGA COMPLETA: no se salta ningun tramo")

    ok = fallo = salt = filas = 0
    fallidos = []
    t0 = time.time()
    cur = conn.cursor()
    pend = 0

    for i, (a, b) in enumerate(tramos, 1):
        if _parar:
            break
        if solo_huecos:
            dias = {a + timedelta(days=k) for k in range((b - a).days + 1)}
            if dias <= ya:
                salt += 1
                continue

        df = bajar(a, b)
        if df is None or df.empty:
            fallo += 1
            fallidos.append((a, b))
            estado = "SIN DATOS"
        else:
            n = upsert(cur, df, [columna])
            filas += n
            ok += 1
            estado = f"{n} filas"
            pend += 1
            if pend >= 5:
                conn.commit()
                pend = 0

        el = time.time() - t0
        hechos = ok + fallo
        eta = (el / hechos) * (len(tramos) - i) if hechos else 0
        print(f"  [{i:>3}/{len(tramos)}] {a:%Y-%m}  {estado:>12}   ETA {fmt(eta)}")
        time.sleep(PAUSA_TRAMO_S)

    if pend:
        conn.commit()
    cur.close()

    print(f"\n  --- {columna} ({fmt(time.time()-t0)}) ---")
    print(f"  tramos ok {ok}   saltados {salt}   fallidos {fallo}   filas {filas}")
    if fallidos:
        nom = f"fallidos_{columna}_{datetime.now():%Y%m%d_%H%M}.txt"
        Path(nom).write_text("\n".join(f"{a} {b}" for a, b in fallidos), encoding="utf-8")
        print(f"  tramos sin datos -> {nom}")
    return filas


def resumen_esquema(cols, zonas):
    print(f"Esquema leido de {TABLA}: {len(zonas)} zonas, {len(cols)} columnas")
    for z, fs in zonas.items():
        print(f"  {z:<9} {NOMBRES.get(z, z):<22} {', '.join(fs)}")


def dias_completos(conn, col, desde, hasta) -> set:
    cur = conn.cursor()
    cur.execute(f"SELECT {COL_TIEMPO}::date, COUNT(*) FROM {TABLA} "
                f"WHERE {COL_TIEMPO}::date BETWEEN %s AND %s AND {col} IS NOT NULL GROUP BY 1",
                (desde, hasta))
    filas = cur.fetchall()
    cur.close()
    return {d for d, n in filas if n >= horas_del_dia(d)}


def upsert(cur, df, cols) -> int:
    """
    Deduplica por timestamp: PostgreSQL aborta el INSERT entero si la misma
    clave aparece dos veces en el mismo VALUES ("cannot affect row a second
    time"). Se queda con la ultima aparicion.
    """
    vistos = {}
    for _, r in df.iterrows():
        if all(r[c] is None for c in cols):
            continue
        ts = r[COL_TIEMPO].to_pydatetime()
        vistos[ts] = tuple([ts] + [r[c] for c in cols])
    rows = list(vistos.values())
    if not rows:
        return 0
    sets = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols)
    execute_values(cur,
                   f"INSERT INTO {TABLA} ({COL_TIEMPO}, {', '.join(cols)}) VALUES %s "
                   f"ON CONFLICT ({COL_TIEMPO}) DO UPDATE SET {sets}", rows)
    return len(rows)


def cargar_omie(conn, desde, hasta, solo_huecos, cols_omie):
    fechas = [desde + timedelta(days=i) for i in range((hasta - desde).days + 1)]
    destino = " + ".join(cols_omie)
    print(f"\n{'='*64}\n  OMIE  ->  {destino}\n"
          f"  {desde} a {hasta}   {len(fechas)} dias\n{'='*64}")

    ya = dias_completos(conn, COL_OMIE, desde, hasta) if solo_huecos else set()
    if solo_huecos:
        print(f"  dias ya completos: {len(ya)} (se saltan)")
    else:
        print("  RECARGA COMPLETA: no se salta ningun dia")

    cols = list(cols_omie)
    ok = fallo = salt = fh = fq = 0
    modos = {"h": 0, "qh": 0}
    fallidos, avisos = [], []
    t0 = time.time()
    cur = conn.cursor()
    pend = 0

    for i, f in enumerate(fechas, 1):
        if _parar:
            print(f"\n  Interrumpido en {f} ({i}/{len(fechas)})")
            break
        if f in ya:
            salt += 1
            continue

        texto = bajar_omie(f)
        if texto is None:
            fallo += 1
            fallidos.append(f)
        else:
            df_h, modo = parsear_omie(texto, f)
            if df_h is None:
                fallo += 1
                fallidos.append(f)
                avisos.append(f"{f}: {modo}")
            else:
                modos[modo] += 1
                fh += upsert(cur, df_h, cols)
                ok += 1
                pend += 1
                if pend >= LOTE_COMMIT:
                    conn.commit()
                    pend = 0
            time.sleep(PAUSA_OMIE_S)

        if i % 100 == 0 or i == len(fechas):
            el = time.time() - t0
            hechos = ok + fallo
            eta = (el / hechos) * (len(fechas) - i) if hechos else 0
            print(f"  [{i}/{len(fechas)}] {f}  ok={ok} fallo={fallo} salt={salt}  ETA {fmt(eta)}")

    if pend:
        conn.commit()
    cur.close()

    print(f"\n  --- omie ({fmt(time.time()-t0)}) ---")
    print(f"  dias ok       : {ok}  (horario {modos['h']}, cuarto-horario {modos['qh']})")
    print(f"  dias saltados : {salt}")
    print(f"  dias sin datos: {fallo}")
    print(f"  filas horarias: {fh}")
    if avisos:
        print(f"  avisos de parseo: {len(avisos)}")
        for a in avisos[:10]:
            print(f"    {a}")
    if fallidos:
        nom = f"fallidos_omie_{datetime.now():%Y%m%d_%H%M}.txt"
        Path(nom).write_text("\n".join(str(x) for x in fallidos), encoding="utf-8")
        print(f"  fechas sin datos -> {nom}")
        print(f"  primera: {fallidos[0]}   ultima: {fallidos[-1]}")


def cargar_serie(conn, etiqueta, columna, bajar, desde, hasta, solo_huecos):
    """Carga por tramos mensuales una columna cualquiera. `bajar(a,b)->df|None`."""
    tramos = tramos_mensuales(desde, hasta)
    print(f"\n{'='*64}\n  {etiqueta}  ->  {columna}\n"
          f"  {desde} a {hasta}   {len(tramos)} tramos mensuales\n{'='*64}")

    ya = dias_completos(conn, columna, desde, hasta) if solo_huecos else set()
    print(f"  dias ya completos: {len(ya)}" if solo_huecos
          else "  RECARGA COMPLETA: no se salta ningun tramo")

    ok = fallo = salt = filas = 0
    fallidos = []
    t0 = time.time()
    cur = conn.cursor()
    pend = 0

    for i, (a, b) in enumerate(tramos, 1):
        if _parar:
            break
        if solo_huecos:
            dias = {a + timedelta(days=k) for k in range((b - a).days + 1)}
            if dias <= ya:
                salt += 1
                continue

        df = bajar(a, b)
        if df is None or df.empty:
            fallo += 1
            fallidos.append((a, b))
            estado = "SIN DATOS"
        else:
            n = upsert(cur, df, [columna])
            filas += n
            ok += 1
            estado = f"{n} filas"
            pend += 1
            if pend >= 5:
                conn.commit()
                pend = 0

        el = time.time() - t0
        hechos = ok + fallo
        eta = (el / hechos) * (len(tramos) - i) if hechos else 0
        print(f"  [{i:>3}/{len(tramos)}] {a:%Y-%m}  {estado:>12}   ETA {fmt(eta)}")
        time.sleep(PAUSA_TRAMO_S)

    if pend:
        conn.commit()
    cur.close()

    print(f"\n  --- {columna} ({fmt(time.time()-t0)}) ---")
    print(f"  tramos ok {ok}   saltados {salt}   fallidos {fallo}   filas {filas}")
    if fallidos:
        nom = f"fallidos_{columna}_{datetime.now():%Y%m%d_%H%M}.txt"
        Path(nom).write_text("\n".join(f"{a} {b}" for a, b in fallidos), encoding="utf-8")
        print(f"  tramos sin datos -> {nom}")
    return filas


def validar(conn, cols, zonas):
    print(f"\n{'='*64}\n  VALIDACION\n{'='*64}")
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*), MIN({COL_TIEMPO})::date, MAX({COL_TIEMPO})::date FROM {TABLA}")
    total, a, b = cur.fetchone()
    print(f"  {TABLA}: {total} filas   {a} -> {b}\n")
    if not total:
        cur.close()
        return

    print(f"  {'columna':<16} {'zona':<22} {'horas':>8} {'%':>7} {'media':>9}")
    for z, fs in zonas.items():
        for f in fs:
            c = col(z, f)
            cur.execute(f"SELECT COUNT({c}), ROUND(AVG({c}),2) FROM {TABLA}")
            n, med = cur.fetchone()
            print(f"  {c:<16} {NOMBRES.get(z,z):<22} {n:>8} {100*n/total:>6.1f}% {str(med):>9}")

    fes = [c for c in ("es_esios", "es_entsoe", "es_omie") if c in cols]
    if len(fes) >= 2:
        base = fes[-1]
        comp = ", ".join(f"COUNT(*) FILTER (WHERE ABS({c}-{base})>0.001)" for c in fes[:-1])
        cond = " AND ".join(f"{c} IS NOT NULL" for c in fes)
        cur.execute(f"SELECT COUNT(*), {comp}, MAX(GREATEST("
                    + ", ".join(f"ABS({c}-{base})" for c in fes[:-1])
                    + f")) FROM {TABLA} WHERE {cond}")
        fila = cur.fetchone()
        if fila[0]:
            det = "  ".join(f"{c}!={base}: {n}" for c, n in zip(fes[:-1], fila[1:-1]))
            print(f"\n  Espana, coherencia ({fila[0]} horas con todas): {det}")
            print(f"    diferencia maxima: {fila[-1]}")

    if "pt_omie" in cols and "es_omie" in cols:
        cur.execute(f"""SELECT COUNT(*), COUNT(*) FILTER (WHERE ABS(pt_omie-es_omie)>0.001),
                               MAX(ABS(pt_omie-es_omie))
                        FROM {TABLA} WHERE pt_omie IS NOT NULL AND es_omie IS NOT NULL""")
        n2, nsp, mx = cur.fetchone()
        if n2:
            print(f"\n  Spread iberico ({n2} horas): splitting en {nsp} "
                  f"({100*nsp/n2:.1f}%), maximo {mx} EUR/MWh")

    otras = [col(z, "entsoe") for z in zonas
             if z != "ES" and "entsoe" in zonas[z] and col(z, "entsoe") in cols]
    if otras and "es_omie" in cols:
        print(f"\n  Frente a Espana:   {'columna':<16} {'horas':>8} {'corr':>7} "
              f"{'spread':>9} {'sd':>9}")
        for c in otras:
            cur.execute(f"""SELECT COUNT(*), ROUND(CORR({c},es_omie)::numeric,3),
                                   ROUND(AVG({c}-es_omie),2), ROUND(STDDEV({c}-es_omie),2)
                            FROM {TABLA} WHERE {c} IS NOT NULL AND es_omie IS NOT NULL""")
            n3, cr, md, sd = cur.fetchone()
            if n3:
                print(f"                     {c:<16} {n3:>8} {str(cr):>7} {str(md):>9} {str(sd):>9}")
    cur.close()


def main():
    p = argparse.ArgumentParser(description="Carga historica de precios en spot_price")
    p.add_argument("--desde")
    p.add_argument("--hasta")
    p.add_argument("--solo-huecos", action="store_true")
    p.add_argument("--solo", help="cargar solo estas columnas, separadas por comas")
    p.add_argument("--validar", action="store_true")
    a = p.parse_args()

    origen = "linea de comandos"
    if not (a.desde or a.hasta):
        a.desde, a.hasta = FECHA_DESDE, FECHA_HASTA
        a.solo_huecos = SOLO_HUECOS
        origen = "bloque CONFIGURACION del script"

    print("Carga historica spot_price")
    print(f"Inicio: {datetime.now()}")

    headers, db = load_config()
    conn = psycopg2.connect(**db)

    try:
        cols, zonas = descubrir(conn)

        if a.validar:
            validar(conn, cols, zonas)
            return

        desde, hasta = date.fromisoformat(a.desde), date.fromisoformat(a.hasta)
        if hasta < desde:
            p.error("--hasta anterior a --desde")

        if a.solo:
            pedidas = {c.strip() for c in a.solo.split(",")}
            desconocidas = pedidas - set(cols)
            if desconocidas:
                p.error(f"columnas inexistentes: {sorted(desconocidas)}")
            cols = [c for c in cols if c in pedidas]
            zonas = {z: [f for f in fs if col(z, f) in pedidas] for z, fs in zonas.items()}
            zonas = {z: fs for z, fs in zonas.items() if fs}

        print(f"Parametros desde: {origen}")
        print(f"  rango : {desde} -> {hasta}  ({(hasta-desde).days+1} dias)")
        print(f"  modo  : {'solo huecos' if a.solo_huecos else 'RECARGA COMPLETA'}")
        print()
        resumen_esquema(cols, zonas)

        client = None
        if any("entsoe" in fs for fs in zonas.values()):
            from entsoe import EntsoePandasClient
            creds, ruta = leer_credenciales()
            print(f"  credenciales: {ruta}")
            client = EntsoePandasClient(api_key=creds["entsoe_token"])

        # 1. OMIE: un solo fichero por dia da ES y PT
        cols_omie = [col(z, "omie") for z in ZONAS_OMIE if "omie" in zonas.get(z, [])]
        if cols_omie and not _parar:
            cargar_omie(conn, desde, hasta, a.solo_huecos, cols_omie)

        # 2. ESIOS: una serie por zona con geo_id propio
        for z, fs in zonas.items():
            if _parar or "esios" not in fs:
                continue
            c = col(z, "esios")
            cargar_serie(conn, f"ESIOS {NOMBRES.get(z,z)}", c,
                         lambda x, y, g=GEO_ESIOS[z], cc=c: bajar_esios(x, y, g, cc, headers),
                         desde, hasta, a.solo_huecos)

        # 3. ENTSO-E: una serie por zona
        for z, fs in zonas.items():
            if _parar or "entsoe" not in fs:
                continue
            c = col(z, "entsoe")
            cargar_serie(conn, f"ENTSO-E {NOMBRES.get(z,z)}", c,
                         lambda x, y, zz=z, cc=c: bajar_zona(x, y, zz, cc, client),
                         desde, hasta, a.solo_huecos)

        validar(conn, cols, zonas)

    except ErrorEstructural as e:
        print(f"\nERROR ESTRUCTURAL: {e}")
        sys.exit(2)
    finally:
        conn.close()

    print(f"\nFin: {datetime.now()}")


if __name__ == "__main__":
    main()