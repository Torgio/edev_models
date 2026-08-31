"""API del estudio de bateria. Lo que la pantalla necesita del servidor, y nada mas.

    GET  /api/bat/estado                 que curva de precio hay publicada
    GET  /api/bat/curva                  los percentiles horarios de un tramo
    GET  /api/bat/instalaciones          lo que el usuario ya tiene guardado
    POST /api/bat/curvas                 subir consumo y/o generacion
    POST /api/bat/estudio                lanzar el calculo. Devuelve un identificador
    GET  /api/bat/estudio/{tarea}        como va, y el resultado cuando acaba
    GET  /api/bat/despacho/{run_id}      el despacho hora a hora de un tramo

POR QUE HAY UNA TAREA Y NO UNA RESPUESTA DIRECTA
El estudio tarda unos cuatro minutos: veinte escenarios sobre 7.426 dias. Eso no cabe en
una peticion HTTP -- el navegador, el nginx de delante y cualquier proxy por el camino
cortan mucho antes, y el usuario se queda mirando una pantalla en blanco sin saber si
sigue vivo. Se devuelve un identificador, el calculo va por detras y la pantalla pregunta.

LO QUE ESTO NO ES
No hay autenticacion. El usuario llega en `email` y se cree lo que dice. Vale para el TFM
y para la demo; para abrir esto a nadie mas hace falta login de verdad, porque ahora mismo
cualquiera puede leer y escribir las instalaciones de cualquiera con solo saber su correo.

Las tareas viven en memoria del proceso: si se reinicia, se pierden las que estuvieran a
medias. Los RESULTADOS no, que estan en la base -- solo se pierde el hilo de "esta
corriendo". Para un solo proceso es suficiente; con varios trabajadores haria falta sacar
el registro a la base o a un Redis.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

# La subida de ficheros necesita `python-multipart`, que no es dependencia de FastAPI.
# Sin el, importar este modulo REVIENTA ENTERO y se cae tambien el panel de predicciones,
# que no tiene nada que ver. Se aisla: si falta, todo lo demas sigue en pie y solo la
# subida contesta que falta instalarlo.
try:
    from fastapi import File, Form, UploadFile
    HAY_MULTIPART = True
except ImportError:                                    # pragma: no cover
    HAY_MULTIPART = False
try:
    import multipart                                   # noqa: F401
except ImportError:
    HAY_MULTIPART = False

REPO = Path(__file__).resolve().parents[2]
for sub in ("ingesta", "production/app", "production/curva"):
    if str(REPO / sub) not in sys.path:
        sys.path.append(str(REPO / sub))

router = APIRouter(prefix="/api/bat", tags=["bateria"])

# el email por defecto solo para no romper la demo; en cuanto haya login, fuera
EMAIL_DEMO = os.environ.get("TFM_EMAIL", "acjg.sgs@outlook.com")

_pool = None
_lock = threading.Lock()
TAREAS: dict[str, dict] = {}


def _crear_pool():
    from config import load_config
    from psycopg2 import pool
    _, db = load_config()
    return pool.ThreadedConnectionPool(1, 6, **db)


@contextmanager
def cursor(escribe=False):
    """Conexion del pool. `escribe=True` confirma al salir; si no, se deshace.

    Deshacer por defecto no es paranoia: una lectura que deje la transaccion abierta
    mantiene vivo un snapshot y bloquea el VACUUM de las tablas grandes.
    """
    global _pool
    with _lock:
        if _pool is None:
            _pool = _crear_pool()
    con = _pool.getconn()
    try:
        with con.cursor() as cur:
            yield cur
        con.commit() if escribe else con.rollback()
    except Exception:
        con.rollback()
        raise
    finally:
        _pool.putconn(con)


def _uid(cur, email: str) -> int:
    cur.execute("SELECT user_id FROM app_user WHERE email = %s", (email,))
    f = cur.fetchone()
    if f:
        return f[0]
    cur.execute("INSERT INTO app_user (email) VALUES (%s) RETURNING user_id", (email,))
    return cur.fetchone()[0]


# ══════════════════════════════════════════════════════════════════════════════
#  La curva de precio
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/estado")
def estado():
    """Que curva hay publicada. Es lo primero que pregunta la pantalla."""
    with cursor() as cur:
        cur.execute("SELECT generated_at, last_observed_date, date_from, date_to, "
                    "n_scenarios, n_hours, engine, matrix_name, matrix_hash FROM app_curve")
        c = cur.fetchone()
        if c is None:
            raise HTTPException(503, "No hay curva de precio publicada.")
        cur.execute("SELECT count(*) FROM app_curve_hourly")
        n = cur.fetchone()[0]
    g, obs, d0, d1, ns, nh, motor, mtz, mh = c
    return {"generada": g.isoformat(), "ultimo_dato_real": str(obs),
            "desde": str(d0), "hasta": str(d1), "escenarios": ns, "horas": nh,
            "motor": motor, "matriz": mtz, "matriz_hash": mh, "filas_horarias": n}


@router.get("/curva")
def curva(desde: date, hasta: date):
    """Los percentiles horarios del tramo pedido.

    Con tope: 178.224 filas no caben en una respuesta y nadie las quiere. Un mes son
    744 y es lo que la pantalla dibuja de una vez.
    """
    if hasta < desde:
        raise HTTPException(400, "'hasta' es anterior a 'desde'.")
    if (hasta - desde).days > 400:
        raise HTTPException(400, "Como mucho 400 dias por peticion.")
    with cursor() as cur:
        cur.execute("SELECT datetime, p10, p50, p90 FROM app_curve_hourly "
                    "WHERE datetime >= %s AND datetime < %s + interval '1 day' "
                    "ORDER BY datetime", (desde, hasta))
        filas = cur.fetchall()
    if not filas:
        raise HTTPException(404, f"La curva no cubre {desde} → {hasta}.")
    return {"desde": str(desde), "hasta": str(hasta), "horas": len(filas),
            # el eje NO se genera con range(24): se devuelve el que hay, que es
            # una rejilla nominal de 24 h/dia y no tiene por que coincidir con
            # las horas reales del calendario
            "t": [t.isoformat(sep=" ") for t, _, _, _ in filas],
            "p10": [round(float(a), 2) for _, a, _, _ in filas],
            "p50": [round(float(b), 2) for _, _, b, _ in filas],
            "p90": [round(float(c), 2) for _, _, _, c in filas]}


# ══════════════════════════════════════════════════════════════════════════════
#  Las curvas del usuario
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/instalaciones")
def instalaciones(email: str = EMAIL_DEMO):
    with cursor() as cur:
        uid = _uid(cur, email)
        cur.execute("SELECT code, name, annual_mwh FROM app_consump_inst "
                    "WHERE user_id = %s ORDER BY code", (uid,))
        con = [{"code": a, "nombre": b, "anual_mwh": float(c)} for a, b, c in cur.fetchall()]
        cur.execute("SELECT code, name, technology, capacity_mwp FROM app_gen_inst "
                    "WHERE user_id = %s ORDER BY code", (uid,))
        gen = [{"code": a, "nombre": b, "tecnologia": c, "mwp": float(d)}
               for a, b, c, d in cur.fetchall()]
        cur.execute("SELECT code, name, power_mw, duration_h FROM app_battery_model "
                    "WHERE user_id = %s ORDER BY code", (uid,))
        bat = [{"code": a, "nombre": b, "kw": float(c) * 1000, "horas": float(d)}
               for a, b, c, d in cur.fetchall()]
    return {"consumo": con, "generacion": gen, "baterias": bat}


# El decorador no vale aqui: FastAPI analiza la firma AL REGISTRAR la ruta, y con
# `File(...)` en los argumentos revienta en ese momento si falta multipart -- da igual
# lo que ponga dentro de un `if`. Definir la funcion es inofensivo; lo que se decide
# despues es CUAL se registra.
async def subir(consumo: UploadFile | None = File(None),
                generacion: UploadFile | None = File(None),
                code_consumo: str = Form("CONSUMO"),
                code_generacion: str = Form("GENERACION"),
                unidad: str = Form("auto"),
                tecnologia: str = Form("fv"),
                forzar: bool = Form(False),
                email: str = Form(EMAIL_DEMO)):
    """Sube una curva, o las dos. Devuelve lo MISMO que `cargar_curvas.py --json`.

    Se reutiliza el modulo tal cual en vez de reimplementar la lectura aqui: si un dia
    cambia como se resuelve el cambio de hora, cambia en un sitio.
    """
    if consumo is None and generacion is None:
        raise HTTPException(400, "No has mandado ningun fichero.")
    from cargar_curvas import cargar_una, respuesta

    tmp = Path(tempfile.mkdtemp(prefix="curvas_"))
    try:
        salida = []
        with cursor(escribe=True) as cur:
            uid = _uid(cur, email)
            for sube, es_consumo, code in ((consumo, True, code_consumo),
                                           (generacion, False, code_generacion)):
                if sube is None:
                    continue
                ruta = tmp / Path(sube.filename).name
                ruta.write_bytes(await sube.read())
                try:
                    i, r, problemas = cargar_una(cur, uid, ruta, es_consumo, code,
                                                 sube.filename, unidad, tecnologia,
                                                 forzar, verbose=False)
                except ValueError as e:
                    # el fichero no se ha podido leer: es culpa del fichero, no del
                    # servidor, asi que 400 y el motivo tal cual para enseñarselo
                    raise HTTPException(400, str(e))
                salida.append(respuesta("consumo" if es_consumo else "generacion",
                                        code, i, r, problemas))
        return salida
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _sin_multipart():
    raise HTTPException(501, "Falta 'python-multipart' en el entorno del servidor. "
                             "Instalalo con: pip install python-multipart")


router.add_api_route("/curvas", subir if HAY_MULTIPART else _sin_multipart,
                     methods=["POST"])


# ══════════════════════════════════════════════════════════════════════════════
#  El estudio
# ══════════════════════════════════════════════════════════════════════════════

class Estudio(BaseModel):
    email: str = EMAIL_DEMO
    code: str = Field("ESTUDIO", description="identificador del caso")
    consumo: str | None = None
    generacion: str | None = None
    # la bateria llega entera: la ficha es del usuario, no del servidor
    potencia_kw: float = 100
    duracion_h: float = 4
    capex_eur_mwh: float = 200_000
    eficiencia: float = 0.90
    soc_min: float = 0.05
    soc_max: float = 0.95
    ciclos: int = 6000
    carga_max_pct: float = 100
    descarga_max_pct: float = 100
    p_min_pct: float = 0
    # el periodo
    desde: date
    hasta: date
    escenarios: int = 20
    politica: str = "libre"
    tasa: float = 0.07
    opex: float = 0.015


def _correr(tarea: str, e: Estudio):
    """Lanza `caso.py` por detras y va contando por donde va.

    Se llama al script y no a sus funciones a proposito: es el MISMO camino que se usa
    desde la consola y el que esta probado. Una segunda forma de ejecutar lo mismo es una
    segunda forma de que se rompa.
    """
    t = TAREAS[tarea]
    env = {**os.environ, "TFM_EMAIL": e.email, "PYTHONIOENCODING": "utf-8"}
    bat = f"{e.code}-BAT"

    ordenes = [
        ["bateria", "--code", bat, "--nombre", f"{e.potencia_kw:.0f} kW / {e.duracion_h:.0f} h",
         "--potencia", str(e.potencia_kw / 1000), "--duracion", str(e.duracion_h),
         "--capex", str(e.capex_eur_mwh), "--eficiencia", str(e.eficiencia),
         "--soc-min", str(e.soc_min), "--soc-max", str(e.soc_max),
         "--ciclos", str(e.ciclos), "--carga-max", str(e.carga_max_pct),
         "--descarga-max", str(e.descarga_max_pct), "--p-min", str(e.p_min_pct)],
        ["crear", "--code", e.code, "--nombre", f"Estudio {e.code}",
         "--modo", "autoconsumo" if (e.consumo or e.generacion) else "standalone",
         "--bateria", bat,
         *(["--consumo", e.consumo] if e.consumo else []),
         *(["--generacion", e.generacion] if e.generacion else []),
         "--desde", str(e.desde), "--hasta", str(e.hasta),
         "--politica", e.politica, "--tasa", str(e.tasa), "--opex", str(e.opex)],
        ["ejecutar", "--code", e.code, "--escenarios", str(e.escenarios)],
    ]
    try:
        for k, orden in enumerate(ordenes):
            t["paso"] = ["dando de alta la batería", "creando el caso",
                         "optimizando"][k]
            p = subprocess.Popen([sys.executable, "-u", "production/app/caso.py", *orden],
                                 cwd=REPO, env=env, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True,
                                 encoding="utf-8", errors="replace", bufsize=1)
            t["_proc"] = p
            ultimas = []
            for linea in p.stdout:
                ultimas.append(linea.rstrip())
                del ultimas[:-40]
                # `caso.py` va escribiendo "escenario 7/20": es el unico progreso real
                # que hay, y sin el la barra seria una animacion mintiendo
                if "escenario " in linea and "/" in linea:
                    try:
                        cur, tot = linea.split("escenario ")[1].split()[0].split("/")
                        t["escenario"], t["escenarios"] = int(cur), int(tot)
                        t["progreso"] = round(int(cur) / int(tot), 3)
                    except (ValueError, IndexError):
                        pass
            if p.wait() != 0:
                t.update(estado="error", error="\n".join(ultimas[-12:]))
                return
        with cursor() as cur:
            cur.execute("""
                SELECT r.run_id FROM app_case_run r
                JOIN app_study_case c ON c.case_id = r.case_id
                JOIN app_user u ON u.user_id = c.user_id
                WHERE u.email = %s AND c.code = %s
                ORDER BY r.run_at DESC LIMIT 1""", (e.email, e.code))
            f = cur.fetchone()
        t.update(estado="hecho", progreso=1.0, run_id=f[0] if f else None,
                 paso="terminado")
    except Exception as ex:                       # noqa: BLE001
        t.update(estado="error", error=f"{type(ex).__name__}: {ex}")
    finally:
        t.pop("_proc", None)


@router.post("/estudio")
def lanzar(e: Estudio):
    if e.hasta <= e.desde:
        raise HTTPException(400, "'hasta' tiene que ser posterior a 'desde'.")
    tarea = uuid.uuid4().hex[:12]
    TAREAS[tarea] = {"estado": "corriendo", "progreso": 0.0, "paso": "arrancando",
                     "escenario": 0, "escenarios": e.escenarios,
                     "arrancada": datetime.now().isoformat(timespec="seconds"),
                     "code": e.code}
    threading.Thread(target=_correr, args=(tarea, e), daemon=True).start()
    return {"tarea": tarea, "estado": "corriendo"}


@router.get("/estudio/{tarea}")
def mirar(tarea: str):
    t = TAREAS.get(tarea)
    if t is None:
        raise HTTPException(404, "Esa tarea no existe, o el servidor se ha reiniciado.")
    fuera = {k: v for k, v in t.items() if not k.startswith("_")}
    if t["estado"] == "hecho" and t.get("run_id"):
        fuera["resultado"] = resultado(t["run_id"])
    return fuera


@router.get("/resultado/{run_id}")
def resultado(run_id: int):
    """La tarjeta y la tabla por año. Es lo que pinta la pantalla 3."""
    with cursor() as cur:
        cur.execute("SELECT * FROM app_case_run WHERE run_id = %s", (run_id,))
        f = cur.fetchone()
        if f is None:
            raise HTTPException(404, f"No hay resultado {run_id}.")
        cols = [d[0] for d in cur.description]
        run = {c: (float(v) if isinstance(v, (int, float)) and c not in
                   ("run_id", "case_id", "days_historical", "days_simulated",
                    "n_scenarios") else v) for c, v in zip(cols, f)}
        run = {c: (v.isoformat() if hasattr(v, "isoformat") else v) for c, v in run.items()}

        cur.execute("""SELECT year, origin, days, margin_mean, p10, p50, p90,
                              cycles_per_day, energy_charged_mwh, energy_discharged_mwh,
                              grid_import_mwh, grid_export_mwh
                       FROM app_case_result_annual WHERE run_id = %s ORDER BY year""",
                    (run_id,))
        campos = ["ano", "origen", "dias", "margen", "p10", "p50", "p90", "ciclos_dia",
                  "cargado_mwh", "descargado_mwh", "importado_mwh", "exportado_mwh"]
        anos = [{k: (float(v) if isinstance(v, (int, float)) and k not in
                     ("ano", "dias") else v) for k, v in zip(campos, fila)}
                for fila in cur.fetchall()]
    return {"run": run, "anual": anos}


@router.get("/despacho/{run_id}")
def despacho(run_id: int, desde: date, hasta: date, escenario: int | None = None):
    """El despacho hora a hora. SIEMPRE por tramo: son medio millon de filas por estudio.

    Sin `escenario` se coge el primero que haya guardado -- no es el 0, que solo existe
    en el tramo historico, asi que fijarlo a 0 devuelve vacio en un caso de futuro.
    """
    if (hasta - desde).days > 62:
        raise HTTPException(400, "Como mucho 62 dias por peticion.")
    with cursor() as cur:
        if escenario is None:
            cur.execute("SELECT min(scenario) FROM app_case_dispatch WHERE run_id = %s",
                        (run_id,))
            f = cur.fetchone()
            escenario = f[0] if f and f[0] is not None else 0
        cur.execute("""
            SELECT datetime, price, charge_mw, discharge_mw, soc_mwh,
                   grid_import_mwh, grid_export_mwh, load_mwh, generation_mwh
            FROM app_case_dispatch
            WHERE run_id = %s AND scenario = %s
              AND datetime >= %s AND datetime < %s + interval '1 day'
            ORDER BY datetime""", (run_id, escenario, desde, hasta))
        filas = cur.fetchall()
    if not filas:
        raise HTTPException(404, f"Sin despacho guardado para {desde} → {hasta}.")
    col = lambda i: [round(float(f[i]), 4) for f in filas]      # noqa: E731
    return {"run_id": run_id, "escenario": escenario, "horas": len(filas),
            "t": [f[0].isoformat(sep=" ") for f in filas],
            "precio": col(1), "carga": col(2), "descarga": col(3), "soc": col(4),
            "importado": col(5), "exportado": col(6),
            "consumo": col(7), "generacion": col(8)}
