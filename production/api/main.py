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

import os
import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

REPO = Path(__file__).resolve().parents[2]
sys.path.append(str(REPO / "ingesta"))

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


# El estudio de bateria va en su propio modulo y se engancha aqui. Asi el panel de
# predicciones sigue siendo dos endpoints y no se mezcla con lo otro, que tiene tareas
# en segundo plano y subida de ficheros.
# `from bateria import ...` a secas solo funciona si esta carpeta esta en sys.path, y
# arrancando como `uvicorn production.api.main:app` NO lo esta. Se añade explicitamente.
sys.path.append(str(Path(__file__).parent))
from bateria import router as router_bateria           # noqa: E402
app.include_router(router_bateria)


# La pantalla del estudio de bateria se sirve DESDE AQUI, no como fichero suelto.
#
# Abierta con file:// el navegador la trata como otro origen y bloquea cualquier llamada
# al API -- se ve la pagina, no se ve un solo dato, y el error aparece en la consola donde
# nadie mira. Sirviendola desde el mismo puerto no hay origen cruzado que valga.
#
#     http://127.0.0.1:8000/bateria/estudio_bateria_dev.html
PANTALLAS = REPO / "docs" / "web"
if PANTALLAS.is_dir():
    app.mount("/bateria", StaticFiles(directory=str(PANTALLAS), html=True),
              name="bateria")
# y las plantillas de ejemplo, que la pantalla enlaza para descargar
PLANTILLAS = REPO / "docs" / "plantillas"
if PLANTILLAS.is_dir():
    app.mount("/plantillas", StaticFiles(directory=str(PLANTILLAS)), name="plantillas")

# CORS solo para desarrollo: permite abrir el HTML con file:// o desde otro puerto
# mientras se maqueta. En produccion sobra, porque todo sale del mismo origen.
if os.environ.get("TFM_CORS_ABIERTO"):
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                       allow_headers=["*"])


# Se monta al final: si fuera antes, se tragaria tambien las rutas /api/*.
if ESTATICOS.is_dir():
    app.mount("/", StaticFiles(directory=str(ESTATICOS), html=True), name="static")
