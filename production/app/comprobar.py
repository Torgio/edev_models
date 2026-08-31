"""Comprobar que la cadena entera es correcta: esquema, curva, perfiles, optimizador.

POR QUE ESTE FICHERO
Hay cuatro piezas -- la curva, los perfiles horarios, el optimizador y las tablas -- y cada
una no valida nada de las demas. Los fallos que aparecen en un montaje asi no suelen ser
errores de calculo: son DERIVAS. El codigo inserta una columna que la tabla ya no tiene, el
artefacto en disco es de otra publicacion distinta de la fila de la base, el balance
energetico se cumple dentro del LP pero no en lo que se guarda. Ninguna revienta de forma
visible: siguen funcionando y diciendo numeros con buena pinta.

El caso que motivo esto: al sustituir `curve_id` por `curve_generated_at` +
`curve_matrix_hash` entro una columna mas en el INSERT y no entro su `%s`. psycopg2 dijo
"not all arguments converted during string formatting", que no señala ni la tabla ni la
columna, y ademas solo salto DESPUES de tres minutos de solver por caso -- con los resultados
ya calculados y tirados. Un `assert` de dos lineas lo caza en frio y en un segundo.

QUE COMPRUEBA
  esquema       las tablas existen; el codigo y el DDL siguen de acuerdo; los ejes
                temporales tienen el tipo que deben (la trampa del cambio de hora)
  curva         el artefacto abre, cuadra con su meta, cubre mañana, y los percentiles
                guardados en la base son los del `.npy`
  perfil        24 horas por dia SIEMPRE: año bisiesto, dia de 23 horas, dia de 25
  optimizador   las leyes que el despacho no puede violar -- balance horario, recursion del
                estado de carga, limites de potencia -- medidas sobre un despacho de verdad
  economia      el margen guardado es el que sale al recalcularlo, y el optimo es optimo
                contra dos casos con solucion conocida a mano

COMO SE USA
    python production/app/comprobar.py               lo que no necesita Postgres
    python production/app/comprobar.py --base        añade las comprobaciones contra la base
    python production/app/comprobar.py --grupo curva

Sale con 0 si todo pasa y con 1 si algo falla, para poder colgarlo del cron. Los AVISOS no
cuentan como fallo: son cosas que hay que saber y que no invalidan nada.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
import tempfile
import traceback
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

AQUI = Path(__file__).resolve().parent
REPO = AQUI.parents[1]
sys.path.insert(0, str(AQUI))
sys.path.insert(0, str(REPO / "production" / "curva"))
sys.path.insert(0, str(REPO / "scripts"))

VERDE, ROJO, GRIS, AMAR, FIN = "\033[32m", "\033[31m", "\033[90m", "\033[33m", "\033[0m"
PRUEBAS: list = []


def prueba(grupo, nombre, base=False):
    """Registra una comprobacion. `base=True` marca las que necesitan Postgres."""
    def deco(f):
        PRUEBAS.append((grupo, nombre, f, base))
        return f
    return deco


class Aviso(Exception):
    """No es un fallo: es algo que hay que saber pero que no invalida el resultado."""


# ══════════════════════════════════════════════════════════════════════════════
#  ESQUEMA · que el codigo y la base no se hayan separado
# ══════════════════════════════════════════════════════════════════════════════

FUENTES = ["production/app/caso.py", "production/app/cargar_perfil.py",
           "production/curva/generar_curva.py", "production/app/optimiza_bateria.py"]


def _inserts_del_codigo():
    """Saca de los .py cada INSERT con sus columnas y cuantos huecos `%s` lleva.

    El AST ya junta los literales adyacentes -- `"INSERT " "INTO x"` es UN nodo Constant --
    asi que la concatenacion no hay que rehacerla a mano.

    LOS DOS TIPOS DE INSERT, Y POR QUE SE TRATAN DISTINTO
    Los que son un literal entero se pueden contar: columnas frente a `%s`. Ahi es donde cabe
    el descuadre y ahi es donde hay que mirar.

    Los que se arman en tiempo de ejecucion -- `f"... ({', '.join(COLS)}) VALUES ..."` -- no
    se pueden contar en estatico, y tampoco hace falta: el numero de huecos SALE de la propia
    lista de columnas, asi que descuadrarse es imposible por construccion. De esos solo se
    miran los nombres que se dejan ver, para el contraste contra el DDL.

    La tupla COLS se busca DENTRO de la funcion que hace el INSERT y no en todo el fichero:
    a nivel de modulo, el `COLS` de una funcion se le atribuia a los INSERT de las otras y
    salian descuadres inventados.

    Devuelve (fichero, tabla, columnas, huecos, execute_values, generado).
    """
    def _texto(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            return n.value, False
        if isinstance(n, ast.JoinedStr):
            return "".join(p.value for p in n.values
                           if isinstance(p, ast.Constant) and isinstance(p.value, str)), True
        return None, False

    def _del_ambito(raiz):
        """Lo que hay en este ambito SIN entrar en las funciones anidadas.

        `ast.walk` no sirve aqui: desde el modulo alcanza el interior de todas las funciones,
        y entonces el `COLS` de una se le atribuye a los INSERT de las otras. Asi cada nodo
        pertenece a un ambito y a uno solo.
        """
        pila = list(ast.iter_child_nodes(raiz))
        while pila:
            n = pila.pop()
            yield n
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                pila.extend(ast.iter_child_nodes(n))

    out = []
    for f in FUENTES:
        ruta = REPO / f
        if not ruta.exists():
            continue
        arbol = ast.parse(ruta.read_text(encoding="utf-8"))
        ambitos = [arbol] + [n for n in ast.walk(arbol)
                             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for amb in ambitos:
            nodos = list(_del_ambito(amb))
            propias = []
            for n in nodos:
                if (isinstance(n, ast.Assign) and isinstance(n.value, (ast.Tuple, ast.List))
                        and getattr(n.targets[0], "id", "").upper() in ("COLS", "CAMPOS")):
                    v = [e.value for e in n.value.elts
                         if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                    if v:
                        propias = v
            for n in nodos:
                txt, generado = _texto(n)
                if not txt or "INSERT INTO" not in txt.upper():
                    continue
                m = re.search(r"INSERT\s+INTO\s+(\w+)", txt, re.I)
                if not m:
                    continue
                mc = re.search(r"INSERT\s+INTO\s+\w+\s*\(([^)]*)\)", txt, re.I | re.S)
                cols = [c.strip() for c in mc.group(1).split(",") if c.strip()] if mc else []
                if generado and propias and not cols:
                    cols = propias      # el f-string dejaba el hueco donde iban los nombres
                ev = bool(re.search(r"VALUES\s*%s", txt, re.I))
                out.append((f, m.group(1), cols,
                            txt.count("%s") - (1 if ev else 0), ev, generado))
    return out


@prueba("esquema", "las tablas app_* existen", base=True)
def _t_tablas(ctx):
    ctx["cur"].execute("SELECT table_name FROM information_schema.tables "
                       r"WHERE table_name LIKE 'app\_%'")
    hay = {r[0] for r in ctx["cur"].fetchall()}
    quiere = {t for _, t, _, _, _, _ in _inserts_del_codigo() if t.startswith("app_")}
    faltan = quiere - hay
    if faltan:
        raise AssertionError(f"el codigo escribe en tablas que no existen: {sorted(faltan)}")
    return f"{len(hay)} tablas · el codigo usa {len(quiere)}"


@prueba("esquema", "cada columna que el codigo escribe existe en su tabla", base=True)
def _t_columnas(ctx):
    ctx["cur"].execute("SELECT table_name, column_name FROM information_schema.columns "
                       r"WHERE table_name LIKE 'app\_%'")
    real: dict = {}
    for t, c in ctx["cur"].fetchall():
        real.setdefault(t, set()).add(c)
    malas, n = [], 0
    for f, tabla, cols, _, _, _ in _inserts_del_codigo():
        if tabla not in real:
            continue
        for c in cols:
            n += 1
            if c not in real[tabla]:
                malas.append(f"{tabla}.{c} (en {Path(f).name})")
    if malas:
        raise AssertionError("el codigo escribe columnas que la tabla no tiene: "
                             + ", ".join(malas))
    return f"{n} columnas verificadas"


@prueba("esquema", "los INSERT literales tienen tantos huecos como columnas")
def _t_huecos(_ctx):
    """EL FALLO DE HOY, en estatico: sin base y sin esperar a que termine el solver.

    Solo los literales. Los que se arman desde una lista de columnas no pueden descuadrarse
    -- los `%s` se generan de esa misma lista -- y `VALUES %s` lo expande psycopg2. Contar
    ahi solo produce falsos positivos, que es la forma mas rapida de que nadie vuelva a
    mirar la salida de este script.
    """
    malos, n, gen = [], 0, 0
    for f, tabla, cols, hue, ev, generado in _inserts_del_codigo():
        if ev or generado or not cols:
            gen += 1
            continue
        n += 1
        if hue != len(cols):
            malos.append(f"{Path(f).name}:{tabla} tiene {len(cols)} columnas y {hue} huecos")
    if malos:
        raise AssertionError("; ".join(malos))
    return f"{n} literales cuadrados · {gen} generados, a salvo por construccion"


@prueba("esquema", "los ejes de hora nominal son TIMESTAMP sin zona", base=True)
def _t_tipos(ctx):
    """La trampa del cambio de hora, convertida en comprobacion.

    La curva y el despacho viven en una rejilla NOMINAL de 24 horas por dia: el cambio de
    hora ya se resolvio al construir la matriz. Con TIMESTAMPTZ, el domingo de marzo en que
    las 02:00 no existen, Postgres mete las 02:00 y las 03:00 en la MISMA marca y revienta la
    clave primaria. `spot_price` y `predictions` si son instantes reales y llevan zona.
    """
    ctx["cur"].execute(
        "SELECT table_name, data_type FROM information_schema.columns "
        "WHERE column_name = 'datetime' AND table_name IN "
        "('app_curve_hourly','app_case_dispatch')")
    filas = ctx["cur"].fetchall()
    if not filas:
        raise AssertionError("ni app_curve_hourly ni app_case_dispatch tienen `datetime`")
    malas = [f"{t} es {d}" for t, d in filas if "with time zone" in d]
    if malas:
        raise AssertionError("; ".join(malas) + " -- reventara el dia del cambio de hora")
    return f"{len(filas)} ejes sin zona"


@prueba("esquema", "hay UNA sola curva publicada", base=True)
def _t_una_curva(ctx):
    ctx["cur"].execute("SELECT count(*) FROM app_curve")
    n = ctx["cur"].fetchone()[0]
    if n != 1:
        raise AssertionError(f"app_curve tiene {n} filas y debe tener exactamente 1")
    return "1 fila, como impone el indice unico"


# ══════════════════════════════════════════════════════════════════════════════
#  CURVA · que el artefacto y la base digan lo mismo
# ══════════════════════════════════════════════════════════════════════════════

@prueba("curva", "el artefacto abre y cuadra con su meta")
def _t_artefacto(ctx):
    from generar_curva import leer
    sims, idx, meta = leer()
    ctx["curva"] = (sims, idx, meta)
    if len(sims) != meta["escenarios"]:
        raise AssertionError(f"{len(sims)} escenarios en el .npy y "
                             f"{meta['escenarios']} en el meta")
    if sims.shape[1] != len(idx):
        raise AssertionError(f"{sims.shape[1]} horas por escenario y {len(idx)} en el indice")
    if not np.isfinite(sims).all():
        raise AssertionError(f"{(~np.isfinite(sims)).sum()} valores no finitos en el .npy")
    return (f"{sims.shape[0]}x{sims.shape[1]} · {meta['desde']} -> {meta['hasta']} · "
            f"media {sims.mean():.1f} EUR/MWh")


@prueba("curva", "la rejilla es de 24 horas por dia, sin huecos ni repetidos")
def _t_rejilla(ctx):
    _, idx, _ = ctx["curva"]
    d = pd.DataFrame({"dia": pd.to_datetime(idx.dia), "hora": np.asarray(idx.hora)})
    n = d.groupby("dia").size()
    if not (n == 24).all():
        mal = n[n != 24]
        raise AssertionError(f"{len(mal)} dias sin 24 horas: {mal.head(3).to_dict()}")
    if d.duplicated().any():
        raise AssertionError(f"{int(d.duplicated().sum())} pares (dia, hora) repetidos")
    dias = pd.DatetimeIndex(n.index)
    hueco = pd.date_range(dias.min(), dias.max()).difference(dias)
    if len(hueco):
        raise AssertionError(f"faltan {len(hueco)} dias del calendario, el primero "
                             f"{hueco[0].date()}")
    # los domingos de cambio de hora: en una rejilla nominal tienen 24 como todos
    cambios = sum(1 for x in dias
                  if x.month in (3, 10) and x.dayofweek == 6 and x.day >= 25)
    return f"{len(dias)} dias x 24 h · {cambios} domingos de cambio de hora, con 24"


@prueba("curva", "no hay hueco entre el ultimo precio conocido y la curva")
def _t_cubre(ctx):
    """La pregunta no es "¿cubre mañana?" sino "¿queda algun dia sin precio?".

    Un caso se arma con TRES fuentes pegadas por fecha: `spot_price` para lo ya ocurrido,
    `predictions` para D+1, y la curva de ahi en adelante. La curva NO tiene por que empezar
    mañana -- mañana lo cubre la prediccion -- y de hecho no debe: la matriz se construye
    `--hasta mañana`, asi que la curva arranca en el dia siguiente y las dos encajan sin
    solaparse.

    Comparar el arranque de la curva contra mañana da un fallo donde no lo hay. Lo que hay
    que medir es el HUECO entre el ultimo dia con precio de otra fuente y el primero de la
    curva, y ese hueco tiene que ser cero.
    """
    _, _, meta = ctx["curva"]
    desde = pd.to_datetime(meta["desde"]).date()
    obs = pd.to_datetime(meta["ultimo_dato_observado"]).date()
    frontera = date.today() + timedelta(days=1)      # hasta donde llega `predictions`

    if desde != obs + timedelta(days=1):
        raise AssertionError(f"la curva empieza el {desde} y la matriz acaba el {obs}: "
                             f"deberian ir pegadas")
    hueco = (desde - frontera).days - 1
    if hueco > 0:
        raise AssertionError(f"{hueco} dias sin precio entre la prediccion ({frontera}) y "
                             f"el arranque de la curva ({desde}): la matriz esta parada")
    atraso = (date.today() - obs).days
    if atraso > 1:
        raise Aviso(f"la matriz acaba el {obs}, hace {atraso} dias: la curva sigue "
                    f"generandose pero sus anclas envejecen sin que nada avise")
    return f"matriz hasta {obs} -> curva desde {desde} · sin hueco"


@prueba("curva", "los percentiles de la base son los del .npy", base=True)
def _t_percentiles(ctx):
    """La comprobacion que valida la publicacion entera, de punta a punta."""
    sims, _, meta = ctx["curva"]
    ctx["cur"].execute("SELECT generated_at, n_scenarios FROM app_curve")
    fila = ctx["cur"].fetchone()
    if fila is None:
        raise AssertionError("no hay ninguna curva registrada en app_curve")
    gen_db, n_db = fila

    def _utc(x):
        """A UTC de verdad, venga con zona o sin ella.

        `tz_localize(None)` DESCARTA la zona en vez de convertirla, y aqui las dos fechas
        vienen en zonas distintas: el `meta.json` guarda UTC y Postgres devuelve la sesion
        en hora de Madrid. Comparandolas asi salian dos horas de diferencia en verano --
        el mismo instante, contado dos veces-- y la comprobacion avisaba de que eran
        publicaciones distintas cuando era exactamente la misma.
        """
        t = pd.Timestamp(x)
        return t.tz_convert("UTC") if t.tzinfo else t.tz_localize("UTC")

    desfase = abs((_utc(gen_db) - _utc(meta["generado"])).total_seconds())
    if desfase > 60:
        raise Aviso(f"el .npy de aqui es de {meta['generado'][:16]} con {len(sims)} "
                    f"escenarios y la curva publicada es de "
                    f"{pd.Timestamp(gen_db):%Y-%m-%d %H:%M} con {n_db}: son publicaciones "
                    f"distintas y no se pueden comparar desde esta maquina")
    d = pd.read_sql("SELECT datetime, p10, p50, p90 FROM app_curve_hourly "
                    "ORDER BY datetime", ctx["con"])
    if len(d) != sims.shape[1]:
        raise AssertionError(f"{len(d)} filas en la base y {sims.shape[1]} horas en el .npy")
    esp = np.percentile(sims, [10, 50, 90], axis=0)
    err = max(float(np.abs(d[c].to_numpy() - esp[i]).max())
              for i, c in enumerate(("p10", "p50", "p90")))
    if err > 0.05:                       # REAL en Postgres: ~7 digitos significativos
        raise AssertionError(f"los percentiles difieren hasta {err:.3f} EUR/MWh")
    return f"{len(d)} filas · desvio maximo {err:.4f} EUR/MWh"


# ══════════════════════════════════════════════════════════════════════════════
#  PERFIL · 24 horas por dia siempre, pase lo que pase con el calendario
# ══════════════════════════════════════════════════════════════════════════════

def _csv_sintetico(ruta: Path, ano: int, valor=1.0) -> pd.DataFrame:
    """Un año entero en formato largo, con las horas españolas DE VERDAD.

    Convencion oficial del sector: 1..24, salvo el domingo de octubre que trae 25 -- las dos
    veces que se viven las 2:00 -- y el de marzo que trae 23. Es exactamente lo que llega en
    los ficheros de las distribuidoras y lo que rompe cualquier codigo que asuma 24 fijas.

    Se construye contando las horas reales del dia en Europe/Madrid en vez de codificar a
    mano que dias son: asi la prueba sigue valiendo para cualquier año que se le pase.
    """
    filas = []
    for dia in pd.date_range(f"{ano}-01-01", f"{ano}-12-31"):
        a = pd.Timestamp(dia).tz_localize("Europe/Madrid", nonexistent="shift_forward")
        b = (pd.Timestamp(dia) + pd.Timedelta(days=1)).tz_localize(
            "Europe/Madrid", nonexistent="shift_forward")
        n = round((b - a).total_seconds() / 3600)
        filas += [(dia.date(), h, valor) for h in range(1, n + 1)]
    d = pd.DataFrame(filas, columns=["fecha", "hora", "consumo_kwh"])
    d.to_csv(ruta, index=False)
    return d


@prueba("perfil", "año bisiesto, dia de 23 h y dia de 25 h -> 24 h por dia")
def _t_perfil(ctx):
    from cargar_perfil import cargar
    detalle = []
    for ano in (2028, 2027):             # 2028 bisiesto, 2027 normal
        f = ctx["tmp"] / f"perfil_{ano}.csv"
        crudo = _csv_sintetico(f, ano)
        s = cargar(f, verbose=False)
        n = s.groupby("dia").size()
        if not (n == 24).all():
            mal = n[n != 24]
            raise AssertionError(f"{ano}: {len(mal)} dias sin 24 h -> "
                                 f"{ {str(k.date()): int(v) for k, v in mal.head(3).items()} }")
        esperados = 366 if ano % 4 == 0 else 365
        if len(n) != esperados:
            raise AssertionError(f"{ano}: {len(n)} dias y el calendario tiene {esperados}")
        if int(s.hora.min()) != 0 or int(s.hora.max()) != 23:
            raise AssertionError(f"{ano}: la hora va de {int(s.hora.min())} a "
                                 f"{int(s.hora.max())} y debe ir de 0 a 23")
        # y la energia no se puede perder por el camino del cambio de hora
        e_in = crudo.consumo_kwh.sum() / 1000
        e_out = float(s.valor.sum())
        if abs(e_in - e_out) / e_in > 0.006:
            raise AssertionError(f"{ano}: entraron {e_in:.3f} MWh y salieron {e_out:.3f}")
        detalle.append(f"{ano}: {len(crudo)} h del fichero -> {len(s)} en rejilla "
                       f"({len(n)} d), {e_out:.2f} MWh")
    return " · ".join(detalle)


@prueba("perfil", "la proyeccion respeta el anual pedido y la rejilla")
def _t_proyeccion(ctx):
    from cargar_perfil import cargar, a_forma, proyectar
    s = cargar(ctx["tmp"] / "perfil_2027.csv", verbose=False)
    forma = a_forma(s)
    p = proyectar(forma, anual_mwh=350.0, desde="2027-01-01", hasta="2029-12-31",
                  crecimiento_pct=0.0)
    n = p.groupby("dia").size()
    if not (n == 24).all():
        raise AssertionError(f"{int((n != 24).sum())} dias proyectados sin 24 horas")
    anual = float(p.valor.sum()) / 3
    if abs(anual - 350) / 350 > 0.02:
        raise AssertionError(f"pedidos 350 MWh/año y salen {anual:.1f}")
    # 2028 es bisiesto: tiene que traer un dia mas que 2027, no el mismo calendario repetido
    por_ano = p.groupby(p.dia.dt.year).dia.nunique()
    if por_ano.get(2028) != 366:
        raise AssertionError(f"2028 es bisiesto y la proyeccion le da "
                             f"{por_ano.get(2028)} dias")
    return f"{len(n)} dias x 24 h · {anual:.1f} MWh/año · 2028 con 366 dias"


# ══════════════════════════════════════════════════════════════════════════════
#  OPTIMIZADOR · las leyes que el despacho no puede violar
# ══════════════════════════════════════════════════════════════════════════════

def _despacho(ctx, dias=30, politica="libre"):
    """Un despacho real sobre la curva publicada, para medir invariantes encima.

    Se cachea por (dias, politica) porque cada resolucion cuesta unos segundos y varias
    comprobaciones miran el mismo despacho desde angulos distintos.
    """
    clave = ("despacho", dias, politica)
    if clave in ctx:
        return ctx[clave]
    from optimiza_bateria import optimizar, BATERIA, SITIO
    sims, _, _ = ctx["curva"]
    px = sims[0].reshape(-1, 24)[:dias]
    bat = dict(BATERIA, potencia_mw=0.1, duracion_h=4.0)
    sitio = dict(SITIO, consumo_anual_mwh=350.0, fv_mwp=0.25, politica_carga=politica)
    L = np.full((dias, 24), 350.0 / 8760)
    # una campana solar cruda: no pretende ser realista, solo garantizar que a mediodia
    # sobra generacion y por la noche falta, que es lo que la bateria tiene que explotar
    h = np.arange(24)
    G = np.repeat((0.25 * np.exp(-((h - 13.5) ** 2) / 8.0))[None, :], dias, axis=0)
    out, det = optimizar(px, bat=bat, sitio=sitio, consumo=L, generacion=G, detalle=True)
    ctx[clave] = (out, det, bat, sitio, L, G)
    return ctx[clave]


@prueba("optimizador", "balance horario: G + descarga + imp = L + carga + exp")
def _t_balance(ctx):
    """La primera ley. Si esto falla, el despacho esta creando o destruyendo energia."""
    _, det, _, _, L, G = _despacho(ctx)
    r = (G + det["descarga"] + det["imp"]) - (L + det["carga"] + det["exp"])
    err = float(np.abs(r).max())
    if err > 1e-7:
        i = np.unravel_index(np.abs(r).argmax(), r.shape)
        raise AssertionError(f"desbalance de {err:.2e} MWh en el dia {i[0]}, hora {i[1]}")
    return f"{r.size} horas · residuo maximo {err:.1e} MWh"


@prueba("optimizador", "recursion del estado de carga y limites del SoC")
def _t_soc(ctx):
    """soc_t = soc_{t-1} + sqrt(n)*carga - descarga/sqrt(n), arrancando en soc_min.

    El rendimiento de ida y vuelta se reparte en dos raices, una al cargar y otra al
    descargar. Comprobarlo aqui es lo que garantiza que el 90% declarado es el 90% aplicado:
    si el motor lo pusiera entero en un solo lado, el margen saldria distinto y nada mas lo
    delataria.
    """
    _, det, bat, _, _, _ = _despacho(ctx)
    er = np.sqrt(bat["eficiencia_rt"])
    E = bat["potencia_mw"] * bat["duracion_h"]
    soc = det["soc"].ravel()
    c, d = det["carga"].ravel(), det["descarga"].ravel()
    prev = np.concatenate([[E * bat["soc_min"]], soc[:-1]])
    r = soc - (prev + er * c - d / er)
    if float(np.abs(r).max()) > 1e-7:
        raise AssertionError(f"la recursion del SoC falla por {np.abs(r).max():.2e} MWh")
    lo, hi = E * bat["soc_min"], E * bat["soc_max"]
    if soc.min() < lo - 1e-9 or soc.max() > hi + 1e-9:
        raise AssertionError(f"SoC fuera de [{lo:.3f}, {hi:.3f}]: llega a "
                             f"[{soc.min():.4f}, {soc.max():.4f}]")
    return (f"residuo {np.abs(r).max():.1e} · SoC en [{soc.min():.3f}, {soc.max():.3f}] "
            f"de [{lo:.3f}, {hi:.3f}]")


@prueba("optimizador", "las potencias respetan carga y descarga maximas")
def _t_potencia(ctx):
    _, det, bat, _, _, _ = _despacho(ctx)
    PC = bat["potencia_mw"] * bat["p_carga_max_pct"] / 100
    PD = bat["potencia_mw"] * bat["p_descarga_max_pct"] / 100
    if det["carga"].max() > PC + 1e-9:
        raise AssertionError(f"carga de {det['carga'].max():.4f} MW sobre un tope de {PC}")
    if det["descarga"].max() > PD + 1e-9:
        raise AssertionError(f"descarga de {det['descarga'].max():.4f} MW sobre {PD}")
    sim = int(((det["carga"] > 1e-6) & (det["descarga"] > 1e-6)).sum())
    if sim:
        raise Aviso(f"{sim} horas cargando y descargando a la vez -- el LP lo permite si el "
                    f"precio es negativo, porque quemar energia da dinero; con `--p-min` "
                    f"pasa a MILP y queda prohibido")
    return (f"carga max {det['carga'].max():.4f} de {PC} · "
            f"descarga {det['descarga'].max():.4f} de {PD}")


@prueba("optimizador", "'solo excedente' no carga ni un MWh de la red")
def _t_solo_excedente(ctx):
    """La politica no es una preferencia del solver: es una cota dura de la variable."""
    _, det, _, _, L, G = _despacho(ctx, politica="solo_excedente")
    sobra = np.maximum(G - L, 0.0)
    exceso = float(np.maximum(det["carga"] - sobra, 0.0).sum())
    if exceso > 1e-7:
        raise AssertionError(f"carga {exceso:.5f} MWh por encima del excedente disponible")
    return f"{det['carga'].sum():.2f} MWh cargados, todos de excedente"


@prueba("optimizador", "el margen guardado es el que sale al recalcularlo")
def _t_margen(ctx):
    """Cierra el circulo: lo que se escribe en la tabla es lo que hizo el despacho."""
    from optimiza_bateria import coste_ciclo
    out, det, bat, sitio, _, _ = _despacho(ctx)
    p_imp = det["precio"] + sitio["recargo_tarifa"]
    p_exp = det["precio"] * sitio["precio_excedente_pct"] / 100
    m = ((p_exp * det["exp"]).sum(axis=1) - (p_imp * det["imp"]).sum(axis=1)
         - coste_ciclo(bat) * det["descarga"].sum(axis=1))
    err = float(np.abs(m - out.margen_eur.to_numpy()).max())
    if err > 1e-6:
        raise AssertionError(f"el margen difiere hasta {err:.2e} EUR/dia")
    return f"{len(out)} dias · desvio {err:.1e} EUR"


# ══════════════════════════════════════════════════════════════════════════════
#  ECONOMIA · el optimo es optimo, contra dos casos resueltos a mano
# ══════════════════════════════════════════════════════════════════════════════

@prueba("economia", "con precio plano la bateria no hace nada")
def _t_plano(_ctx):
    """Sin diferencial no hay arbitraje: cualquier ciclo pierde por rendimiento y desgaste.

    Es la prueba mas barata de que la funcion objetivo tiene los signos donde deben estar.
    Un optimizador con el signo cambiado cicla como un loco justo aqui.
    """
    from optimiza_bateria import optimizar, BATERIA
    out = optimizar(np.full((7, 24), 50.0), bat=dict(BATERIA, potencia_mw=1.0))
    if float(out.descarga_mwh.sum()) > 1e-6:
        raise AssertionError(f"ha descargado {out.descarga_mwh.sum():.4f} MWh con el precio "
                             f"plano a 50 EUR/MWh")
    if abs(float(out.margen_eur.sum())) > 1e-6:
        raise AssertionError(f"margen de {out.margen_eur.sum():.4f} EUR y deberia ser 0")
    return "0 ciclos y 0 EUR en 7 dias, como debe ser"


@prueba("economia", "un valle y un pico: el optimo coincide con el calculo a mano")
def _t_analitico(_ctx):
    """Doce horas baratas, doce caras, y una bateria que cabe entera en cada tramo.

    El optimo es evidente y se escribe en tres lineas, asi que sirve de referencia exacta:

        entrada = E_util / sqrt(n)                 lo que hay que COMPRAR para llenarla
        salida  = E_util * sqrt(n)                 lo que se puede VENDER al vaciarla
        margen  = p_alto*salida - p_bajo*entrada - cc*salida

    `ventana=1` para que el estado de carga cierre cada dia y no se guarde nada de un dia
    para otro, que es lo que hace comparable el numero con el calculo de arriba.
    """
    from optimiza_bateria import optimizar, BATERIA, coste_ciclo
    bat = dict(BATERIA, potencia_mw=1.0, duracion_h=4.0)
    E = bat["potencia_mw"] * bat["duracion_h"]
    eu = E * (bat["soc_max"] - bat["soc_min"])
    er = np.sqrt(bat["eficiencia_rt"])
    p_bajo, p_alto = 10.0, 120.0
    cc = coste_ciclo(bat)

    salida, entrada = eu * er, eu / er
    esperado = p_alto * salida - p_bajo * entrada - cc * salida
    out = optimizar(np.array([[p_bajo] * 12 + [p_alto] * 12]), bat=bat, ventana=1)
    got = float(out.margen_eur.iloc[0])
    if abs(got - esperado) > 0.02:
        raise AssertionError(f"el optimo da {got:.3f} EUR y a mano salen {esperado:.3f}")
    if abs(float(out.descarga_mwh.iloc[0]) - salida) > 1e-4:
        raise AssertionError(f"descarga {out.descarga_mwh.iloc[0]:.4f} MWh y deberia "
                             f"descargar {salida:.4f}")
    return f"{got:.2f} EUR frente a {esperado:.2f} calculados a mano"


@prueba("economia", "el doble de bateria no da mas del doble de margen")
def _t_concavidad(ctx):
    """Concavidad: los huecos buenos del dia son finitos y una bateria mayor coge los peores.

    Si al duplicar la potencia el margen se multiplicara por MAS de dos, el solver no estaria
    arbitrando sino explotando algun artefacto -- una hora con precio imposible, un limite
    mal puesto. Es una comprobacion de sensatez sobre la curva ademas de sobre el motor.
    """
    from optimiza_bateria import optimizar, BATERIA
    sims, _, _ = ctx["curva"]
    px = sims[0].reshape(-1, 24)[:60]
    m = {p: float(optimizar(px, bat=dict(BATERIA, potencia_mw=p)).margen_eur.sum())
         for p in (0.1, 0.2)}
    if m[0.1] <= 0:
        raise Aviso(f"margen no positivo en standalone: {m[0.1]:.1f} EUR en 60 dias")
    r = m[0.2] / m[0.1]
    if r > 2.001:
        raise AssertionError(f"al doblar la potencia el margen se multiplica por {r:.4f}")
    return f"x{r:.3f} al doblar la potencia · tope teorico x2"


# ══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", action="store_true",
                    help="incluir las comprobaciones que necesitan Postgres")
    ap.add_argument("--grupo",
                    help="solo uno: esquema | curva | perfil | optimizador | economia")
    ap.add_argument("--traza", action="store_true", help="volcar la traza de los fallos")
    a = ap.parse_args()

    ctx = {"tmp": Path(tempfile.mkdtemp(prefix="comprobar_"))}
    con = cur = None
    if a.base:
        from caso import conexion
        con = conexion()
        cur = con.cursor()
        ctx["con"], ctx["cur"] = con, cur

    print(f"\n  comprobacion de la cadena · {date.today():%Y-%m-%d}")
    print(f"  {'con base' if a.base else 'sin base -- añade --base para incluirla'}\n")

    ok = fallo = avisos = saltadas = 0
    grupo_ant = None
    try:
        for grupo, nombre, f, necesita_db in PRUEBAS:
            if a.grupo and grupo != a.grupo:
                continue
            if grupo != grupo_ant:
                print(f"  {grupo.upper()}")
                grupo_ant = grupo
            if necesita_db and not a.base:
                print(f"    {GRIS}··{FIN}  {nombre:<54} {GRIS}necesita --base{FIN}")
                saltadas += 1
                continue
            try:
                det = f(ctx)
                print(f"    {VERDE}ok{FIN}   {nombre:<54} {GRIS}{det}{FIN}")
                ok += 1
            except Aviso as e:
                print(f"    {AMAR}··{FIN}   {nombre}")
                print(f"         {AMAR}{e}{FIN}")
                avisos += 1
            except Exception as e:
                print(f"    {ROJO}FALLA{FIN} {nombre}")
                print(f"         {ROJO}{e}{FIN}")
                if a.traza:
                    traceback.print_exc()
                fallo += 1
    finally:
        if con is not None:
            con.rollback()
            cur.close()
            con.close()

    print(f"\n  {ok} pasan · {fallo} fallan · {avisos} avisos · {saltadas} saltadas\n")
    raise SystemExit(1 if fallo else 0)


if __name__ == "__main__":
    main()
