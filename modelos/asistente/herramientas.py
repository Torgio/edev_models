r"""
TFM Energia UCM - Herramientas deterministas para el asistente LLM (30-ago-2026)

Estas funciones NO usan ningun modelo de lenguaje -- son la capa de calculo real que el LLM va a
invocar (patron "tool use" / function calling, no RAG documental). El LLM solo entiende la
pregunta en lenguaje natural, elige que funcion llamar y con que parametros, y redacta la
respuesta a partir de lo que estas funciones devuelven. Los numeros siempre salen de aqui, nunca
los inventa el modelo de lenguaje.

Distincion importante, para que el asistente nunca la confunda:
  - `prediccion_d_mas_1()`   -> UNICO horizonte real de prediccion (mañana). Sale del modelo
                                entrenado. Es una PREDICCION.
  - `precio_historico_*()`   -> cualquier otro horizonte (semana que viene, marzo, 2027...) se
                                responde con patrones REALES YA OCURRIDOS (percentiles por hora/
                                mes/dia de la semana), nunca como si fuera una prediccion del
                                modelo. Es una REFERENCIA HISTORICA, y hay que decirlo asi.

Uso:
    from modelos.asistente.herramientas import (
        precio_historico_percentiles, precio_historico_serie,
        prediccion_d_mas_1, simular_bateria,
    )
"""

import re
import sys
import warnings
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2

warnings.filterwarnings("ignore")

REPO = Path(__file__).parent.parent.parent
sys.path.append(str(REPO / "ingesta"))
sys.path.append(str(REPO / "modelos"))


def _conectar():
    from config import load_config
    _, db_config = load_config()
    return psycopg2.connect(**db_config)


def precio_historico_percentiles(hora: int | None = None, mes: int | None = None,
                                  dia_semana: int | None = None,
                                  anio_desde: int | None = None, anio_hasta: int | None = None) -> dict:
    """Percentiles del precio REAL historico, filtrado por hora/mes/dia de la semana.

    Es la herramienta de "extrapolacion" honesta: no predice nada, describe como se ha
    comportado el precio en circunstancias parecidas en el pasado. `dia_semana`: 0=lunes,
    6=domingo (convencion de pandas .dayofweek). Si no se filtra nada, describe todo el
    historico disponible.

    Devuelve un dict con n_horas, media, p10, p25, p50 (mediana), p75, p90, min, max -- y un
    campo `etiqueta` que dice explicitamente que esto es referencia historica, no prediccion,
    para que el LLM lo traslade asi a la respuesta.
    """
    conn = _conectar()
    try:
        df = pd.read_sql("SELECT datetime, es_esios FROM spot_price WHERE es_esios IS NOT NULL", conn)
    finally:
        conn.close()
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True).dt.tz_convert("Europe/Madrid")

    mask = pd.Series(True, index=df.index)
    if hora is not None:
        mask &= df["datetime"].dt.hour == hora
    if mes is not None:
        mask &= df["datetime"].dt.month == mes
    if dia_semana is not None:
        mask &= df["datetime"].dt.dayofweek == dia_semana
    if anio_desde is not None:
        mask &= df["datetime"].dt.year >= anio_desde
    if anio_hasta is not None:
        mask &= df["datetime"].dt.year <= anio_hasta

    serie = df.loc[mask, "es_esios"]
    if len(serie) == 0:
        return {"error": "No hay horas historicas que cumplan ese filtro."}

    return {
        "etiqueta": "REFERENCIA HISTORICA (patron ya ocurrido, no es una prediccion del modelo)",
        "filtro": {"hora": hora, "mes": mes, "dia_semana": dia_semana,
                   "anio_desde": anio_desde, "anio_hasta": anio_hasta},
        "n_horas": int(len(serie)),
        "media_eur_mwh": round(float(serie.mean()), 2),
        "p10": round(float(serie.quantile(0.10)), 2),
        "p25": round(float(serie.quantile(0.25)), 2),
        "p50_mediana": round(float(serie.quantile(0.50)), 2),
        "p75": round(float(serie.quantile(0.75)), 2),
        "p90": round(float(serie.quantile(0.90)), 2),
        "min": round(float(serie.min()), 2),
        "max": round(float(serie.max()), 2),
    }


def precio_historico_serie(desde: str, hasta: str) -> pd.DataFrame:
    """Serie horaria de precio REAL entre dos fechas (ambas inclusive, dia natural completo en
    hora de Madrid). Para backtests y graficas -- no para fechas futuras (usar prediccion_d_mas_1
    para eso, solo cubre mañana).

    BUG ENCONTRADO Y CORREGIDO (31-ago-2026): un `BETWEEN %(desde)s AND %(hasta)s` sobre fechas
    sin hora compara contra la MEDIANOCHE de `hasta`, no contra el final de ese dia -- asi que el
    dia `hasta` completo se perdia (y una consulta de un solo dia, `desde == hasta`, devolvia
    practicamente 0 filas). Se detecto porque una pregunta tan simple como "la tabla de precios
    de HOY" no se podia responder aunque el dato SI estuviera en la base. Corregido con un limite
    superior explicito de "hasta + 1 dia", en hora de Madrid (no UTC, para que "hoy" signifique
    el dia natural peninsular, igual que el resto de herramientas de este modulo)."""
    conn = _conectar()
    try:
        df = pd.read_sql(
            "SELECT datetime, es_esios AS precio FROM spot_price "
            "WHERE datetime >= (%(desde)s::date)::timestamp AT TIME ZONE 'Europe/Madrid' "
            "AND datetime < (%(hasta)s::date + INTERVAL '1 day')::timestamp AT TIME ZONE 'Europe/Madrid' "
            "ORDER BY datetime",
            conn, params={"desde": desde, "hasta": hasta},
        )
    finally:
        conn.close()
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    return df


def precio_tabla_horaria(desde: str, hasta: str) -> dict:
    """Tabla de precio REAL, hora a hora, sin resumir -- para cuando piden VER los precios en
    crudo ("los precios de hoy por hora", "la evolucion de esta semana", "el precio de ayer"),
    en vez de un resumen estadistico. Es el hueco que faltaba: antes de esta funcion, una
    pregunta tan simple como "una tabla con los precios de hoy" no se podia responder aunque el
    dato SI estuviera en la base -- solo existian herramientas de resumen (percentiles) o de
    filtro (solo negativas), ninguna que devolviera el detalle completo de un rango corto.

    No es para fechas futuras -- la unica prediccion real es `prediccion_d_mas_1` (mañana). Si
    el rango pedido incluye hoy o el futuro, esta funcion solo trae las horas YA publicadas.

    Args:
        desde: Fecha de inicio, YYYY-MM-DD (dia natural completo en hora de Madrid).
        hasta: Fecha de fin, YYYY-MM-DD (inclusive). Para "hoy", usa la misma fecha en desde y
            hasta.
    """
    df = precio_historico_serie(desde, hasta)
    if df.empty:
        return {"error": f"No hay precio real publicado todavia entre {desde} y {hasta}."}
    if len(df) > 500:
        return {"error": f"El rango pedido tiene {len(df)} horas -- demasiadas para tabular en "
                          f"detalle (limite 500, unas 3 semanas). Usa un rango mas corto, o "
                          f"precio_historico_percentiles para un resumen de un periodo largo."}

    local = df["datetime"].dt.tz_convert("Europe/Madrid")
    filas = [{"dia": str(t.date()), "hora": int(t.hour), "precio_eur_mwh": round(float(p), 2)}
             for t, p in zip(local, df["precio"])]
    return {
        "etiqueta": "REFERENCIA HISTORICA (precio real ya ocurrido, no es una prediccion)",
        "desde": desde, "hasta": hasta,
        "horas_devueltas": len(filas),
        "horas": filas,
    }


def precio_tendencia_mensual(desde: str, hasta: str) -> dict:
    """Evolucion del precio medio MES A MES en un rango -- para ver la TENDENCIA a lo largo
    del tiempo (sube/baja, estacionalidad), no un solo numero resumen ni el detalle hora a
    hora. Usa esta herramienta cuando la pregunta sea sobre como ha evolucionado el precio en
    un periodo largo (un año o mas) -- ej. "precio medio en 2026" se responde mejor con esto
    (mes a mes) que solo con el numero agregado de `precio_historico_percentiles`, porque deja
    ver si el año fue estable o tuvo meses muy distintos entre si.

    Se añadio porque una prueba real mostro el hueco: preguntar "el precio medio en 2026" solo
    devolvia un numero agregado, sin dar pie a ver la evolucion -- ni en tabla ni en grafica.

    Args:
        desde: Fecha de inicio, YYYY-MM-DD.
        hasta: Fecha de fin, YYYY-MM-DD.
    """
    df = precio_historico_serie(desde, hasta)
    if df.empty:
        return {"error": f"No hay precio real publicado entre {desde} y {hasta}."}

    local = df["datetime"].dt.tz_convert("Europe/Madrid")
    df = df.assign(mes=local.dt.to_period("M").astype(str))
    resumen = df.groupby("mes")["precio"].agg(media="mean", mediana="median",
                                                minimo="min", maximo="max", horas="count").round(2)
    return {
        "etiqueta": "REFERENCIA HISTORICA (precio real ya ocurrido, no es una prediccion)",
        "desde": desde, "hasta": hasta,
        "por_mes": [
            {"mes": m, "precio_medio_eur_mwh": round(float(r["media"]), 2),
             "mediana_eur_mwh": round(float(r["mediana"]), 2),
             "minimo_eur_mwh": round(float(r["minimo"]), 2),
             "maximo_eur_mwh": round(float(r["maximo"]), 2), "horas": int(r["horas"])}
            for m, r in resumen.iterrows()
        ],
    }


def precio_negativos(anio: int | None = None) -> dict:
    """Cuenta horas de precio negativo y el minimo (mas negativo) del año -- REFERENCIA
    HISTORICA, sobre precio real ya ocurrido. Si `anio` es None, usa el año en curso.

    El precio spot español SI puede ser negativo (excedente de renovables sin demanda que lo
    absorba) -- no es un error de datos, es una condicion real de mercado.
    """
    anio = anio or pd.Timestamp.now(tz="Europe/Madrid").year
    conn = _conectar()
    try:
        df = pd.read_sql(
            "SELECT datetime, es_esios FROM spot_price "
            "WHERE EXTRACT(YEAR FROM datetime AT TIME ZONE 'Europe/Madrid') = %(anio)s "
            "AND es_esios IS NOT NULL",
            conn, params={"anio": anio},
        )
    finally:
        conn.close()

    if df.empty:
        return {"error": f"No hay datos para el año {anio}."}

    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    negativos = df[df["es_esios"] < 0]
    total_horas = len(df)

    resultado = {
        "etiqueta": "REFERENCIA HISTORICA (precio real ya ocurrido, no es una prediccion)",
        "anio": anio,
        "horas_con_dato": total_horas,
        "horas_con_precio_negativo": int(len(negativos)),
        "porcentaje_horas_negativas": round(100 * len(negativos) / total_horas, 2),
    }
    if len(negativos) > 0:
        fila_min = negativos.loc[negativos["es_esios"].idxmin()]
        resultado["precio_minimo_eur_mwh"] = round(float(fila_min["es_esios"]), 2)
        resultado["fecha_hora_minimo"] = str(fila_min["datetime"])
    else:
        resultado["precio_minimo_eur_mwh"] = None
        resultado["nota"] = f"Ninguna hora con precio negativo en {anio} (hasta la fecha disponible)."
    return resultado


def precio_horas_negativas(anio: int | None = None, limite: int = 100) -> dict:
    """Lista detallada de las horas de precio NEGATIVO de un año, dia a dia -- REFERENCIA
    HISTORICA sobre precio real ya ocurrido. Complementa a `precio_negativos` (que solo da el
    conteo total y el minimo del año): esta devuelve cada hora individual (dia, hora, precio),
    ordenada de mas negativa a menos, para que se pueda presentar como tabla o graficar.

    Antes de existir esta funcion, una pregunta como "lista las horas con los precios mas
    negativos de 2026" no se podia responder aunque el dato SI estuviera en la base de datos --
    `precio_negativos` solo trae el minimo absoluto, no el detalle. Esta funcion cierra ese hueco.

    Args:
        anio: Año a consultar. Omitir para usar el año en curso.
        limite: Cuantas horas devolver como maximo (de mas a menos negativa). Por defecto 100;
            un año completo puede tener varios cientos de horas negativas (ej. 681 en 2026), asi
            que subir el limite mucho encarece el contexto -- 100-200 ya cubre sobradamente
            cualquier pregunta razonable de "las mas negativas" o "los peores dias".
    """
    anio = anio or pd.Timestamp.now(tz="Europe/Madrid").year
    limite = max(1, min(limite, 500))  # tope duro para no disparar el contexto del LLM
    conn = _conectar()
    try:
        df = pd.read_sql(
            "SELECT datetime, es_esios FROM spot_price "
            "WHERE EXTRACT(YEAR FROM datetime AT TIME ZONE 'Europe/Madrid') = %(anio)s "
            "AND es_esios IS NOT NULL AND es_esios < 0 "
            "ORDER BY es_esios ASC LIMIT %(lim)s",
            conn, params={"anio": anio, "lim": limite},
        )
    finally:
        conn.close()

    if df.empty:
        return {"etiqueta": "REFERENCIA HISTORICA (precio real ya ocurrido, no es una prediccion)",
                "anio": anio, "horas_negativas": [],
                "nota": f"Ninguna hora con precio negativo en {anio} (hasta la fecha disponible)."}

    local = pd.to_datetime(df["datetime"], utc=True).dt.tz_convert("Europe/Madrid")
    filas = [{"dia": str(t.date()), "hora": int(t.hour), "precio_eur_mwh": round(float(p), 2)}
             for t, p in zip(local, df["es_esios"])]
    return {
        "etiqueta": "REFERENCIA HISTORICA (precio real ya ocurrido, no es una prediccion) -- "
                    "ordenado de mas negativo a menos, no cronologicamente",
        "anio": anio,
        "horas_mostradas": len(filas),
        "limite_aplicado": limite,
        "aviso_si_se_pide_el_total": "usa precio_negativos(anio) para el conteo TOTAL real de "
                                      "horas negativas del año -- esta lista puede estar truncada "
                                      "por `limite`.",
        "horas_negativas": filas,
    }


def prediccion_d_mas_1() -> dict:
    """La prediccion real del modelo para el dia siguiente (D+1) -- el UNICO horizonte donde el
    proyecto predice de verdad, no extrapola.

    LIMITACION CONOCIDA, PROBADA Y NO OCULTADA: `construir_dataset_horario()` usa
    `DATASET_END`, una constante FIJA (hoy "2026-08-15") que el equipo congelo a proposito para
    que la comparacion de matrices sea reproducible -- no avanza sola con el calendario. Por eso
    esta funcion, tal cual esta, da la prediccion para el dia siguiente a esa fecha congelada, NO
    para "mañana" en sentido literal. Es exactamente la pieza que el propio equipo tiene listada
    como pendiente ("Features de D+1 desde Postgres", P1 en tareas pendientes 29-ago) -- no se
    resuelve aqui tocando DATASET_END (romperia la comparacion de matrices de todo el equipo,
    justo el dia que se elige el modelo principal). Se avisa en voz alta en vez de fingir que
    funciona: `fecha_objetivo` siempre se compara contra la fecha real de hoy, y si no coincide
    con "mañana", el campo `advertencia` lo dice explicitamente.
    """
    import joblib
    from construir_dataset_horario import construir_dataset_horario

    art = joblib.load(REPO / "modelos" / "artefactos" / "lightgbm_horario_final.joblib")
    modelo, feature_cols, medianas = art["modelo"], art["feature_cols"], art["medianas"]

    dataset = construir_dataset_horario(pdbc="lag")
    ultimo_dia = dataset.index.max().normalize()
    fila_manana = dataset.loc[dataset.index >= ultimo_dia]
    if fila_manana.empty:
        return {"error": "No hay datos suficientes para construir la prediccion."}

    X = fila_manana[feature_cols].fillna(medianas)
    pred = modelo.predict(X)
    fecha_objetivo = fila_manana.index.min().date()
    manana_real = pd.Timestamp.now(tz="Europe/Madrid").date() + pd.Timedelta(days=1)

    resultado = {
        "etiqueta": "PREDICCION DEL MODELO (D+1, unico horizonte real de prediccion del proyecto)",
        "fecha_objetivo": str(fecha_objetivo),
        "horas": [
            {"hora_utc": str(ts), "precio_pred": round(float(p), 2)}
            for ts, p in zip(fila_manana.index, pred)
        ],
    }
    if fecha_objetivo != manana_real:
        resultado["advertencia"] = (
            f"Esta NO es la prediccion de mañana ({manana_real}) -- el dataset esta acotado a "
            f"DATASET_END=2026-08-15 (constante fija del equipo para comparar matrices) y esta "
            f"prediccion es para {fecha_objetivo}, el dia siguiente a esa fecha congelada. "
            f"Falta la pieza de produccion real (features de D+1 desde Postgres, ya identificada "
            f"como pendiente por el equipo) para que esto sea la prediccion de mañana de verdad."
        )
    return resultado


def simular_bateria(potencia_mw: float, capacidad_mwh: float, eficiencia: float,
                     desde: str, hasta: str) -> dict:
    """Generaliza simulador_bess_horario.py: parametros de bateria a eleccion del usuario, sobre
    precio REAL historico (backtest, un ciclo de carga/descarga por dia). Devuelve el ingreso
    total del periodo y el % capturado frente al limite teorico (oraculo)."""
    duracion_h = capacidad_mwh / potencia_mw
    if duracion_h < 0.5 or duracion_h > 12:
        return {"error": f"Duracion implicita ({duracion_h:.1f}h) fuera de un rango razonable "
                          f"para este simulador (0.5-12h). Revisa potencia/capacidad."}
    duracion_h_entera = max(1, round(duracion_h))

    df = precio_historico_serie(desde, hasta)
    df["fecha_local"] = df["datetime"].dt.tz_convert("Europe/Madrid").dt.date

    ingresos = []
    for fecha, grupo in df.groupby("fecha_local"):
        real = grupo.sort_values("datetime")["precio"].values
        if len(real) < 24:
            continue
        orden = np.argsort(real)
        h_carga, h_descarga = orden[:duracion_h_entera], orden[-duracion_h_entera:]
        if set(h_carga) & set(h_descarga):
            continue
        coste = real[h_carga].sum() * potencia_mw
        ingreso = real[h_descarga].sum() * potencia_mw * eficiencia
        ingresos.append(ingreso - coste)

    if not ingresos:
        return {"error": "No hay dias completos en ese rango de fechas."}

    dias = len(ingresos)
    total = float(np.sum(ingresos))
    return {
        "etiqueta": "SIMULACION SOBRE PRECIO REAL (backtest historico, no es una proyeccion a futuro)",
        "parametros": {"potencia_mw": potencia_mw, "capacidad_mwh": capacidad_mwh,
                       "eficiencia": eficiencia, "duracion_h_usada": duracion_h_entera,
                       "desde": desde, "hasta": hasta},
        "dias_simulados": dias,
        "ingreso_total_eur": round(total, 2),
        "ingreso_medio_diario_eur": round(total / dias, 2),
        "ingreso_anualizado_eur": round(total / dias * 365, 2),
    }


def simular_autoconsumo_solar(potencia_solar_kwp: float, potencia_bateria_mw: float,
                               capacidad_bateria_mwh: float, eficiencia_bateria: float,
                               consumo_anual_mwh: float, desde: str, hasta: str) -> dict:
    """Simula el ahorro de instalar paneles solares + bateria frente a comprar toda la energia
    a precio de mercado, para una empresa. VERSION 1 -- ver `limitaciones` en la respuesta para
    que se pueda comunicar con precision que falta mejorar.

    Logica, hora a hora: la generacion solar cubre primero el consumo directo; el excedente
    carga la bateria; si falta generacion, la bateria descarga; lo que sigue faltando se compra a
    precio real de mercado; el excedente que no cabe en la bateria se vende a precio de mercado.

    Simplificaciones de esta primera version (documentadas, no ocultas):
      - Perfil de consumo PLANO (el mismo consumo cada hora) -- una empresa real tiene forma
        horaria/semanal/estacional. Lo correcto es que el cliente aporte su propia curva.
      - Generacion solar estimada de forma simple: potencia instalada x (radiacion real /
        1000 W/m², la condicion estandar de fabrica de un panel) -- sin perdidas por
        temperatura, orientacion/inclinacion del panel, ni eficiencia del inversor.
      - Sin degradacion de bateria ni costes de operacion/mantenimiento (mismo criterio que
        simular_bateria).
    """
    conn = _conectar()
    try:
        # mismo bug de limite superior que precio_historico_serie (ver su docstring) -- corregido
        # igual aqui: "hasta" incluye el dia entero, no solo su medianoche.
        df_precio = pd.read_sql(
            "SELECT datetime, es_esios AS precio FROM spot_price "
            "WHERE datetime >= (%(desde)s::date)::timestamp AT TIME ZONE 'Europe/Madrid' "
            "AND datetime < (%(hasta)s::date + INTERVAL '1 day')::timestamp AT TIME ZONE 'Europe/Madrid' "
            "ORDER BY datetime",
            conn, params={"desde": desde, "hasta": hasta})
        df_sol = pd.read_sql(
            "SELECT ts, ssrd_mean FROM era5_weather_agg "
            "WHERE ts >= %(desde)s::date AND ts < (%(hasta)s::date + INTERVAL '1 day') "
            "ORDER BY ts",
            conn, params={"desde": desde, "hasta": hasta})
    finally:
        conn.close()

    df_precio["datetime"] = pd.to_datetime(df_precio["datetime"], utc=True)
    df_sol["ts"] = pd.to_datetime(df_sol["ts"], utc=True)

    idx = pd.date_range(df_precio["datetime"].min(), df_precio["datetime"].max(), freq="h", tz="UTC")
    precio = df_precio.set_index("datetime")["precio"].reindex(idx)
    radiacion = df_sol.set_index("ts")["ssrd_mean"].reindex(idx).interpolate(limit=2).fillna(0)

    # Hueco recurrente de 1h en el cambio de hora de octubre (visto en 2024 Y 2025 -- ver nota 28
    # de la memoria, mismo patron cada año en spot_price). Tolerable con interpolate(limit=1);
    # si hay huecos mas grandes, algo distinto esta pasando y si hay que avisar.
    huecos_antes = precio.isna().sum()
    precio = precio.interpolate(limit=1)
    if precio.isna().any():
        return {"error": f"Faltan {precio.isna().sum()} horas de precio en ese rango (mas de un "
                          f"hueco aislado) -- elige un periodo distinto."}
    if huecos_antes > 0:
        pass  # hueco de 1h relleno por interpolacion, no se detiene la simulacion por esto

    generacion_mw = (potencia_solar_kwp / 1000) * (radiacion / 1000).clip(lower=0)
    consumo_mw = pd.Series(consumo_anual_mwh / 8760, index=idx)  # perfil PLANO, ver limitaciones

    soc = 0.0  # estado de carga de la bateria, MWh
    coste_con_instalacion = 0.0
    coste_sin_instalacion = 0.0
    energia_autoconsumida = 0.0

    for t in idx:
        gen, con, p = generacion_mw[t], consumo_mw[t], precio[t]
        coste_sin_instalacion += con * p

        directo = min(gen, con)
        energia_autoconsumida += directo
        excedente = gen - directo
        deficit = con - directo

        if excedente > 0:
            carga = min(excedente, potencia_bateria_mw, capacidad_bateria_mwh - soc)
            soc += carga
            sobra = excedente - carga
            coste_con_instalacion -= sobra * p  # vertido a red = ingreso
        elif deficit > 0:
            descarga = min(deficit, potencia_bateria_mw, soc) * eficiencia_bateria
            soc -= descarga / eficiencia_bateria if eficiencia_bateria > 0 else 0
            energia_autoconsumida += descarga
            falta = deficit - descarga
            coste_con_instalacion += falta * p

    dias = len(idx) / 24
    ahorro_total = coste_sin_instalacion - coste_con_instalacion
    return {
        "etiqueta": "SIMULACION SOBRE DATOS REALES (backtest historico, version 1 -- ver limitaciones)",
        "parametros": {"potencia_solar_kwp": potencia_solar_kwp, "potencia_bateria_mw": potencia_bateria_mw,
                       "capacidad_bateria_mwh": capacidad_bateria_mwh, "eficiencia_bateria": eficiencia_bateria,
                       "consumo_anual_mwh_asumido": consumo_anual_mwh, "desde": desde, "hasta": hasta},
        "dias_simulados": round(dias, 1),
        "coste_sin_instalacion_eur": round(coste_sin_instalacion, 2),
        "coste_con_instalacion_eur": round(coste_con_instalacion, 2),
        "ahorro_eur": round(ahorro_total, 2),
        "ahorro_anualizado_eur": round(ahorro_total / dias * 365, 2) if dias > 0 else None,
        "porcentaje_autoconsumo": round(100 * energia_autoconsumida / consumo_mw.sum(), 1) if consumo_mw.sum() > 0 else None,
        "limitaciones": [
            "Perfil de consumo PLANO (mismo cada hora) -- el siguiente paso es que el cliente "
            "aporte su propia curva de consumo real.",
            "Generacion solar simplificada (radiacion/1000 W/m2 x potencia) -- falta modelar "
            "perdidas por temperatura, orientacion del panel y eficiencia del inversor.",
            "Sin degradacion de bateria ni costes de operacion/mantenimiento.",
        ],
    }


def precio_futuro_curva(desde: str, hasta: str, nivel_por_anio: dict[int, float] | None = None) -> dict:
    """Curva de precio horario para CUALQUIER rango, incluido horizonte largo (varios años a
    futuro) -- envuelve `scripts/curva_precios.py`, construido por un compañero del equipo y
    validado por backtest (ver `metodologia` en la respuesta). Es la herramienta correcta para
    "precio en 2030", "curva a 2046", "cómo evolucionará el precio en 10 años" -- NUNCA uses
    `precio_historico_percentiles` para estos casos, esa es solo para patrones YA OCURRIDOS.

    Distincion importante que hay que trasladar siempre al usuario: esto NO es una prediccion
    determinista como `prediccion_d_mas_1` (que solo existe para mañana). Es un ESCENARIO --
    cose tres tramos segun la fecha (historico real / lo que predijeron los modelos de D+1, si
    el rango los alcanza / una simulacion Monte Carlo para el resto), y el tramo simulado
    depende de un supuesto de NIVEL de precio anual que hay que aportar (viene de futuros MIBEL
    o de un escenario, no se predice) -- si no se aporta, se usa la media de los ultimos 12
    meses mantenida plana, que es un marcador de posicion, no una prevision real de mercado.

    Args:
        desde: Fecha de inicio, YYYY-MM-DD. Puede ser pasada, de mañana en adelante, o a
            decadas vista.
        hasta: Fecha de fin, YYYY-MM-DD.
        nivel_por_anio: Opcional. Nivel de precio medio anual en EUR/MWh, dando solo unas pocas
            "anclas" (2-4 años bastan) -- ej. {2027: 66, 2030: 60, 2040: 55, 2046: 52}. Los años
            intermedios se interpolan linealmente automaticamente. Si se omite, se usa un
            marcador de posicion (media de los ultimos 12 meses, plana) -- avisar de esto en la
            respuesta.
    """
    import sys as _sys
    _sys.path.append(str(REPO / "scripts"))
    from curva_precios import curva, por_anclas

    if nivel_por_anio:
        # `curva()` exige el nivel de TODOS los años del rango, no solo anclas -- por_anclas()
        # es el propio helper del script para interpolar entre las anclas dadas (asi el usuario
        # (o el LLM) puede dar solo 2-4 puntos, como en el uso real de curva_precios.py, sin que
        # esto explote con un ValueError por años intermedios que faltan).
        anio_ini, anio_fin = pd.Timestamp(desde).year, pd.Timestamp(hasta).year
        nivel_por_anio = por_anclas(nivel_por_anio, anio_ini, anio_fin)

    c = curva(desde, hasta, nivel=nivel_por_anio, n=200, verbose=False)
    c = c.assign(anio=c["dia"].dt.year)

    resumen = []
    for (anio, origen), grupo in c.groupby(["anio", "origen"]):
        fila = {"anio": int(anio), "origen": origen, "dias": int(grupo["dia"].nunique()),
                "precio_medio_eur_mwh": round(float(grupo["p50"].mean()), 2)}
        if origen == "simulado":
            fila["p10_eur_mwh"] = round(float(grupo["p10"].mean()), 2)
            fila["p90_eur_mwh"] = round(float(grupo["p90"].mean()), 2)
            fila["horas_negativas_pct"] = round(100 * float((grupo["p50"] < 0).mean()), 1)
        resumen.append(fila)
    resumen.sort(key=lambda f: (f["anio"], f["origen"]))

    hay_simulado = any(f["origen"] == "simulado" for f in resumen)
    resultado = {
        "etiqueta": "ESCENARIO A LARGO PLAZO (metodologia validada por backtest -- NO es una "
                    "prediccion determinista, es una curva de referencia con banda de "
                    "incertidumbre donde hace falta simular)",
        "desde": desde, "hasta": hasta,
        "resumen_anual": resumen,
        "metodologia": "precio(dia,hora) = nivel(año) x factor estacional del historico + forma "
                       "intradiaria (deformandose por la canibalizacion solar: el valle de "
                       "mediodia se hunde año a año) + residuo remuestreado de dias reales "
                       "completos -- fuente: scripts/curva_precios.py",
    }
    if hay_simulado and nivel_por_anio is None:
        resultado["advertencia"] = (
            "No se aporto un escenario de nivel de precio anual -- se uso la media de los "
            "ultimos 12 meses mantenida plana para los años simulados. Es un MARCADOR DE "
            "POSICION, no una prevision de mercado: el nivel real deberia salir de los futuros "
            "MIBEL (cotizan a 3-4 años) o de un escenario fundamental del equipo."
        )
    return resultado


def extrapolar_consumo_cliente(historico_mensual_mwh: list[float], anios_a_futuro: int = 2) -> dict:
    """Extrapola el consumo mensual futuro de un cliente a partir de SU PROPIO historico real --
    con rangos (p10/p50/p90), nunca un solo numero. Misma logica que
    `scripts/curva_precios.py` (nivel x estacionalidad + residuo -> percentiles): el NIVEL no se
    predice con una tendencia arriesgada, se mantiene el mas reciente; la ESTACIONALIDAD sale del
    propio historico del cliente; el RESIDUO (variabilidad real mes a mes) es lo que abre la banda
    p10-p90 -- no una suposicion de cuanta incertidumbre "deberia" haber.

    Args:
        historico_mensual_mwh: Consumo mensual del cliente en MWh, EMPEZANDO EN ENERO, sin
            huecos, con longitud multiplo de 12 (minimo 12 meses; con 24-36 la estacionalidad
            sale mas fiable).
        anios_a_futuro: Cuantos años hacia adelante extrapolar (por defecto 2).
    """
    n = len(historico_mensual_mwh)
    if n < 12 or n % 12 != 0:
        return {"error": "Se necesitan meses completos empezando en enero, en multiplos de 12 "
                          "(12, 24, 36...). Recibido: " + str(n) + " meses."}

    datos = np.array(historico_mensual_mwh).reshape(-1, 12)
    n_anios = datos.shape[0]

    nivel_por_anio = datos.mean(axis=1)
    factor_estacional = (datos / nivel_por_anio[:, None]).mean(axis=0)  # 1 valor por mes (0=ene..11=dic)

    nivel_reciente = float(nivel_por_anio[-1])  # el mas reciente, sin extrapolar tendencia -- ver limitaciones

    esperado = nivel_por_anio[:, None] * factor_estacional[None, :]
    residuo_relativo = (datos - esperado) / esperado  # variabilidad real, en proporcion
    p10_r, p50_r, p90_r = np.percentile(residuo_relativo, [10, 50, 90])

    proyeccion = []
    for anio_futuro in range(1, anios_a_futuro + 1):
        for mes in range(12):
            base = nivel_reciente * factor_estacional[mes]
            proyeccion.append({
                "anio_relativo": anio_futuro, "mes": mes + 1,
                "p10_mwh": round(float(base * (1 + p10_r)), 2),
                "p50_mwh": round(float(base * (1 + p50_r)), 2),
                "p90_mwh": round(float(base * (1 + p90_r)), 2),
            })

    return {
        "etiqueta": "EXTRAPOLACION DEL CONSUMO DEL CLIENTE (basada en SU histórico real, con "
                    "rangos -- NO es una prediccion de precio ni de mercado)",
        "meses_historicos_usados": n, "años_historicos": n_anios,
        "nivel_base_mensual_mwh": round(nivel_reciente, 2),
        "proyeccion_mensual": proyeccion,
        "limitaciones": [
            "El nivel base se mantiene igual al del ultimo año -- no proyecta crecimiento ni "
            "caida del negocio. Si el cliente espera crecer/reducir consumo, hay que ajustarlo "
            "a mano multiplicando el nivel.",
            f"La estacionalidad y la banda de incertidumbre salen de solo {n_anios} año(s) de "
            "historico del cliente -- con mas años, la banda sera mas fiable.",
            "No incorpora clima, precios de la energia ni decisiones del cliente (ej. nueva "
            "maquinaria) que puedan cambiar el consumo futuro.",
        ],
    }


_MODELO_EMBEDDING = None


def _cargar_modelo_embedding():
    global _MODELO_EMBEDDING
    if _MODELO_EMBEDDING is None:
        from fastembed import TextEmbedding
        _MODELO_EMBEDDING = TextEmbedding(
            model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    return _MODELO_EMBEDDING


def buscar_documentacion(pregunta: str, k: int = 3) -> dict:
    """Busqueda semantica (RAG) sobre la documentacion del proyecto (notas_memoria_tfm.md,
    columnas_pendientes_equipo.md) -- para preguntas de METODOLOGIA/decisiones ("por que", "como
    se decidio", "que es"), no para datos de precio (para eso, las otras herramientas).
    Requiere haber corrido antes `indexar_documentacion.py`."""
    from pgvector.psycopg2 import register_vector

    modelo = _cargar_modelo_embedding()
    vector_pregunta = list(modelo.embed([pregunta]))[0]

    conn = _conectar()
    try:
        register_vector(conn)
        cur = conn.cursor()
        cur.execute("""
            SELECT fuente, numero, titulo, texto, embedding <=> %s AS distancia
            FROM documentacion_embeddings
            ORDER BY distancia ASC
            LIMIT %s
        """, (vector_pregunta, k))
        filas = cur.fetchall()
    finally:
        conn.close()

    if not filas:
        return {"error": "La tabla documentacion_embeddings esta vacia -- corre indexar_documentacion.py primero."}

    return {
        "etiqueta": "FRAGMENTOS DE LA DOCUMENTACION DEL PROYECTO (citar la fuente/nota, no inventar)",
        "resultados": [
            {"fuente": f, "nota": n, "titulo": t, "texto": txt, "similitud": round(1 - d, 3)}
            for f, n, t, txt, d in filas
        ],
    }


def capacidad_instalada(fecha: str | None = None) -> dict:
    """Capacidad instalada por tecnologia en España (MW) -- dato administrativo (cuanta potencia
    HAY instalada), no de generacion real horaria. Usa esta herramienta para "cuanta solar/eolica
    hay instalada", "capacidad renovable", etc.

    Se añadio tras encontrar el hueco en una prueba real: la pregunta "¿cuánta capacidad solar
    hay instalada ahora mismo?" caía en `consulta_sql_lectura`, y con el modelo mas barato
    (Haiku) el LLM respondia que no tenia el dato aunque SI estaba disponible -- con Opus si
    funcionaba, pero depender de que el modelo "se acuerde" de intentar el SQL generico para
    algo tan previsible no es fiable. Ahora es una herramienta fija, funciona con cualquier
    modelo.

    Args:
        fecha: YYYY-MM-DD. Si se omite, usa la fecha mas reciente con dato (datos desde 2020).
    """
    conn = _conectar()
    try:
        if fecha:
            df = pd.read_sql("SELECT * FROM esios_capacity_installed WHERE date = %(f)s",
                              conn, params={"f": fecha})
        else:
            df = pd.read_sql("SELECT * FROM esios_capacity_installed ORDER BY date DESC LIMIT 1", conn)
    finally:
        conn.close()

    if df.empty:
        return {"error": f"No hay dato de capacidad instalada para {fecha or 'ninguna fecha'}. "
                          f"La serie empieza el 2020-01-01."}

    f = df.iloc[0]
    total = float(f["total_mw"])
    # GW ya calculado aqui, no lo hace el LLM: probado que Haiku, redondeando el a ojo,
    # escribio "54.885 GW" para un valor que son 54.884,7 MW (~54,9 GW) -- misma cifra, unidad
    # mal puesta. Con las dos unidades ya resueltas no hace falta que el modelo convierta nada.
    por_tecnologia_mw = {
        "solar_fotovoltaica": round(float(f["solar_pv_mw"]), 1),
        "eolica": round(float(f["wind_mw"]), 1),
        "hidraulica": round(float(f["hydro_mw"]), 1),
        "nuclear": round(float(f["nuclear_mw"]), 1),
        "ciclo_combinado_gas": round(float(f["ccgt_mw"]), 1),
        "carbon": round(float(f["coal_mw"]), 1),
        "bombeo": round(float(f["pump_mw"]), 1),
        "baterias_hibridas": round(float(f["battery_hybrid_mw"]), 1),
        "cogeneracion": round(float(f["cogeneration_mw"]), 1),
        "solar_termica": round(float(f["solar_thermal_mw"]), 1),
    }
    return {
        "etiqueta": "REFERENCIA -- capacidad instalada administrativa (no es generacion real horaria)",
        "fecha": str(f["date"]),
        "total_mw": round(total, 1),
        "total_gw": round(total / 1000, 2),
        "total_renovable_mw": round(float(f["total_renewable_mw"]), 1),
        "porcentaje_renovable": round(100 * float(f["total_renewable_mw"]) / total, 1) if total else None,
        "por_tecnologia_mw": por_tecnologia_mw,
        "por_tecnologia_gw": {k: round(v / 1000, 2) for k, v in por_tecnologia_mw.items()},
    }


# Tablas a las que el rol de Postgres `asistente_solo_lectura` tiene GRANT SELECT (y nada mas --
# ni INSERT/UPDATE/DELETE, ni ninguna otra tabla de la base compartida). Ver
# sql/registro_cambios_bd.md para el alta del rol, 31-ago-2026. Esta lista en Python es una
# segunda barrera solo para dar un mensaje de error claro -- la barrera REAL es el GRANT de
# Postgres, que ya se probo que bloquea todo lo demas aunque este chequeo tuviera un fallo.
_SQL_TABLAS_PERMITIDAS = {"spot_price", "era5_weather_agg", "esios_capacity_installed",
                          "predictions", "documentacion_embeddings"}
_SQL_PALABRAS_PROHIBIDAS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|COPY|CALL|EXECUTE|VACUUM|MERGE)\b",
    re.IGNORECASE)


def consulta_sql_lectura(sql: str) -> dict:
    """Ejecuta una consulta SQL de SOLO LECTURA escrita por el propio modelo de lenguaje, contra
    un rol de Postgres (`asistente_solo_lectura`) que SOLO puede hacer SELECT sobre 5 tablas --
    no puede escribir nada, ni ver ninguna otra tabla de la base compartida del equipo, aunque
    esta funcion tuviera un fallo (verificado con una prueba real de INSERT, que Postgres
    rechazo con "permission denied").

    USAR ESTA HERRAMIENTA COMO ULTIMO RECURSO -- solo cuando la pregunta pide datos en crudo
    (tabla/grafica) que ninguna otra herramienta ya cubre. Es MENOS FIABLE que las demas: el SQL
    se genera al vuelo y no ha sido probado de antemano por una persona, a diferencia de cada una
    de las otras funciones de este modulo. SIEMPRE avisa en la respuesta que esta tabla/numero
    sale de una consulta generada dinamicamente, no de una herramienta pre-verificada.

    Tablas disponibles y sus columnas:
      spot_price(datetime timestamptz, es_esios, es_entsoe, es_omie, pt_entsoe, pt_omie,
        fr_entsoe, de_lu_entsoe, it_nord_entsoe, ch_entsoe, be_entsoe, nl_entsoe, at_entsoe,
        pl_entsoe, cz_entsoe -- todos los precios en EUR/MWh, uno por pais/fuente)
      era5_weather_agg(ts timestamp SIN zona horaria -- UTC naive, t2m_mean, wind10_mean,
        wind100_mean, ssrd_mean, tcc_mean, d2m_mean, wind_gust10_mean, tp_mean, msl_mean --
        meteorologia agregada horaria)
      esios_capacity_installed(date, total_mw, total_renewable_mw, wind_mw, solar_pv_mw,
        nuclear_mw, coal_mw, ccgt_mw, hydro_mw, battery_hybrid_mw, ... -- capacidad instalada
        por tecnologia, MW, una fila por dia)
      predictions(datetime timestamptz, pred_date, model, prediction, seed, matrix,
        matrix_hash, source -- predicciones ya calculadas por los distintos modelos del equipo)
      documentacion_embeddings(id, fuente, numero, titulo, texto -- NO selecciones la columna
        `embedding`, es un vector de 384 numeros, inutil en una tabla/grafica)

    Reglas duras (si no se cumplen, se devuelve un error explicando cual):
      - Debe ser una unica sentencia SELECT (o WITH ... SELECT), nada de INSERT/UPDATE/DELETE/DDL.
      - Sin punto y coma extra (una sola sentencia).
      - Si no lleva LIMIT, se le añade LIMIT 200 automaticamente; el maximo es 500 filas.

    Args:
        sql: La consulta SELECT completa, en SQL de Postgres.
    """
    sql_limpio = sql.strip().rstrip(";")

    if ";" in sql_limpio:
        return {"error": "Solo se permite UNA sentencia por consulta (se encontro un ';' de mas "
                          "en medio de la consulta)."}
    if not re.match(r"^\s*(SELECT|WITH)\b", sql_limpio, re.IGNORECASE):
        return {"error": "Solo se permiten consultas que empiecen por SELECT o WITH (solo lectura)."}
    if _SQL_PALABRAS_PROHIBIDAS.search(sql_limpio):
        return {"error": "La consulta contiene una palabra no permitida en modo solo-lectura "
                          "(nada de INSERT/UPDATE/DELETE/DDL/COPY/etc.)."}

    limite_existente = re.search(r"\bLIMIT\s+(\d+)\b", sql_limpio, re.IGNORECASE)
    if limite_existente is None:
        sql_limpio += " LIMIT 200"
    elif int(limite_existente.group(1)) > 500:
        sql_limpio = sql_limpio[:limite_existente.start()] + "LIMIT 500" + sql_limpio[limite_existente.end():]

    from config import load_config_asistente_solo_lectura
    try:
        conn = psycopg2.connect(**load_config_asistente_solo_lectura())
    except KeyError as e:
        return {"error": f"Falta configurar el rol de solo lectura en credentials.json: {e}"}

    try:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SET statement_timeout = '10s'")
        cur.execute(sql_limpio)
        columnas = [d.name for d in cur.description] if cur.description else []
        filas = cur.fetchmany(500)
    except psycopg2.Error as e:
        return {"error": f"Error de Postgres al ejecutar la consulta: {e}", "sql_ejecutado": sql_limpio}
    finally:
        conn.close()

    def _json_seguro(v):
        if isinstance(v, (Decimal,)):
            return float(v)
        if isinstance(v, (pd.Timestamp,)) or hasattr(v, "isoformat"):
            return str(v)
        return v

    return {
        "etiqueta": "CONSULTA SQL GENERADA DINAMICAMENTE -- verificar antes de tomarla como dato "
                    "firme, no es una herramienta pre-probada como las demas",
        "sql_ejecutado": sql_limpio,
        "columnas": columnas,
        "filas": [dict(zip(columnas, [_json_seguro(v) for v in fila])) for fila in filas],
        "n_filas": len(filas),
    }


if __name__ == "__main__":
    print("=== Ejemplo: percentiles historicos de la hora 20h en septiembre ===")
    print(precio_historico_percentiles(hora=20, mes=9))

    print("\n=== Ejemplo: simulacion de una bateria 1MW/2MWh en 2022 (año de crisis) ===")
    print(simular_bateria(potencia_mw=1.0, capacidad_mwh=2.0, eficiencia=0.9,
                           desde="2022-01-01", hasta="2022-12-31"))
