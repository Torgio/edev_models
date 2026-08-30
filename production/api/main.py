"""API del panel de predicciones.

Sirve lo que hay en la tabla `predictions` cruzado con el PMD real de `spot_price`, y la
pagina estatica que lo pinta. Dos endpoints y nada mas: el panel no necesita otra cosa.

    GET /api/rango          entre que fechas hay datos y que modelos existen
    GET /api/dia/2026-03-15 las horas de ese dia: cada modelo y el PMD real

Arrancar en local:
    uvicorn production.api.main:app --reload --port 8000

En el servidor, detras del nginx que ya hay:
    uvicorn production.api.main:app --host 127.0.0.1 --port 8000

NO SE ASUMEN 24 HORAS. El domingo de marzo el dia tiene 23 y el de octubre 25. El eje se
construye con las horas que REALMENTE existen en la base, en vez de generar range(24) y
confiar en que cuadre.
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

REPO = Path(__file__).resolve().parents[2]
sys.path.append(str(REPO / "ingesta"))
sys.path.append(str(REPO / "modelos" / "asistente"))

TZ = "Europe/Madrid"
ESTATICOS = Path(__file__).parent / "static"

app = FastAPI(title="TFM Energia · panel de predicciones", docs_url="/api/docs")

_pool = None


def _crear_pool():
    from config import load_config
    from psycopg2 import pool
    _, db = load_config()
    return pool.ThreadedConnectionPool(1, 8, **db)


@contextmanager
def cursor():
    """Conexion del pool con la zona horaria fijada.

    Fijarla importa: `datetime` es timestamptz y sin esto cada conexion la interpretaria
    segun la del servidor, que va en UTC. El panel se lee en hora peninsular.
    """
    global _pool
    if _pool is None:
        _pool = _crear_pool()
    con = _pool.getconn()
    try:
        with con.cursor() as cur:
            cur.execute(f"SET TIME ZONE '{TZ}'")
            yield cur
        con.commit()
    finally:
        _pool.putconn(con)


@app.get("/api/rango")
def rango():
    with cursor() as cur:
        cur.execute("""
            SELECT MIN(datetime)::date, MAX(datetime)::date, COUNT(DISTINCT datetime::date)
            FROM predictions
        """)
        desde, hasta, dias = cur.fetchone()
        if desde is None:
            raise HTTPException(404, "No hay predicciones cargadas todavia.")
        cur.execute("""
            SELECT p.model,
                   ROUND(AVG(ABS(p.prediction - s.es_esios))::numeric, 3) AS mae,
                   MIN(p.source)
            FROM predictions p
            LEFT JOIN spot_price s ON s.datetime = p.datetime
            GROUP BY 1 ORDER BY 2 NULLS LAST
        """)
        modelos = [{"modelo": m, "mae": float(mae) if mae is not None else None,
                    "origen": o} for m, mae, o in cur.fetchall()]
    return {"desde": str(desde), "hasta": str(hasta), "dias": dias, "modelos": modelos}


@app.get("/api/dia/{dia}")
def por_dia(dia: date):
    with cursor() as cur:
        # El eje de horas sale de la UNION de las dos tablas: si un dia tiene 23 o 25
        # horas, o si el PMD llega antes que la prediccion, el eje sigue siendo correcto.
        cur.execute("""
            WITH horas AS (
                SELECT datetime FROM predictions WHERE datetime::date = %(d)s
                UNION
                SELECT datetime FROM spot_price
                 WHERE datetime::date = %(d)s AND es_esios IS NOT NULL
            )
            SELECT h.datetime, s.es_esios
            FROM horas h
            LEFT JOIN spot_price s ON s.datetime = h.datetime
            ORDER BY h.datetime
        """, {"d": dia})
        eje = cur.fetchall()
        if not eje:
            raise HTTPException(404, f"No hay datos para {dia}.")

        cur.execute("""
            SELECT model, datetime, prediction
            FROM predictions WHERE datetime::date = %(d)s
            ORDER BY model, datetime
        """, {"d": dia})
        crudo = cur.fetchall()

    momentos = [t for t, _ in eje]
    idx = {t: i for i, t in enumerate(momentos)}
    series = {}
    for m, t, v in crudo:
        series.setdefault(m, [None] * len(momentos))[idx[t]] = round(float(v), 2)

    real = [float(v) if v is not None else None for _, v in eje]

    # el error medio del dia, por modelo: es lo que hace util la leyenda
    mae = {}
    for m, vals in series.items():
        pares = [(p, r) for p, r in zip(vals, real) if p is not None and r is not None]
        mae[m] = round(sum(abs(p - r) for p, r in pares) / len(pares), 2) if pares else None

    return JSONResponse({
        "dia": str(dia),
        "horas": [t.strftime("%H:%M") for t in momentos],
        "real": real,
        "series": series,
        "mae_dia": mae,
    })


class PreguntaAsistente(BaseModel):
    pregunta: str


@app.post("/api/asistente")
def asistente(cuerpo: PreguntaAsistente):
    """Reenvia la pregunta al asistente (LLM + herramientas, ver modelos/asistente/chat.py).

    Requiere `anthropic_api_key` en el credentials.json de la maquina donde corre esto -- cada
    persona usa su propia clave local, no una compartida en el servidor (ver nota 33/decision de
    seguridad: es una clave con creditos reales, a diferencia del resto de credenciales del
    proyecto). Si no esta configurada, se devuelve un error claro en vez de que la pagina falle
    en silencio.
    """
    from chat import preguntar
    try:
        respuesta = preguntar(cuerpo.pregunta)
    except FileNotFoundError:
        raise HTTPException(500, "No hay credentials.json en esta maquina.")
    except KeyError:
        raise HTTPException(500, "Falta 'anthropic_api_key' en credentials.json -- "
                                  "cada persona necesita la suya propia para usar el asistente.")
    except Exception as e:
        raise HTTPException(500, f"Error del asistente: {e}")
    return {"respuesta": respuesta}


# Se monta al final: si fuera antes, se tragaria tambien las rutas /api/*.
if ESTATICOS.is_dir():
    app.mount("/", StaticFiles(directory=str(ESTATICOS), html=True), name="static")
