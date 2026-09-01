"""API de solo lectura para el dashboard Pulso Energía.

La web nunca recibe credenciales de PostgreSQL: consulta esta API por HTTPS y la API
devuelve únicamente predicciones, precios reales y agregados ya preparados.

Uso local:
    pip install -r requirements-dashboard.txt
    uvicorn api.dashboard_api:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from contextlib import asynccontextmanager, contextmanager, closing
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from api.auth import COOKIE_NAME, SESSION_SECONDS, LoginLimiter, auth_config
from api.peak_accuracy import evaluation_window, midnight, peak_accuracy
from api.stored_results import evaluations, battery

REPO = Path(__file__).resolve().parents[1]
sys.path.append(str(REPO / "ingesta"))

TZ = ZoneInfo("Europe/Madrid")
SOURCES = {"test", "production"}


@contextmanager
def _connection():
    import psycopg2

    if os.getenv("DASHBOARD_DB_MODE") == "environment":
        fields = {"host": "PGHOST", "dbname": "PGDATABASE", "user": "PGUSER", "password": "PGPASSWORD"}
        missing = [name for name in fields.values() if not os.getenv(name)]
        if missing:
            raise RuntimeError("Falta configuración PostgreSQL: " + ", ".join(missing))
        db = {field: os.environ[name] for field, name in fields.items()}
        db["port"] = int(os.getenv("PGPORT", "5432"))
    else:
        from config import load_config

        _, db = load_config()
    with closing(psycopg2.connect(
        **db,
        connect_timeout=10,
        options="-c default_transaction_read_only=on -c statement_timeout=15000",
    )) as connection:
        with connection:
            yield connection


def _source(value: str) -> str:
    if value not in SOURCES:
        raise HTTPException(400, f"source debe ser uno de {sorted(SOURCES)}")
    return value


origins = [
    item.strip()
    for item in os.getenv(
        "DASHBOARD_ALLOWED_ORIGINS",
        "http://localhost:3000,https://pulso-energia-tfm.maguicervinio.chatgpt.site",
    ).split(",")
    if item.strip()
]

@asynccontextmanager
async def lifespan(app):
    auth_config()  # Si faltan credenciales en producción, no arrancar sin protección.
    yield


app = FastAPI(title="Pulso Energía API", version="1.1.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

login_limiter = LoginLimiter()


@app.middleware("http")
async def protect_data(request: Request, call_next):
    auth = auth_config()
    public = (request.method, request.url.path) in {("GET", "/session"), ("POST", "/login"), ("POST", "/logout")}
    if auth and request.method != "OPTIONS" and not public and not auth.valid(request.cookies.get(COOKIE_NAME)):
        return JSONResponse({"detail": "Inicia sesión para consultar los datos."}, status_code=401, headers={"Cache-Control": "private, no-store"})
    if request.method == "POST" and request.headers.get("origin") and request.headers["origin"] not in origins:
        return JSONResponse({"detail": "Origen no permitido."}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Cookie, Origin"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/session")
def session(request: Request):
    auth = auth_config()
    return {"authenticated": auth is None or auth.valid(request.cookies.get(COOKIE_NAME)), "auth_required": auth is not None}


@app.post("/login")
async def login(request: Request):
    auth = auth_config()
    if auth is None:
        return {"authenticated": True, "auth_required": False}
    if not login_limiter.allow(request.client.host if request.client else "unknown"):
        return JSONResponse({"detail": "Demasiados intentos. Espera un minuto."}, status_code=429, headers={"Retry-After": "60"})
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        raise HTTPException(415, "Se requiere JSON.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 2048:
            raise HTTPException(413, "Solicitud demasiado grande.")
    import json
    try:
        payload = json.loads(body)
        password = payload.get("password") if isinstance(payload, dict) else None
    except (ValueError, UnicodeError):
        password = None
    if not isinstance(password, str) or not 1 <= len(password) <= 128:
        raise HTTPException(400, "Contraseña no válida.")
    if not await run_in_threadpool(auth.verify_password, password):
        raise HTTPException(401, "Contraseña incorrecta.")
    response = JSONResponse({"authenticated": True, "auth_required": True})
    response.set_cookie(COOKIE_NAME, auth.issue(), max_age=SESSION_SECONDS, httponly=True, secure=True, samesite="strict", path="/")
    return response


@app.post("/logout")
def logout():
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(COOKIE_NAME, path="/", secure=True, httponly=True, samesite="strict")
    return response


@app.get("/health")
def health():
    with _connection() as con, con.cursor() as cur:
        cur.execute("SELECT max(updated_at), count(*) FROM predictions")
        updated_at, rows = cur.fetchone()
    return {"status": "ok", "rows": rows, "updated_at": updated_at}


@app.get("/days")
def days(source: str = Query("production")):
    source = _source(source)
    with _connection() as con, con.cursor() as cur:
        cur.execute(
            """
            SELECT (datetime AT TIME ZONE 'Europe/Madrid')::date AS target_date,
                   count(DISTINCT model) AS models,
                   count(*) AS rows,
                   count(s.es_esios) AS rows_with_actual
            FROM predictions p
            LEFT JOIN spot_price s USING (datetime)
            WHERE p.source = %s
            GROUP BY 1 ORDER BY 1
            """,
            (source,),
        )
        result = cur.fetchall()
    return {
        "source": source,
        "days": [
            {"date": day, "models": models, "rows": rows, "rows_with_actual": actual}
            for day, models, rows, actual in result
        ],
    }


@app.get("/predictions/{target_date}")
def predictions(target_date: date, source: str = Query("production")):
    source = _source(source)
    with _connection() as con, con.cursor() as cur:
        cur.execute(
            """
            SELECT p.datetime, p.model, p.prediction, s.es_esios, p.updated_at
            FROM predictions p
            LEFT JOIN spot_price s USING (datetime)
            WHERE p.source = %s
              AND (p.datetime AT TIME ZONE 'Europe/Madrid')::date = %s
            ORDER BY p.datetime, p.model
            """,
            (source, target_date),
        )
        rows = cur.fetchall()

    if not rows:
        raise HTTPException(404, "No hay predicciones para esa fecha y source")

    by_time: dict = defaultdict(lambda: {"predictions": {}})
    models: set[str] = set()
    last_update = None
    for timestamp, model, prediction, actual, updated_at in rows:
        local = timestamp.astimezone(TZ)
        key = local.isoformat()
        point = by_time[key]
        point.update({
            "datetime": key,
            "hour": local.strftime("%H:%M"),
            "actual": float(actual) if actual is not None else None,
        })
        point["predictions"][model] = float(prediction)
        models.add(model)
        last_update = max(last_update, updated_at) if last_update else updated_at

    return {
        "date": target_date,
        "source": source,
        "models": sorted(models),
        "updated_at": last_update,
        "hours": list(by_time.values()),
    }


@app.get("/peak-accuracy")
def peak_counter(
    model: str = Query("ensemble", min_length=1, max_length=100),
    days: int = Query(30, ge=1, le=30),
    end_date: date | None = Query(None, ge=date(1900, 1, 31)),
    source: Literal["production"] = Query("production"),
):
    from datetime import timedelta

    start, end = evaluation_window(end_date, days)
    try:
        with _connection() as con, con.cursor() as cur:
            cur.execute(
                """
                SELECT p.datetime, p.prediction, s.es_esios
                FROM predictions p
                LEFT JOIN spot_price s USING (datetime)
                WHERE p.model = %s AND p.source = %s
                  AND p.datetime >= %s AND p.datetime < %s
                ORDER BY p.datetime
                """,
                (model, source, midnight(start), midnight(end + timedelta(days=1))),
            )
            rows = cur.fetchall()
    except Exception:
        raise HTTPException(503, "No se pudo consultar el acierto de pico.") from None
    return peak_accuracy(rows, start, end, model)


@app.get("/leaderboard")
def leaderboard(request: Request):
    if request.query_params:
        raise HTTPException(400, "Esta ruta devuelve evaluaciones guardadas, sin filtros de producción ni días.")
    try:
        return evaluations(_connection)
    except Exception:
        raise HTTPException(503, "No se pudieron consultar las evaluaciones guardadas.") from None


@app.get("/bess/{target_date}")
def bess_plan(target_date: date, request: Request):
    if request.query_params:
        raise HTTPException(400, "Esta ruta lee BESS guardado por fecha; no admite parámetros de simulación.")
    try:
        return battery(_connection, target_date)
    except Exception:
        raise HTTPException(503, "No se pudieron consultar los resultados BESS guardados.") from None
