"""Backfill historico de la PREVISION meteorologica ECMWF a D+1.

PROBLEMA QUE RESUELVE
`ecmwf_forecast_load.py` baja la prevision del IFS desde ECMWF Open Data, que es la
fuente correcta pero solo mantiene una VENTANA MOVIL DE 4 DIAS -- verificado contra el
servidor el 28-ago-2026: los runs del 25-ago en adelante descargan, el del 24-ago ya da
404. Por eso `ecmwf_forecast_agg` solo tiene 176 filas desde el 8-ago-2026. Lo que se
pierde no se recupera por ahi.

Este script rellena hacia atras usando el archivo de Open-Meteo, que re-sirve el MISMO
modelo (ECMWF IFS 0.25 grados) conservando el lead time.

QUE ENDPOINT Y POR QUE
Open-Meteo tiene dos archivos y solo uno vale:

  Historical Forecast API   llega a 2017, pero PEGA las primeras horas de cada run.
                            Eso es casi un analisis, NO una prevision a D+1. Medido
                            sobre junio de 2025 contra la prevision real: el viento a
                            100 m da r = 0,65 y 3,74 km/h de error medio absoluto. Usarlo
                            como si fuera prevision meteria un sesgo optimista. NO SE USA.

  Previous Runs API         devuelve el valor que el modelo predijo N dias antes de la
                            hora valida, a ANTELACION FIJA. Es una prevision de verdad.
                            Es la que se usa aqui (ver LEAD_DIAS para cual y por que).

COBERTURA REAL (medida contra la API mes a mes, 28-ago-2026):
    2020 - 2024-03   inservible -- el archivo del IFS arranca en 2024 y el viento a
                     100 m no esta completo hasta abril (ver INICIO_ARCHIVO)
    2024-04 -> hoy   100 % en todas las variables salvo la racha de viento, que no
                     esta archivada por esta via (ver VARIABLES)
Para 2020-2023 no hay via gratuita: haria falta MARS con licencia o TIGGE (conjunto,
mas grueso). Fabricar esa prevision desde ERA5 seria inventar un pronostico que nadie
emitio, asi que este script simplemente no cubre ese tramo.

COMPARABILIDAD CON ERA5
Se replica el tratamiento de `era5_load.py` para que las dos series sean comparables:
  - mismo dominio: AREA = {north 44, west -9.5, south 36, east 4.5} (peninsula + Baleares)
  - misma agregacion: media aritmetica simple sobre TODAS las celdas, mar incluido
    (ERA5 hace `.mean()` sobre la caja entera, no solo tierra). Open-Meteo sirve puntos
    de mar -- verificado, devuelve elevacion 0.0 y dato completo.
  - mismas unidades: K, m/s, W/m2, fraccion 0-1, mm, Pa.
La unica diferencia deliberada es el paso de rejilla: ERA5 usa 0.25 grados (1.881 puntos)
y aqui se muestrea a 2 grados (40 puntos). Para una MEDIA ESPACIAL la diferencia es
despreciable, y el paso importa porque Open-Meteo no cobra por peticion sino por
LOCALIZACIONES x DIAS: bajar a 1 grado triplica el peso y agota la cuota horaria.

LIMITES DE TASA -- lo que hace fallar esto y como se sobrevive
Open-Meteo tiene un limite por minuto y otro POR HORA, y el segundo no se arregla
esperando: hay que llegar al siguiente reloj en punto. Por eso el script
  1) usa una rejilla ligera por defecto,
  2) distingue los dos 429 y ante el horario duerme hasta la hora siguiente, y
  3) CACHEA CADA TRAMO en cuanto lo termina, en `data/bronze/_cache_ecmwf/`.
Si el proceso muere, se relanza el mismo comando y sigue donde estaba. Nada de lo ya
descargado se vuelve a pedir.

SALIDA
Sin `--a-postgres` solo escribe `data/bronze/ecmwf_forecast_horario.parquet`.

Con `--a-postgres` carga en `ecmwf_forecast_agg`, LA MISMA TABLA QUE EL CRON, y lo hace
TRAMO A TRAMO segun descarga -- no al final. La convencion coincide exactamente: la clave
es (ts, run_date), con `run_date` = dia de la corrida y `ts` = hora valida. Con
`previous_day1`, `run_date = date(ts) - 1 dia`, que es justo lo que escribe el cron.

Dos salvaguardas para que mezclarlas sea seguro:
  - columna `fuente`: las filas que ya existian se etiquetan como del GRIB oficial y las
    nuevas quedan marcadas como Open-Meteo. Se puede deshacer con un DELETE por `fuente`.
  - `ON CONFLICT DO NOTHING`: donde el cron ya escribio, MANDA EL CRON. Su dato viene del
    GRIB nativo y ademas trae `wind_gust10_mean`, que por esta via llega siempre vacia --
    un UPDATE la borraria.

Las filas del cron son trihorarias (8/dia) y las de aqui horarias (24/dia): se completan
entre si, no se estorban.

Uso:
    python ecmwf_forecast_historico.py                # 2024-04-01 -> hoy, a parquet
    python ecmwf_forecast_historico.py                # relanzar tras un corte: reanuda
    python ecmwf_forecast_historico.py --a-postgres   # ademas carga la tabla
    python ecmwf_forecast_historico.py --lead 2       # antelacion conservadora de 48 h
    python ecmwf_forecast_historico.py --paso 1.0     # rejilla mas fina (mas peso)
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
SALIDA = REPO / "data" / "bronze" / "ecmwf_forecast_horario.parquet"

API = "https://previous-runs-api.open-meteo.com/v1/forecast"
MODELO = "ecmwf_ifs025"

# ANTELACION (lead time) POR DEFECTO. `previous_dayN` devuelve el valor que el modelo
# predijo N*24 h antes de la hora valida -- es un desplazamiento RODANTE respecto de la
# hora, no una corrida fija.
#
# Que pasa en produccion: a las ~11:00 del dia D se puja para las 24 horas de D+1 con
# UNA sola corrida, la de D 00Z (publicada sobre las 07:00 UTC). Eso son antelaciones
# de 24 h para la hora 00 de D+1 y de 47 h para la hora 23.
#
# Ninguna opcion de la API reproduce eso exactamente, y las dos se desvian lo mismo:
#
#     hora de D+1   produccion   previous_day1        previous_day2
#     00               24 h      24 h  <- exacto      48 h  (+24)
#     12               36 h      24 h  (-12)          48 h  (+12)
#     23               47 h      24 h  (-23)          48 h  <- casi exacto
#
# Promedio de desviacion: ~11,5 h en las dos, en direcciones opuestas. `day1` peca de
# fresca (optimista), `day2` de rancia (conservadora). Medido contra ERA5 real
# (junio 2025, punto 40.4/-3.7) la diferencia de calidad es pequena:
#
#     variable        day1     day2     day3
#     t2m (C)         3.752    3.973    4.153
#     wind100 (m/s)   1.532    1.599    1.668
#
# Se elige 1 porque es la antelacion que describe la decision real -- el dia D se usa la
# prevision de D+1 -- y porque clava las horas de la manana, que es cuando `day2` mas se
# equivoca. La contaminacion posible afecta solo a las horas tardias y como mucho en 12 h
# de frescura; por franjas horarias `day1` no resulta mejor de forma sistematica (en
# 13-18 sale PEOR que `day2`), asi que esa ventaja es ruido de muestra.
#
# La via exacta seria fijar la corrida con la Single Runs API (`&run=`), pero tambien es
# ventana movil: verificado 28-ago-2026, el run del 27-ago responde y el de junio de 2025
# da "model run is not available". No sirve para el historico.
#
# `--lead 2` queda disponible para repetir el ejercicio con el criterio conservador y
# comprobar que la conclusion del modelo no depende de esta eleccion.
LEAD_DIAS = 1

# Mismo dominio que era5_load.py -- no tocar sin tocar aquel.
AREA = {"north": 44.0, "west": -9.5, "south": 36.0, "east": 4.5}

# La documentacion de Open-Meteo dice "la mayoria de modelos desde enero de 2024", pero
# para ecmwf_ifs025 eso no se sostiene. Medido mes a mes contra la API (28-ago-2026,
# punto 40.4/-3.7, % de horas con dato):
#
#     mes        t2m      wind100
#     2024-01    0.0 %      0.0 %
#     2024-02   89.7 %      0.0 %
#     2024-03  100.0 %     81.5 %
#     2024-04  100.0 %    100.0 %   <- primera cobertura completa
#     ...        100 %      100 %
#
# El viento a 100 m -- la variable que de verdad importa para el precio -- no esta
# completa hasta abril. Se arranca ahi: pedir antes devuelve columnas medio vacias que
# luego hay que imputar, y eso es peor que no tenerlas.
INICIO_ARCHIVO = date(2024, 4, 1)

# variable Open-Meteo -> (columna destino, factor, desplazamiento)
# Las unidades de destino son las de era5_load.py: K, m/s, W/m2, fraccion, mm, Pa.
VARIABLES = {
    "temperature_2m":      ("t2m_mean",          1.0,   273.15),
    "dew_point_2m":        ("d2m_mean",          1.0,   273.15),
    "wind_speed_10m":      ("wind10_mean",       1.0,   0.0),     # se pide en m/s
    "wind_speed_100m":     ("wind100_mean",      1.0,   0.0),
    # `wind_gusts_10m` EXISTE en el run actual pero NO esta archivado en Previous Runs:
    # `wind_gusts_10m_previous_day1` responde 200 y devuelve 24 nulos (verificado
    # 28-ago-2026). Se mantiene la columna para que el esquema calce con el de ERA5 y
    # con `ecmwf_forecast_agg`, pero llegara siempre vacia por esta via. Mismo caso que
    # el `ssrdc` que se retiro de los dos loaders: un canal que nunca puede traer dato
    # real en produccion no debe entrar en ningun tensor.
    "wind_gusts_10m":      ("wind_gust10_mean",  1.0,   0.0),
    "shortwave_radiation": ("ssrd_mean",         1.0,   0.0),     # ya en W/m2
    "cloud_cover":         ("tcc_mean",          0.01,  0.0),     # % -> fraccion
    "precipitation":       ("tp_mean",           1.0,   0.0),     # ya en mm
    "pressure_msl":        ("msl_mean",          100.0, 0.0),     # hPa -> Pa
}

# Open-Meteo pondera cada peticion por LOCALIZACIONES x DIAS, no por numero de llamadas.
# Con 40 puntos x 120 dias cada peticion pesa como cientos de llamadas y se agota el
# limite por minuto en el cuarto tramo (observado 28-ago-2026). Lotes mas pequenos y con
# pausa tardan parecido -- el tiempo se lo comian las esperas de 20-40 s del reintento --
# y no disparan el 429.
# Decimales al guardar. La media espacial en float64 arrastra ruido de representacion
# (288.28249999999997) que no significa nada: la precision fisica del dato es mucho mas
# gruesa. Se redondea, pero NO todo por igual -- medido sobre las 21.128 filas:
#
#     variable                 error a 2 dec   error a 4 dec
#     t2m, d2m, msl, ssrd          <0,001 %        0,000 %
#     wind10, wind100          0,04 - 0,06 %       0,000 %
#     tcc                            0,72 %        0,007 %
#     tp                             2,76 %        0,000 %
#
# `tp` es el caso critico: a 2 decimales, 6.410 celdas se irian a 0,00 cuando solo 4.059
# son cero de verdad -- 2.351 horas de lluvia ligera se perderian. Y `tcc`, al vivir en
# [0,1], pasaria de 3.647 valores distintos a 98. Esas dos van a 4 decimales.
DECIMALES = {"tcc_mean": 4, "tp_mean": 4}
DECIMALES_POR_DEFECTO = 2

PUNTOS_POR_LOTE = 25
DIAS_POR_LOTE = 14
PAUSA_S = 3.0             # cortesia con la API publica
REINTENTOS = 6
ESPERA_429_S = 65         # el limite de Open-Meteo es por minuto: esperar el minuto entero


def rejilla(paso: float):
    """Puntos del dominio ERA5 muestreados cada `paso` grados, mar incluido."""
    lats = np.arange(AREA["south"], AREA["north"] + 1e-9, paso)
    lons = np.arange(AREA["west"], AREA["east"] + 1e-9, paso)
    return [(round(float(la), 4), round(float(lo), 4)) for la in lats for lo in lons]


def _pedir(url: str, reintentos: int = REINTENTOS):
    for intento in range(reintentos):
        try:
            with urllib.request.urlopen(url, timeout=180) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            cuerpo = e.read().decode()[:300]
            if e.code == 429 and intento < reintentos - 1:
                # Open-Meteo tiene DOS limites y no se tratan igual:
                #   por minuto -> basta esperar el minuto entero
                #   por HORA   -> hay que esperar al siguiente reloj en punto; dormir 60 s
                #                 solo consume reintentos contra una puerta cerrada
                #                 ("Hourly API request limit exceeded", visto 28-ago-2026)
                por_hora = "hourly" in cuerpo.lower()
                cabecera = e.headers.get("Retry-After") if e.headers else None
                if (cabecera or "").isdigit():
                    espera = int(cabecera)
                elif por_hora:
                    ahora = datetime.now()
                    proxima = (ahora + timedelta(hours=1)).replace(
                        minute=0, second=30, microsecond=0)
                    espera = max(60, int((proxima - ahora).total_seconds()))
                else:
                    espera = ESPERA_429_S
                tipo = "POR HORA" if por_hora else "por minuto"
                print(f"    limite de tasa {tipo}: esperando {espera}s "
                      f"(lo descargado ya esta en cache, no se pierde)")
                time.sleep(espera)
                continue
            raise RuntimeError(f"HTTP {e.code}: {cuerpo}") from None
        except Exception:
            if intento < reintentos - 1:
                time.sleep(5 * (intento + 1))
                continue
            raise
    raise RuntimeError("reintentos agotados")


def _tramos(desde: date, hasta: date, dias: int):
    ini = desde
    while ini <= hasta:
        fin = min(ini + timedelta(days=dias - 1), hasta)
        yield ini, fin
        ini = fin + timedelta(days=1)


def _agregar_tramo(ini, fin, lotes_p, pedidas, sufijo, lead, etiqueta, verbose):
    """Descarga un tramo de fechas y devuelve su media espacial horaria."""
    suma, cuenta = {}, {}
    for li, lote in enumerate(lotes_p, 1):
        lat = ",".join(str(p[0]) for p in lote)
        lon = ",".join(str(p[1]) for p in lote)
        url = (f"{API}?latitude={lat}&longitude={lon}"
               f"&hourly={','.join(pedidas)}"
               f"&start_date={ini}&end_date={fin}"
               f"&models={MODELO}&timezone=UTC&wind_speed_unit=ms")
        datos = _pedir(url)
        if isinstance(datos, dict):
            datos = [datos]
        for d in datos:
            h = d["hourly"]
            ts = h["time"]
            for var, (col, _, _) in VARIABLES.items():
                serie = h.get(f"{var}{sufijo}")
                if serie is None:
                    continue
                sm = suma.setdefault(col, {})
                ct = cuenta.setdefault(col, {})
                for t, v in zip(ts, serie):
                    if v is None:
                        continue
                    sm[t] = sm.get(t, 0.0) + v
                    ct[t] = ct.get(t, 0) + 1
        if verbose:
            print(f"  {etiqueta} lote {li}/{len(lotes_p)}", flush=True)
        time.sleep(PAUSA_S)

    horas = sorted({t for c in cuenta.values() for t in c})
    out = pd.DataFrame({"ts": pd.to_datetime(horas, utc=True)})
    for var, (col, factor, desplaz) in VARIABLES.items():
        sm, ct = suma.get(col, {}), cuenta.get(col, {})
        out[col] = [(sm[t] / ct[t]) * factor + desplaz if ct.get(t) else np.nan for t in horas]
    for _, (col, _, _) in VARIABLES.items():
        if col in out.columns:
            out[col] = out[col].round(DECIMALES.get(col, DECIMALES_POR_DEFECTO))

    out["fuente"] = f"open-meteo/previous-runs · {MODELO} · previous_day{lead}"
    out["lead_h"] = lead * 24
    return out


def descargar(desde: date, hasta: date, paso: float, lead: int = LEAD_DIAS,
              cache: Path = None, con=None, verbose: bool = True) -> pd.DataFrame:
    """Media espacial horaria de la prevision, sobre el dominio ERA5.

    INCREMENTAL Y REANUDABLE. Cada tramo, en cuanto termina:
      1. se guarda en `cache` (parquet suelto), y
      2. si se paso una conexion `con`, se inserta en Postgres en ese momento.
    No se espera a tener el historico entero. Con los limites de tasa de la API el
    proceso se cae a media descarga, y asi lo ya bajado queda en firme en los dos
    sitios; relanzar el mismo comando retoma donde estaba sin volver a pedir nada.
    """
    puntos = rejilla(paso)
    sufijo = f"_previous_day{lead}"
    pedidas = [f"{v}{sufijo}" for v in VARIABLES]
    lotes_p = [puntos[i:i + PUNTOS_POR_LOTE] for i in range(0, len(puntos), PUNTOS_POR_LOTE)]
    tramos = list(_tramos(desde, hasta, DIAS_POR_LOTE))

    cache = Path(cache) if cache else (REPO / "data" / "bronze" / "_cache_ecmwf")
    cache.mkdir(parents=True, exist_ok=True)

    # Que dias tiene YA la base por esta via. La cache de ficheros no basta como
    # registro: un tramo puede estar cargado en Postgres y no tener parquet (se bajo
    # con otro `--cache`, o se limpio el directorio). Preguntar a la base evita volver
    # a pedir a la API dias que ya estan, que es lo unico que de verdad cuesta.
    dias_en_bd = set()
    if con is not None:
        with con.cursor() as cur:
            cur.execute(f"SELECT DISTINCT ts::date FROM {TABLA} WHERE fuente LIKE %s",
                        ("open-meteo%",))
            dias_en_bd = {r[0] for r in cur.fetchall()}
        if verbose and dias_en_bd:
            print(f"Ya en `{TABLA}`: {len(dias_en_bd)} dias; esos tramos se omiten")

    if verbose:
        print(f"Dominio {AREA} · paso {paso}° -> {len(puntos)} puntos")
        print(f"Antelacion: previous_day{lead} ({lead * 24} h antes de la hora valida)")
        print(f"Rango {desde} -> {hasta} · {len(tramos)} tramos x {len(lotes_p)} lotes "
              f"= {len(tramos) * len(lotes_p)} peticiones")
        print(f"Cache reanudable: {cache}")

    piezas, t0, nuevas = [], time.time(), 0
    for ti, (ini, fin) in enumerate(tramos, 1):
        destino = cache / f"tramo_{ini}_{fin}_d{lead}_p{paso}.parquet"
        dias_tramo = {(ini + timedelta(days=k)) for k in range((fin - ini).days + 1)}
        if dias_tramo <= dias_en_bd and not destino.exists():
            # Todo el tramo esta ya en la base y no hay parquet que leer: no se pide
            # nada a la API. Ese tramo no entrara en el parquet de salida de esta
            # corrida, pero el dato vive en Postgres, que es donde importa.
            if verbose:
                print(f"  tramo {ti}/{len(tramos)} ({ini}->{fin}) ya en la base, se omite")
            continue
        if destino.exists():
            parte = pd.read_parquet(destino)
            piezas.append(parte)
            # Aunque el tramo estuviera cacheado, puede no haber llegado a la base
            # (p. ej. la primera corrida se hizo sin --a-postgres). Se reintenta:
            # el ON CONFLICT lo hace idempotente.
            if con is not None:
                nuevas += a_postgres(con, parte, verbose=False)
            if verbose:
                print(f"  tramo {ti}/{len(tramos)} ({ini}->{fin}) ya en cache, se omite")
            continue
        etiqueta = f"tramo {ti}/{len(tramos)} ({ini}->{fin})"
        parte = _agregar_tramo(ini, fin, lotes_p, pedidas, sufijo, lead, etiqueta, verbose)
        parte.to_parquet(destino, index=False)
        piezas.append(parte)
        if verbose:
            print(f"  {etiqueta} OK · {len(parte):,} horas · {time.time() - t0:.0f}s")
        if con is not None:
            nuevas += a_postgres(con, parte, verbose=verbose)

    if verbose:
        print(f"Descarga completa en {time.time() - t0:.0f}s")
        if con is not None:
            print(f"Insertadas en {TABLA}: {nuevas:,} filas nuevas")
    if not piezas:
        # Todo estaba ya en la base: no es un error, es que no habia nada que hacer.
        cols = ["ts"] + [c for _, (c, _, _) in VARIABLES.items()] + ["fuente", "lead_h"]
        return pd.DataFrame(columns=cols)
    return (pd.concat(piezas, ignore_index=True)
              .drop_duplicates(subset="ts", keep="last")
              .sort_values("ts").reset_index(drop=True))


TABLA = "ecmwf_forecast_agg"
FUENTE_CRON = "ecmwf-opendata (GRIB oficial)"


def conexion():
    sys.path.append(str(Path(__file__).parent))
    from config import load_config
    import psycopg2
    _, db = load_config()
    return psycopg2.connect(**db)


def preparar_tabla(con):
    """Anade la columna de procedencia a la tabla del cron, sin tocar sus datos.

    Se carga en `ecmwf_forecast_agg`, la MISMA que alimenta el cron, porque la
    convencion coincide exactamente: su clave es (ts, run_date) con `run_date` = dia de
    la corrida y `ts` = hora valida de D+1. Con `previous_day1` el run_date de una fila
    es `date(ts) - 1 dia`, que es justo lo que escribe el cron.

    Lo unico que hacia falta para poder mezclarlas era saber de donde viene cada fila:
    las del cron salen del GRIB oficial de ECMWF y las de aqui de Open-Meteo, que
    re-sirve el mismo modelo. La columna `fuente` lo deja por escrito; las que ya
    existian se etiquetan como del cron.
    """
    with con.cursor() as cur:
        cur.execute(f"ALTER TABLE {TABLA} ADD COLUMN IF NOT EXISTS fuente TEXT")
        cur.execute(f"UPDATE {TABLA} SET fuente = %s WHERE fuente IS NULL", (FUENTE_CRON,))
    con.commit()


def a_postgres(con, df: pd.DataFrame, verbose: bool = True) -> int:
    """Inserta un tramo. Las filas del GRIB oficial NUNCA se pisan.

    `ON CONFLICT DO NOTHING` y no `DO UPDATE` a proposito: donde el cron ya escribio,
    su dato manda. Viene del GRIB nativo y ademas trae `wind_gust10_mean`, que por esta
    via llega siempre vacia -- un UPDATE la borraria.
    """
    from psycopg2.extras import execute_values

    cols = [c for _, (c, _, _) in VARIABLES.items()]
    d = df.copy()
    # La tabla usa TIMESTAMP sin zona (UTC implicito) y una fecha de corrida.
    ts_utc = pd.to_datetime(d["ts"], utc=True)
    d["ts"] = ts_utc.dt.tz_localize(None)
    d["run_date"] = (ts_utc - pd.to_timedelta(d["lead_h"], unit="h")).dt.date

    filas = [tuple(None if pd.isna(v) else v for v in r)
             for r in d[["ts", "run_date"] + cols + ["fuente"]].to_numpy()]
    with con.cursor() as cur:
        execute_values(cur, f"""
            INSERT INTO {TABLA} (ts, run_date, {', '.join(cols)}, fuente)
            VALUES %s ON CONFLICT (ts, run_date) DO NOTHING
        """, filas, page_size=2000)
        insertadas = cur.rowcount
    con.commit()
    if verbose:
        print(f"      -> Postgres: {insertadas:,} filas nuevas de {len(filas):,} "
              f"({len(filas) - insertadas:,} ya estaban)")
    return insertadas


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--desde", default=str(INICIO_ARCHIVO))
    ap.add_argument("--hasta", default=str(date.today()))
    ap.add_argument("--paso", type=float, default=2.0,
                    help="grados entre puntos de la rejilla (2.0 = 40 puntos; "
                         "bajarlo multiplica el peso ante la API)")
    ap.add_argument("--lead", type=int, default=LEAD_DIAS, choices=[1, 2, 3],
                    help="dias de antelacion; 2 es el seguro (ver nota en LEAD_DIAS)")
    ap.add_argument("--a-postgres", action="store_true")
    ap.add_argument("--salida", default=str(SALIDA))
    ap.add_argument("--cache", default=None,
                    help="directorio de tramos ya descargados (reanudacion)")
    a = ap.parse_args()

    desde = date.fromisoformat(a.desde)
    if desde < INICIO_ARCHIVO:
        print(f"AVISO: el archivo de Previous Runs empieza el {INICIO_ARCHIVO}. "
              f"Se recorta desde ahi (pediste {desde}).")
        desde = INICIO_ARCHIVO

    con = None
    if a.a_postgres:
        con = conexion()
        preparar_tabla(con)
        print(f"Postgres: cargando en `{TABLA}` tramo a tramo, segun se descarga.")
    try:
        df = descargar(desde, date.fromisoformat(a.hasta), a.paso, a.lead,
                       cache=Path(a.cache) if a.cache else None, con=con)
    finally:
        if con is not None:
            con.close()

    if df.empty:
        print()
        print("Nada nuevo que descargar: el rango pedido ya estaba completo.")
        raise SystemExit(0)

    ruta = Path(a.salida)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(ruta, index=False)
    # `--salida` puede apuntar fuera del repo (pruebas), asi que relative_to no vale.
    mostrada = ruta.relative_to(REPO) if ruta.is_relative_to(REPO) else ruta
    print(f"\nGuardado -> {mostrada}  ({len(df):,} horas x {df.shape[1]} columnas)")
    print(f"Rango: {df['ts'].min()} -> {df['ts'].max()}")
    cob = df.drop(columns=["ts", "fuente", "lead_h"]).notna().mean() * 100
    print("Cobertura por variable:")
    for c, v in cob.items():
        nota = "  <- no archivada en Previous Runs, se espera 0%" if c == "wind_gust10_mean" else ""
        print(f"  {c:20s} {v:5.1f}%{nota}")
    vacias = [c for c, v in cob.items() if v == 0 and c != "wind_gust10_mean"]
    if vacias:
        print(f"\nAVISO: columnas vacias no esperadas: {vacias}")


