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

import sys
import warnings
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
    """Serie horaria de precio REAL entre dos fechas (inclusive). Para backtests y graficas --
    no para fechas futuras (usar prediccion_d_mas_1 para eso, solo cubre mañana)."""
    conn = _conectar()
    try:
        df = pd.read_sql(
            "SELECT datetime, es_esios AS precio FROM spot_price "
            "WHERE datetime BETWEEN %(desde)s AND %(hasta)s ORDER BY datetime",
            conn, params={"desde": desde, "hasta": hasta},
        )
    finally:
        conn.close()
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    return df


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


if __name__ == "__main__":
    print("=== Ejemplo: percentiles historicos de la hora 20h en septiembre ===")
    print(precio_historico_percentiles(hora=20, mes=9))

    print("\n=== Ejemplo: simulacion de una bateria 1MW/2MWh en 2022 (año de crisis) ===")
    print(simular_bateria(potencia_mw=1.0, capacidad_mwh=2.0, eficiencia=0.9,
                           desde="2022-01-01", hasta="2022-12-31"))
