"""Optimizacion de una bateria sobre una curva ya publicada. Dos modos, un solo motor.

QUE HACE
Lee la curva del artefacto (`production/curva/generar_curva.py`), le aplica una ficha de bateria y devuelve
el despacho optimo. NO reconstruye la curva: eso lo hace el cron una vez al dia. Optimizar
deja de necesitar la matriz, la base ni los modelos -- le basta el precio.

LOS DOS MODOS SON EL MISMO LP
El balance de energia en el punto de medida es:

    generacion + descarga + importacion  =  consumo + carga + exportacion

`standalone` es el caso `generacion = consumo = 0` y `precio_export = precio_import`: la
bateria compra y vende al mercado y nada mas. Mantener dos formulaciones separadas garantiza
que se desincronicen, asi que hay una sola y dos entradas de linea de comandos.

Y de ese balance sale gratis algo que suele modelarse a mano: **no hace falta decidir si se
carga del excedente o de la red**. Si la importacion de esa hora es cero, cargo del excedente.
La politica "solo excedente" es entonces una restriccion de una linea:

    carga <= max(0, generacion - consumo)

LA POTENCIA MINIMA CONVIERTE ESTO EN UN MILP
Un inversor que no modula por debajo del 10 % de su nominal no tiene una cota: tiene "o cero
o al menos eso", que necesita una binaria por hora. Medido sobre 90 dias: cuesta el 0,04 % de
margen y multiplica el tiempo por 20-40. Por eso `p_min_pct = 0` por defecto -- se enciende
cuando hace falta un perfil fisicamente realizable, no para calcular el margen.

    python scripts/optimiza_bateria.py --modo standalone --escenarios 20
    python scripts/optimiza_bateria.py --modo autoconsumo --perfil consumo.xlsx \
           --consumo-anual 350 --fv-mwp 0.25 --politica solo_excedente
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.optimize import Bounds, LinearConstraint, linprog, milp

REPO = Path(__file__).resolve().parents[2]
# `production/app/x.py` -> el repo esta dos niveles arriba. Los MOTORES viven en `scripts/`
# y no se duplican aqui: los comparten los notebooks, y dos copias acabarian divergiendo.
sys.path.append(str(REPO / "ingesta"))
sys.path.append(str(REPO / "scripts"))
sys.path.append(str(Path(__file__).resolve().parent))   # los hermanos de esta carpeta
sys.path.append(str(REPO / "production" / "curva"))     # `generar_curva`, que publica
                                                        # el artefacto que aqui se lee

BLOQUE = 365          # dias por resolucion; solo afecta a memoria, no al resultado
VENTANA = 7           # dias entre cierres del estado de carga


# ─────────────────────────────────────────────────────────────────────────────
# la ficha
# ─────────────────────────────────────────────────────────────────────────────

BATERIA = dict(
    potencia_mw        = 1.0,      # la NOMINAL. Todo lo demas va en % de ella.
    duracion_h         = 4.0,      # E = potencia x duracion
    p_carga_max_pct    = 100.0,    # muchos sistemas cargan mas despacio de lo que descargan
    p_descarga_max_pct = 100.0,
    p_min_pct          = 0.0,      # minimo tecnico del inversor. >0 convierte esto en MILP.
    eficiencia_rt      = 0.90,
    soc_min            = 0.05,
    soc_max            = 0.95,
    ciclos_vida        = 6000,
    degrada_1000cic    = 3.0,
    capex_eur_mwh      = 200_000,
    degrada_anual      = 1.5,
)

SITIO = dict(
    consumo_anual_mwh    = 0.0,
    fv_mwp               = 0.0,
    crecimiento_pct      = 1.0,    # del consumo, anual
    degradacion_fv_pct   = 0.5,    # de la generacion, anual
    # Peajes, cargos y margen de comercializadora sobre el spot al IMPORTAR. Del orden de
    # 60-90 EUR/MWh en un suministro español segun tarifa de acceso y potencia contratada.
    recargo_tarifa       = 70.0,
    # Lo que pagan por EXPORTAR, en % del spot. La compensacion simplificada de excedentes
    # suele quedarse bastante por debajo del precio de mercado.
    #
    # LA DIFERENCIA ENTRE ESTOS DOS NUMEROS ES EL NEGOCIO DEL AUTOCONSUMO. Si fueran
    # iguales, importar y exportar costarian lo mismo, el emplazamiento seria neutro y el
    # valor de la bateria se reduciria EXACTAMENTE al arbitraje del modo standalone. Se ha
    # comprobado: con recargo 0 y excedente al 100 %, los dos modos dan la misma cifra.
    precio_excedente_pct = 80.0,
    politica_carga       = "libre",   # libre | prefiere_excedente | solo_excedente
    # Topes fisicos de la acometida. `None` = sin limite.
    potencia_contratada  = None,   # MW: no se puede importar mas que esto en una hora
    limite_vertido       = None,   # MW: ni exportar mas que esto
)


def coste_ciclo(bat=BATERIA) -> float:
    """EUR por MWh descargado. Sin esto el optimizador cicla gratis y quema la bateria."""
    E = bat["potencia_mw"] * bat["duracion_h"]
    e_util = E * (bat["soc_max"] - bat["soc_min"])
    return (E * bat["capex_eur_mwh"]) / (bat["ciclos_vida"] * e_util)


# ─────────────────────────────────────────────────────────────────────────────
# el motor
# ─────────────────────────────────────────────────────────────────────────────

def _bloque(precio, bat, cc, ventana, consumo=None, generacion=None, sitio=SITIO):
    """Un LP (o MILP) para un bloque de dias. `precio`, `consumo` y `generacion` son (dias,24).

    Variables por hora: carga, descarga, estado de carga, importacion, exportacion.
    Y dos binarias mas si hay potencia minima.

    El estado de carga va como VARIABLE con una restriccion de diferencia, no como suma
    acumulada: la version acumulada necesita una matriz triangular densa que para un año son
    613 MB y no resuelve.
    """
    d, h = precio.shape
    n = d * h
    p = precio.ravel()
    L = np.zeros(n) if consumo is None else consumo.ravel()
    G = np.zeros(n) if generacion is None else generacion.ravel()
    solo = consumo is None and generacion is None

    E = bat["potencia_mw"] * bat["duracion_h"]
    er = np.sqrt(bat["eficiencia_rt"])
    PC = bat["potencia_mw"] * bat["p_carga_max_pct"] / 100
    PD = bat["potencia_mw"] * bat["p_descarga_max_pct"] / 100
    pmin = bat["potencia_mw"] * bat["p_min_pct"] / 100
    s0 = E * bat["soc_min"]

    p_imp = p + (0.0 if solo else sitio["recargo_tarifa"])
    p_exp = p * (1.0 if solo else sitio["precio_excedente_pct"] / 100)

    # ── variables: c | d | soc | imp | exp   [ | zc | zd ] ────────────────────
    bin_ = pmin > 0
    nv = (7 if bin_ else 5) * n
    obj = np.zeros(nv)
    obj[n:2 * n] = cc                    # el desgaste, que se paga con cada MWh descargado
    obj[3 * n:4 * n] = p_imp             # comprar cuesta
    obj[4 * n:5 * n] = -p_exp            # vender ingresa
    # (linprog MINIMIZA, asi que el coste va en positivo y el ingreso en negativo)

    fi, co, va = [], [], []
    fila = 0
    # 1 · estado de carga:  soc_t - soc_{t-1} - er*c_t + d_t/er = 0
    for t in range(n):
        fi += [fila, fila, fila]; co += [t, n + t, 2 * n + t]; va += [-er, 1 / er, 1.0]
        if t:
            fi += [fila]; co += [2 * n + t - 1]; va += [-1.0]
        fila += 1
    beq = list(np.zeros(n)); beq[0] = s0
    # 2 · balance:  G + d + imp - L - c - exp = 0
    for t in range(n):
        fi += [fila, fila, fila, fila]
        co += [n + t, 3 * n + t, t, 4 * n + t]
        va += [1.0, 1.0, -1.0, -1.0]
        beq.append(float(L[t] - G[t]))
        fila += 1
    # 3 · cerrar el estado de carga al final de cada ventana
    cierres = list(range(ventana * h - 1, n, ventana * h))
    if not cierres or cierres[-1] != n - 1:
        cierres.append(n - 1)
    for t in cierres:
        fi += [fila]; co += [2 * n + t]; va += [1.0]; beq.append(s0); fila += 1

    Aeq = sparse.csr_matrix((va, (fi, co)), shape=(fila, nv))
    beq = np.asarray(beq)

    lb = np.zeros(nv)
    lb[2 * n:3 * n] = E * bat["soc_min"]
    # Topes de la acometida. Van como cota de variable, que es lo mas barato para el solver:
    # una columna acotada no añade fila a la matriz. Sin ellos, el optimizador cargaria a
    # plena potencia en la hora mas barata aunque el suministro no diera para tanto, y el
    # despacho resultante dispararia el ICP en la vida real.
    tope_imp = sitio.get("potencia_contratada") if not solo else None
    tope_exp = sitio.get("limite_vertido") if not solo else None
    ub = np.concatenate([np.full(n, PC), np.full(n, PD), np.full(n, E * bat["soc_max"]),
                         np.full(n, np.inf if tope_imp is None else float(tope_imp)),
                         np.full(n, np.inf if tope_exp is None else float(tope_exp))]
                        + ([np.ones(2 * n)] if bin_ else []))

    # ── politica de carga ─────────────────────────────────────────────────────
    if not solo and sitio["politica_carga"] == "solo_excedente":
        # no puede cargar mas de lo que sobra de la generacion en esa hora
        ub[:n] = np.minimum(ub[:n], np.maximum(G - L, 0.0))
    elif not solo and sitio["politica_carga"] == "prefiere_excedente":
        # no se prohibe cargar de red: se penaliza, para que solo lo haga si compensa mucho
        obj[3 * n:4 * n] = p_imp + 1.0

    desig = []
    if bin_:
        # `pmin*z <= x <= pmax*z` con z binaria: o la maquina esta parada, o va por encima
        # de su minimo tecnico. Y `zc + zd <= 1`, que impide cargar y descargar a la vez --
        # el LP no lo hace nunca porque pierde eficiencia, pero con precios negativos si
        # podria salirle a cuenta quemar energia, y eso no es fisico.
        I_C, I_D = 0, n                     # donde empiezan carga y descarga
        Z_C, Z_D = 5 * n, 6 * n             # y sus binarias
        f2, c2, v2, lo, hi = [], [], [], [], []
        r = 0
        for t in range(n):
            for base, zbase, pmx in ((I_C, Z_C, PC), (I_D, Z_D, PD)):
                x, z = base + t, zbase + t
                f2 += [r, r]; c2 += [x, z]; v2 += [1.0, -pmx]; lo += [-np.inf]; hi += [0.]
                r += 1
                f2 += [r, r]; c2 += [x, z]; v2 += [1.0, -pmin]; lo += [0.]; hi += [np.inf]
                r += 1
            f2 += [r, r]; c2 += [Z_C + t, Z_D + t]; v2 += [1.0, 1.0]
            lo += [-np.inf]; hi += [1.0]; r += 1
        desig = [LinearConstraint(sparse.csr_matrix((v2, (f2, c2)), shape=(r, nv)),
                                  np.array(lo), np.array(hi))]

    if bin_:
        integ = np.zeros(nv); integ[5 * n:] = 1
        res = milp(c=obj, constraints=[LinearConstraint(Aeq, beq, beq)] + desig,
                   bounds=Bounds(lb, ub), integrality=integ,
                   options={"time_limit": 600, "mip_rel_gap": 1e-4})
        x = res.x
    else:
        res = linprog(obj, A_eq=Aeq, b_eq=beq, bounds=list(zip(lb, ub)), method="highs")
        x = res.x if res.success else None
    if x is None:
        # Casi siempre es un tope demasiado apretado: si el consumo de una hora supera la
        # potencia contratada y no hay generacion ni bateria que lo cubra, el balance no
        # tiene solucion. Devolver ceros en silencio daria un margen de cero que se
        # confundiria con "no compensa ciclar".
        peor = float(np.max(L - G)) if not solo else 0.0
        raise RuntimeError(
            f"el despacho no tiene solucion en este bloque. "
            + (f"La hora de mayor demanda neta pide {peor:.3f} MW y la potencia contratada "
               f"es {tope_imp} MW." if tope_imp is not None and peor > float(tope_imp)
               else "Revisa los topes de acometida y de vertido."))

    r_ = lambda a: a.reshape(d, h)
    return dict(carga=r_(x[:n]), descarga=r_(x[n:2 * n]), soc=r_(x[2 * n:3 * n]),
                imp=r_(x[3 * n:4 * n]), exp=r_(x[4 * n:5 * n]))


def optimizar(precio, bat=BATERIA, sitio=SITIO, consumo=None, generacion=None,
              cc=None, ventana=VENTANA, bloque=BLOQUE, detalle=False):
    """Despacho optimo. `precio` es (dias, 24) en EUR/MWh.

    Devuelve un DataFrame por dia con el coste, el ingreso y los ciclos. Con `detalle=True`
    devuelve ademas los perfiles horarios.

    En `standalone` el "coste" es la compra de energia y el "ingreso" la venta. En
    `autoconsumo` el coste es la factura de la luz con bateria, y lo que importa es
    compararla con la factura SIN bateria -- que se calcula aparte, en `sin_bateria`.
    """
    px = np.atleast_2d(np.asarray(precio, dtype=float))
    cc = coste_ciclo(bat) if cc is None else cc
    solo = consumo is None and generacion is None
    E = bat["potencia_mw"] * bat["duracion_h"]
    e_util = E * (bat["soc_max"] - bat["soc_min"])

    trozos, perfiles = [], []
    for i in range(0, len(px), bloque):
        sl = slice(i, i + bloque)
        r = _bloque(px[sl], bat, cc, ventana,
                    None if solo else consumo[sl], None if solo else generacion[sl], sitio)
        p_ = px[sl]
        p_imp = p_ + (0.0 if solo else sitio["recargo_tarifa"])
        p_exp = p_ * (1.0 if solo else sitio["precio_excedente_pct"] / 100)
        trozos.append(pd.DataFrame({
            "compra_eur": (p_imp * r["imp"]).sum(axis=1),
            "venta_eur": (p_exp * r["exp"]).sum(axis=1),
            "desgaste_eur": cc * r["descarga"].sum(axis=1),
            "carga_mwh": r["carga"].sum(axis=1),
            "descarga_mwh": r["descarga"].sum(axis=1),
            "importado_mwh": r["imp"].sum(axis=1),
            "exportado_mwh": r["exp"].sum(axis=1),
            "ciclos": r["descarga"].sum(axis=1) / e_util}))
        if detalle:
            perfiles.append(r)
    out = pd.concat(trozos, ignore_index=True)
    out["margen_eur"] = out.venta_eur - out.compra_eur - out.desgaste_eur
    if detalle:
        junto = {k: np.vstack([p[k] for p in perfiles]) for k in perfiles[0]}
        junto["precio"] = px
        if not solo:
            junto["consumo"], junto["generacion"] = consumo, generacion
        return out, junto
    return out


def sin_bateria(precio, consumo, generacion, sitio=SITIO) -> pd.DataFrame:
    """La factura del emplazamiento SIN bateria. Es la referencia contra la que se compara.

    Sin bateria, cada hora se cubre el consumo con lo que genera y el resto se importa; lo
    que sobra se exporta. No hay decision que tomar: es aritmetica.
    """
    p = np.atleast_2d(np.asarray(precio, dtype=float))
    L, G = np.atleast_2d(consumo), np.atleast_2d(generacion)
    neto = L - G
    imp = np.maximum(neto, 0.0)
    exp = np.maximum(-neto, 0.0)
    p_imp = p + sitio["recargo_tarifa"]
    p_exp = p * sitio["precio_excedente_pct"] / 100
    return pd.DataFrame({
        "compra_eur": (p_imp * imp).sum(axis=1),
        "venta_eur": (p_exp * exp).sum(axis=1),
        "importado_mwh": imp.sum(axis=1),
        "exportado_mwh": exp.sum(axis=1),
        "margen_eur": (p_exp * exp - p_imp * imp).sum(axis=1)})


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--modo", default="standalone", choices=["standalone", "autoconsumo"])
    ap.add_argument("--escenarios", type=int, default=10)
    ap.add_argument("--desde"); ap.add_argument("--hasta")
    ap.add_argument("--duracion", type=float, help="horas de la bateria")
    ap.add_argument("--p-min-pct", type=float, default=0.0,
                    help="minimo tecnico en %% de la nominal; >0 pasa a MILP y va 20x mas lento")
    ap.add_argument("--perfil", help="Excel o CSV con el consumo (modo autoconsumo)")
    ap.add_argument("--perfil-fv", help="idem con la generacion; si falta se usa la solar")
    ap.add_argument("--consumo-anual", type=float, default=350.0, help="MWh/año")
    ap.add_argument("--fv-mwp", type=float, default=0.25)
    ap.add_argument("--politica", default="libre",
                    choices=["libre", "prefiere_excedente", "solo_excedente"])
    a = ap.parse_args()

    from generar_curva import leer
    sims, idx, meta = leer()
    dias = pd.DatetimeIndex(idx.dia.unique())
    px = sims[:a.escenarios].reshape(a.escenarios, -1, 24)
    m = np.ones(len(dias), bool)
    if a.desde: m &= dias >= pd.Timestamp(a.desde)
    if a.hasta: m &= dias <= pd.Timestamp(a.hasta)
    px, dias = px[:, m], dias[m]

    bat = dict(BATERIA)
    if a.duracion: bat["duracion_h"] = a.duracion
    bat["p_min_pct"] = a.p_min_pct
    cc = coste_ciclo(bat)

    print(f"\n  curva publicada el {meta['generado'][:10]} · "
          f"{meta['escenarios']} escenarios · {meta['desde']} -> {meta['hasta']}")
    print(f"  se usan {a.escenarios} escenarios x {len(dias):,} dias")
    print(f"  bateria {bat['potencia_mw']:.0f} MW / {bat['duracion_h']:.0f} h · "
          f"coste de ciclo {cc:.1f} EUR/MWh"
          + (f" · MILP con minimo {a.p_min_pct:.0f}%" if a.p_min_pct else ""))

    consumo = generacion = None
    sitio = dict(SITIO, politica_carga=a.politica)
    if a.modo == "autoconsumo":
        from cargar_perfil import cargar, a_forma, proyectar
        if not a.perfil:
            raise SystemExit("el modo autoconsumo necesita --perfil con la curva de consumo")
        f = a_forma(cargar(a.perfil, verbose=False))
        consumo = proyectar(f, a.consumo_anual, dias[0], dias[-1],
                            sitio["crecimiento_pct"]).valor.to_numpy().reshape(-1, 24)
        if a.perfil_fv:
            fg = a_forma(cargar(a.perfil_fv, verbose=False))
        else:
            fg = f.assign(pu=0.0)          # sin fichero de generacion no hay generacion
        generacion = proyectar(fg, a.fv_mwp * 1600, dias[0], dias[-1],
                               -sitio["degradacion_fv_pct"]).valor.to_numpy().reshape(-1, 24)
        sitio.update(consumo_anual_mwh=a.consumo_anual, fv_mwp=a.fv_mwp)
        print(f"  emplazamiento: {a.consumo_anual:,.0f} MWh/año · {a.fv_mwp:.2f} MWp · "
              f"politica '{a.politica}'")

    filas = []
    for s in range(len(px)):
        r = optimizar(px[s], bat, sitio, consumo, generacion, cc)
        base = (sin_bateria(px[s], consumo, generacion, sitio)
                if a.modo == "autoconsumo" else None)
        filas.append({"escenario": s, "margen": r.margen_eur.sum(),
                      "ciclos_dia": r.ciclos.mean(),
                      "ahorro": (r.margen_eur.sum() - base.margen_eur.sum())
                      if base is not None else np.nan})
    d = pd.DataFrame(filas)
    print(f"\n  {'':22s} {'P10':>12s} {'P50':>12s} {'P90':>12s}")
    print("  " + "-" * 54)
    col = "ahorro" if a.modo == "autoconsumo" else "margen"
    et = "ahorro con bateria" if a.modo == "autoconsumo" else "margen de arbitraje"
    print(f"  {et:22s} " + " ".join(f"{np.percentile(d[col], q):12,.0f}" for q in (10, 50, 90)))
    print(f"  {'ciclos por dia':22s} "
          + " ".join(f"{np.percentile(d.ciclos_dia, q):12.2f}" for q in (10, 50, 90)))
    print(f"\n  por MW instalado y año: "
          f"{d[col].median() / len(dias) * 365 / bat['potencia_mw']:,.0f} EUR")


if __name__ == "__main__":
    main()
