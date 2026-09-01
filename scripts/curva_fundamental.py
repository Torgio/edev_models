"""Curva de precio a futuro por FUNDAMENTALES, no por deformacion de la forma historica.

QUE CAMBIA RESPECTO A `curva_precios.py`
Aquel toma un nivel de precio como dato, estima la forma intradiaria de los ultimos dos años
y la ensancha con un factor que sale de la capacidad solar instalada. Funciona, pero tiene
tres defectos medidos:

  1. El conductor esta mal elegido. `spread ~ GW solares` da R2 0,53 y el propio modulo
     admite que eolica y solar crecen a la vez, asi que el tiempo las confunde. Medido
     sobre la matriz: la DEMANDA RESIDUAL correla +0,78 a +0,84 con el precio horario
     dentro de cada año; la solar sola, -0,52. Y quitando la residual de un ajuste
     `precio ~ f(residual, gas, CO2, hora)` el R2 fuera de muestra cae a -0,045: peor que
     predecir la media. Es ella quien hace el trabajo.

  2. No hay suelo en cero. Las horas a precio <= 0 han pasado del 0,00 % en 2020 al
     15,50 % en 2026, y un 3,82 % se casan a CERO EXACTO. Una distribucion continua no
     puede producir una masa puntual, asi que el valle de mediodia nunca llega al suelo y
     el spread proyectado sale corto -- justo donde la bateria gana el dinero.

  3. El nivel es un supuesto sobre el PRECIO. Aqui el nivel sale del modelo: lo que se
     aporta son gas, demanda y capacidad instalada, que es de lo que hay proyecciones
     publicadas (PNIEC) en vez de adivinanzas sobre el precio.

POR QUE LA RESIDUAL SI SE PUEDE PROYECTAR Y EL PRECIO NO
Porque se descompone en piezas con objetivo publicado y en meteorologia:

    residual(h, d, año) = demanda(año) x perfil(h, d)
                        - solar_GW(año)  x rendimiento_solar  x ssrd(h, d)
                        - eolica_GW(año) x rendimiento_eolico x v100(h, d)^3

LA TRAMPA QUE HAY QUE EVITAR, Y ES SUTIL
El factor de carga MEDIDO no sirve como entrada. Con el denominador correcto -- solo solar
de red, porque el autoconsumo no vierte a `ree_gsolar_mw` -- y comparando enero-agosto de
todos los años, cae de 0,194 en 2020 a 0,139 en 2025 mientras la capacidad se multiplica
por cinco. Esa caida es ENDOGENA: cuando el precio se va a cero las plantas vierten y dejan
de generar, asi que el factor de carga medido ya lleva dentro el efecto del precio que
queremos predecir. Proyectar con el contaria el recorte dos veces -- subestimaria la
renovable disponible, sobreestimaria la residual y devolveria un precio mas alto del que
habra.

Por eso aqui se usa RADIACION Y VIENTO, que son exogenos de verdad: el precio no cambia
cuanto sol hace. Y el recorte deja de ser un supuesto y pasa a ser una consecuencia --
cuando la residual se hunde, la curva de oferta devuelve cero, y ese cero ES el vertido.

    python scripts/curva_fundamental.py --ajuste          # que sale de los datos
    python scripts/curva_fundamental.py --backtest 2025   # esconder un año conocido
    python scripts/curva_fundamental.py --curva 2027 2046
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO / "ingesta"))
sys.path.append(str(REPO / "scripts"))

TZ = "Europe/Madrid"
SEMILLA = 42

# Umbral para estimar el RENDIMIENTO sin contaminacion de vertidos. Por debajo de este
# precio la planta no tiene incentivo a producir y el dato deja de medir el recurso.
# 5 EUR/MWh y no 0: cerca de cero ya hay recorte parcial.
PRECIO_LIMPIO = 5.0

# Bins de demanda residual para la curva de oferta. 40 da ~1.400 horas por bin sobre 6
# años, suficiente para una mediana estable sin alisar el codo de la curva.
BINS_RESIDUAL = 40

# Años de historico con los que se ajusta la curva de oferta. NO son todos a proposito:
# ver la nota en `curva_oferta`. Con 3 quedan ~26.000 horas, de sobra para 40 bins.
ANOS_OFERTA = 3

# Horas por bloque al remuestrear el residuo. 720 = 30 dias. Ver la nota de `curva_oferta`
# sobre por que bloques y no ruido blanco: el residuo tiene ACF 0,80 a un retardo, 0,39 a 24
# y 0,12 a 720, asi que un ruido independiente se promedia al agregar y hunde la banda.
BLOQUE_RUIDO = 720

# Tramos de demanda residual para la MARGINAL del ruido, y cuantiles por tramo. Diez tramos
# dejan ~2.000 horas en cada uno con tres años de ventana: suficiente para un percentil 1
# estable. Ver la nota de `curva_oferta` sobre por que la dispersion no puede ser unica.
TRAMOS_RUIDO = 10
NQ_RUIDO = 201

# Calibracion ASIMETRICA de la banda, medida sobre 2025 con el año excluido del ajuste. El
# residuo dentro de muestra subestima el error fuera de muestra -- no incluye el error de
# estimacion de la curva de oferta ni la deriva de regimen -- y lo hace mas por abajo que
# por arriba. Con estos dos factores la cobertura P10-P90 pasa de 70,3 % a 80,5 %.
# Es una calibracion, no un mecanismo: corrige el sintoma, no lo explica.
#
# Y NO TRANSFIERE ENTRE REGIMENES, lo que confirma que es un parche. Con estos factores la
# cobertura sale 80,5 % en 2025 -- el año con el que se midieron -- y 70,6 % en 2024, cuya
# ventana de ajuste (2020-2023) incluye la crisis del gas. Cuanto mas ha derivado el regimen
# entre el ajuste y el año predicho, mas ancha tendria que ser la banda. Lo que si transfiere
# es la FORMA: el sesgo simulado sale 0,29 en los dos años, contra 0,12 y 0,15 reales.
CALIBRA_BAJO = 2.00
CALIBRA_ALTO = 1.15

# Elasticidad del precio al gas: `precio ~ gas^beta`. Con 1.0 el precio es proporcional, que
# es lo que dice la teoria si el ciclo combinado margina siempre. No margina siempre. Se
# estima de los datos con `elasticidad_gas`; este valor es solo el ultimo medido, por si se
# pide sin panel. Ver la nota de esa funcion sobre POR QUE se estima con el historico entero
# y no con la ventana de `k`.
BETA_GAS = 0.713


def _con():
    from curva_precios import _con as c
    return c()


# ─────────────────────────────────────────────────────────────────────────────
# 1 · el panel
# ─────────────────────────────────────────────────────────────────────────────

_PANEL: dict[str, pd.DataFrame] = {}

COLS = ["fecha_objetivo", "hora", "target_price", "ree_demanda_prev", "ree_gwind_prev",
        "ree_gsolar_prev", "gas_mibgas", "co2_eua_dec",
        "ssrd_meteo", "wind100_meteo", "t2m_meteo"]


def meta_matriz(matriz="produccion") -> dict:
    """El `meta.json` que dejo `construir_matriz_produccion` al escribir la matriz.

    Existe porque el hash NO se puede sacar del DataFrame: `.attrs` esta vacio en cuanto se
    lee de disco, y quien lo intentara se llevaria un `None` silencioso. Aqui esta el hash
    de verdad, la fecha de generacion y el corte de train/val.
    """
    import json
    f = REPO / "data" / "gold" / f"matriz_{matriz}.meta.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def panel(matriz="produccion") -> pd.DataFrame:
    """Una fila por hora con precio, demanda, renovable, combustibles, meteo y capacidad.

    Sale de la matriz y no de la base a proposito: la matriz ya tiene todo alineado en hora
    peninsular, depurado y sin nulos, y el canal `*_meteo` ya viene interpolado a horario
    (ERA5 es trihorario en la base).

    AVISO sobre `*_meteo`: es el canal de PREVISION, un 36 % prevision real de ECMWF y el
    resto pseudo-prevision (ERA5 mas el error remuestreado). Para ajustar un rendimiento
    fisico eso atenua un poco la pendiente, pero es autoconsistente: la simulacion sortea
    dias del mismo canal, asi que el sesgo se cancela entre ajuste y uso.
    """
    if matriz in _PANEL:
        return _PANEL[matriz]
    ruta = REPO / "data" / "gold" / f"matriz_{matriz}.parquet"
    try:
        d = pd.read_parquet(ruta, columns=COLS)
    except Exception:
        d = pd.read_csv(ruta.with_suffix(".csv"), usecols=COLS,
                        parse_dates=["fecha_objetivo"])
    d = d.rename(columns={"fecha_objetivo": "dia", "target_price": "precio",
                          "ree_demanda_prev": "demanda"})
    d["dia"] = pd.to_datetime(d.dia)

    from curva_meteo import capacidad_diaria
    cap = capacidad_diaria()
    d = d.merge(cap, left_on="dia", right_index=True, how="left")
    d[["solar_gw", "eolica_gw"]] = d[["solar_gw", "eolica_gw"]].ffill().bfill()
    d["ano"] = d.dia.dt.year
    d = d.sort_values(["dia", "hora"]).reset_index(drop=True)
    _PANEL[matriz] = d
    return d


# ─────────────────────────────────────────────────────────────────────────────
# 2 · rendimiento: de meteorologia a generacion POTENCIAL
# ─────────────────────────────────────────────────────────────────────────────

def rendimientos(p: pd.DataFrame | None = None):
    """Cuanta generacion da cada GW instalado por unidad de recurso.

    Se ajusta SOLO sobre horas con precio > PRECIO_LIMPIO. Ahi la planta produce todo lo
    que puede, asi que la generacion observada mide el recurso y no la decision economica.
    Incluir las horas baratas seria meter el vertido dentro del rendimiento, que es justo
    lo que este modulo existe para evitar.

    Sin ordenada en el origen: cero sol tiene que dar cero solar. Una constante libre daria
    generacion nocturna.
    """
    p = panel() if p is None else p
    q = p[p.precio > PRECIO_LIMPIO]

    xs = (q.ssrd_meteo * q.solar_gw).to_numpy()
    eta_s = float((xs * q.ree_gsolar_prev.to_numpy()).sum() / (xs ** 2).sum())

    # Potencia ~ v^3 por debajo de la nominal. Se recorta al percentil 99 del cubo
    # observado: mas alla la turbina esta en su meseta y el cubo dispara sin sentido.
    v3 = q.wind100_meteo.to_numpy() ** 3
    tope = float(np.percentile(v3, 99))
    xe = np.minimum(v3, tope) * q.eolica_gw.to_numpy()
    eta_e = float((xe * q.ree_gwind_prev.to_numpy()).sum() / (xe ** 2).sum())

    def potencial(ssrd, v100, solar_gw, eolica_gw):
        sol = eta_s * np.asarray(ssrd) * np.asarray(solar_gw)
        eol = eta_e * np.minimum(np.asarray(v100) ** 3, tope) * np.asarray(eolica_gw)
        return np.maximum(sol, 0.0), np.maximum(eol, 0.0)

    # Los tres coeficientes, sueltos y colgados de la funcion: el bucle vectorizado de
    # `simular` opera sobre arrays (escenario, dia, hora) y no puede llamar a la clausura
    # sin reconstruirlos.
    potencial.coef = (eta_s, eta_e, tope)

    def r2(y, yh):
        return 1 - ((y - yh) ** 2).sum() / ((y - y.mean()) ** 2).sum()

    s_aj, e_aj = potencial(q.ssrd_meteo, q.wind100_meteo, q.solar_gw, q.eolica_gw)
    return potencial, {
        "horas_limpias": int(len(q)), "de": int(len(p)),
        "eta_solar": round(eta_s, 4), "eta_eolica": round(eta_e, 6),
        "tope_v3": round(tope, 1),
        "R2_solar": round(r2(q.ree_gsolar_prev.to_numpy(), s_aj), 3),
        "R2_eolica": round(r2(q.ree_gwind_prev.to_numpy(), e_aj), 3)}


def con_residual(p: pd.DataFrame, potencial) -> pd.DataFrame:
    """Añade la generacion potencial y la demanda residual al panel."""
    d = p.copy()
    sol, eol = potencial(d.ssrd_meteo, d.wind100_meteo, d.solar_gw, d.eolica_gw)
    d["solar_pot"], d["eolica_pot"] = sol, eol
    d["residual"] = d.demanda - sol - eol
    return d


# ─────────────────────────────────────────────────────────────────────────────
# 3 · la curva de oferta, en dos partes
# ─────────────────────────────────────────────────────────────────────────────

def elasticidad_gas(d: pd.DataFrame, bins: int = 20, minimo: int = 50) -> float:
    """Pendiente de log(precio) sobre log(gas), dentro de cada bin de demanda residual.

    Se estima con TODO el historico y no con la ventana reciente de `k`, y el motivo es de
    identificacion, no de gusto: en 2023-2025 el gas estuvo en 38,7 / 34,4 / 35,9 EUR/MWh,
    practicamente plano, asi que ahi no hay variacion de la que aprender. En el historico
    completo va de 4,2 a 225,0.

    Controlar por bin de residual es imprescindible: sin eso, la pendiente recogeria que los
    años de gas caro fueron tambien años de menos renovable.
    """
    q = d[(d.precio > 1) & (d.gas_mibgas > 1)].copy()
    q["_b"] = pd.qcut(q.residual, bins, labels=False, duplicates="drop")
    pend, peso = [], []
    for _, g in q.groupby("_b"):
        if g.gas_mibgas.nunique() < minimo:
            continue
        pend.append(np.polyfit(np.log(g.gas_mibgas.to_numpy()),
                               np.log(g.precio.to_numpy()), 1)[0])
        peso.append(len(g))
    if not pend:
        return BETA_GAS
    return float(np.average(pend, weights=peso))


def curva_oferta(d: pd.DataFrame, hasta_ano: int | None = None,
                 desde_ano: int | None = None, anos: int | None = ANOS_OFERTA,
                 beta: float | None = None,
                 calibra_bajo: float = CALIBRA_BAJO,
                 calibra_alto: float = CALIBRA_ALTO):
    """`precio = gas x k(residual)`, con suelo en cero probabilistico.

    DOS PARTES, y la primera es la que arregla el defecto de la version anterior:

      p0(residual)  probabilidad de que la hora se case a <= 0. Se estima por bins y sale
                    monotona decreciente sola -- no hay que imponerla.
      k(residual)   mediana de `precio / gas` en las horas positivas. Es el heat rate
                    implicito de la planta marginal: cerca de 0 cuando marginan renovables
                    o nuclear, cerca de 2 cuando margina un ciclo combinado.

    Dividir por el gas ANTES de ajustar es lo que hace que la curva extrapole. Sin eso, el
    ajuste memoriza el nivel de precios de los años de entrenamiento -- medido: un LightGBM
    sobre `f(residual, gas, CO2, hora)` da R2 0,965 dentro de muestra y 0,570 fuera, y
    entrenado solo con 2025 sube a 0,705. Menos datos y mejor transferencia significa que
    la relacion se mueve, y lo que se mueve es el nivel, no la forma.

    Fuera del rango observado k se mantiene plana en su extremo. Extrapolar una pendiente
    veinte años es inventar; saturar al menos es una hipotesis declarada.
    """
    if hasta_ano is not None:
        d = d[d.ano <= hasta_ano]
    # El beta se estima ANTES de recortar la ventana: necesita la variacion de gas del
    # historico entero, que es justo lo que la ventana reciente no tiene.
    if beta is None:
        beta = elasticidad_gas(d)
    if desde_ano is None and anos:
        # Ventana movil, no todo el historico. Medido en el empalme con 2026: ajustando
        # con 2020-2026 el modelo devuelve 82,3 EUR/MWh y ajustando desde 2025, 74,5,
        # contra 65,6 reales. La diferencia son 2021-2022 -- gas a 99 EUR/MWh y otro
        # parque -- dejando `k` alta para un regimen que ya no existe. Mas datos empeoran
        # la transferencia, que es la firma de una relacion que se mueve.
        desde_ano = int(d.ano.max()) - anos + 1
    if desde_ano is not None:
        d = d[d.ano >= desde_ano]
    d = d[d.gas_mibgas > 1].copy()

    borde = np.quantile(d.residual, np.linspace(0, 1, BINS_RESIDUAL + 1))
    borde = np.unique(borde)
    d["bin"] = np.clip(np.searchsorted(borde, d.residual) - 1, 0, len(borde) - 2)
    centro = (borde[:-1] + borde[1:]) / 2

    g = d.groupby("bin")
    p0 = g.apply(lambda x: (x.precio <= 0).mean(), include_groups=False)
    pos = d[d.precio > 0]
    k = pos.groupby("bin").apply(
        lambda x: (x.precio / x.gas_mibgas ** beta).median(), include_groups=False)
    idx = pd.RangeIndex(len(centro))
    p0 = p0.reindex(idx).interpolate(limit_direction="both").to_numpy()
    k = k.reindex(idx).interpolate(limit_direction="both").to_numpy()
    # k monotona creciente: mas demanda residual nunca puede abaratar la hora. Se impone
    # con un maximo acumulado, que es lo minimo que respeta el merit order.
    k = np.maximum.accumulate(k)

    # ── lo que la curva NO explica ───────────────────────────────────────────
    # Se guarda la serie entera EN ORDEN, no solo su desviacion tipica. El residuo tiene
    # ACF 0,804 a un retardo, 0,390 a 24 y 0,118 a 720: sortear valores sueltos destruye
    # esa estructura y la banda se hunde al agregar (sd mensual 0,043 simulada contra
    # 0,599 real). Remuestrear bloques contiguos la conserva sin modelar nada.
    kk = np.interp(pos.residual, centro, k)
    ratio = (pos.precio / (pos.gas_mibgas ** beta * np.maximum(kk, 1e-6))).clip(0.1, 5)
    sigma = float(np.std(np.log(ratio)))
    orden = pos.sort_values(["dia", "hora"])
    kk_o = np.interp(orden.residual, centro, k)
    resid = np.log((orden.precio / (orden.gas_mibgas ** beta
                                    * np.maximum(kk_o, 1e-6))).clip(0.1, 5)).to_numpy()
    resid = resid - np.median(resid)      # `k` es una mediana: el residuo se centra en ella

    # ── la dispersion NO es unica: depende del tramo del merit order ─────────
    # Medido por decil de residual, sd(e) va de 1,392 abajo a 0,174 arriba -- ocho veces --
    # y el sesgo de -0,19 a -2,63. Tiene sentido: con el ciclo combinado marginando, el
    # precio esta clavado al combustible; con la renovable marginando, puede pasar de todo.
    # Un sigma unico inflaba la hora de pico hasta 653 EUR/MWh (el maximo real de 2025 fue
    # 240) y dejaba el valle sin cola negativa.
    #
    # Se guarda la marginal EMPIRICA de cada tramo, y el residuo se convierte a su
    # percentil DENTRO de su tramo. Al simular, ese percentil se mapea contra la marginal
    # del tramo que le toque a la hora simulada: dependencia por bloques, marginal por
    # tramo. Es una copula empirica, y no postula ninguna forma de distribucion.
    bord_t = np.unique(np.quantile(orden.residual, np.linspace(0, 1, TRAMOS_RUIDO + 1)))
    tramo_o = np.clip(np.searchsorted(bord_t, orden.residual) - 1, 0, len(bord_t) - 2)
    probs = np.linspace(0, 1, NQ_RUIDO)
    QTAB = np.stack([np.quantile(resid[tramo_o == t], probs)
                     if (tramo_o == t).sum() > NQ_RUIDO else np.quantile(resid, probs)
                     for t in range(len(bord_t) - 1)])
    # percentil de cada residuo dentro de SU tramo, en orden cronologico
    u_orden = np.empty(len(resid))
    for t in range(len(bord_t) - 1):
        m_t = tramo_o == t
        if m_t.any():
            u_orden[m_t] = (np.argsort(np.argsort(resid[m_t])) + .5) / m_t.sum()

    # reparto de los precios <= 0: cuantos son cero exacto y como es la cola negativa
    neg = d[d.precio <= 0].precio.to_numpy()
    frac_cero = float((neg == 0).mean()) if len(neg) else 1.0

    def _ruido(residual, rng, blanco=False, homo=False):
        """Perturbacion multiplicativa: bloques para la dependencia, marginal por tramo.

        `blanco=True`  ruido independiente y homocedastico -- la primera version.
        `homo=True`    bloques pero con marginal unica -- la segunda.
        Los dos se conservan para poder enseñar la mejora medida, no porque sirvan.
        """
        forma = residual.shape
        if blanco:
            return np.exp(rng.normal(0, sigma, forma) - sigma ** 2 / 2)
        n = int(np.prod(forma[1:])) if len(forma) > 1 else int(forma[0])
        filas = int(forma[0]) if len(forma) > 1 else 1
        b = min(BLOQUE_RUIDO, len(resid) // 2)
        cuantos = int(np.ceil(n / b))
        fuente = resid if homo else u_orden
        out = np.empty((filas, cuantos * b))
        ini = rng.integers(0, len(fuente) - b, size=(filas, cuantos))
        for i in range(filas):
            out[i] = np.concatenate([fuente[j:j + b] for j in ini[i]])
        out = out[:, :n].reshape(forma)
        if homo:
            return np.exp(out)
        # `out` son percentiles: se mapean contra la marginal del tramo de CADA hora
        t = np.clip(np.searchsorted(bord_t, residual) - 1, 0, QTAB.shape[0] - 1)
        iq = np.clip((out * (NQ_RUIDO - 1)).astype(np.int32), 0, NQ_RUIDO - 1)
        e = QTAB[t, iq]
        # y se ensancha, mas abajo que arriba. Ver la nota de las constantes.
        return np.exp(np.where(e < 0, calibra_bajo * e, calibra_alto * e))

    def precio(residual, gas, u=None, rng=None, blanco=False, homo=False):
        """Precio horario. `u` en [0,1) decide si la hora cae al suelo."""
        rng = np.random.default_rng(SEMILLA) if rng is None else rng
        residual = np.asarray(residual, dtype=float)
        gas = np.asarray(gas, dtype=float)
        u = rng.random(residual.shape) if u is None else u
        cae = u < np.interp(residual, centro, p0)
        y = (gas ** beta * np.interp(residual, centro, k)
             * _ruido(residual, rng, blanco, homo))
        if len(neg):
            suelo = np.where(rng.random(residual.shape) < frac_cero, 0.0,
                             rng.choice(neg[neg < 0], size=residual.shape)
                             if (neg < 0).any() else 0.0)
        else:
            suelo = np.zeros(residual.shape)
        return np.where(cae, suelo, y)

    return precio, {"bins": len(centro), "horas": int(len(d)),
                    "ajustada_desde": int(d.ano.min()), "hasta": int(d.ano.max()),
                    "beta_gas": round(float(beta), 3),
                    "residual_p5": round(float(centro[0]), 0),
                    "residual_p95": round(float(centro[-1]), 0),
                    "k_min": round(float(k[0]), 3), "k_max": round(float(k[-1]), 3),
                    "p0_max": round(float(p0[0]), 3), "p0_min": round(float(p0[-1]), 3),
                    "sigma_log": round(sigma, 3),
                    "acf1_residuo": round(float(np.corrcoef(resid[:-1],
                                                           resid[1:])[0, 1]), 3),
                    "acf24_residuo": round(float(np.corrcoef(resid[:-24],
                                                            resid[24:])[0, 1]), 3),
                    "resid": resid,
                    "sd_por_tramo": [round(float(np.std(resid[tramo_o == t])), 3)
                                     for t in range(len(bord_t) - 1)],
                    "tramos_ruido": int(len(bord_t) - 1),
                    "calibra": (calibra_bajo, calibra_alto),
                    "frac_cero_exacto": round(frac_cero, 3),
                    "centro": centro, "k": k, "p0": p0}


# ─────────────────────────────────────────────────────────────────────────────
# 4 · simular el futuro
# ─────────────────────────────────────────────────────────────────────────────

def perfil_demanda(d: pd.DataFrame, anos: int = 3) -> pd.Series:
    """Demanda de cada (mes, tipo de dia, hora) dividida por la media del periodo.

    Se normaliza para poder escalarla con un escenario de demanda anual sin arrastrar el
    nivel del pasado.
    """
    q = d[d.ano > d.ano.max() - anos].copy()
    q["tipo"] = np.where(q.dia.dt.dayofweek >= 5, "finde", "laborable")
    q["mes"] = q.dia.dt.month
    return q.groupby(["mes", "tipo", "hora"]).demanda.mean() / q.demanda.mean()


def _meteo_molde(d: pd.DataFrame, anos: int = 6) -> pd.DataFrame:
    """Dias historicos de los que sortear meteorologia, indexados por (mes, dia del mes).

    Se sortean DIAS ENTEROS, no horas: un mediodia soleado va con su tarde soleada. Romper
    eso destruiria la correlacion intradiaria y la banda saldria absurdamente estrecha.
    """
    q = d[d.ano > d.ano.max() - anos][["dia", "hora", "ssrd_meteo", "wind100_meteo"]].copy()
    q["mes"], q["dm"] = q.dia.dt.month, q.dia.dt.day
    return q


def simular(desde, hasta, gas: dict, demanda: dict, solar_gw: dict, eolica_gw: dict,
            potencial=None, precio=None, molde=None, perfil=None,
            n: int = 200, percentiles=(10, 50, 90), verbose=True, crudo=False):
    """Precio horario simulado, con banda de percentiles.

    Los cuatro escenarios -- gas, demanda, solar y eolica -- son ANUALES y se aportan como
    diccionarios año -> valor (usa `curva_precios.por_anclas`). Ninguno es una prediccion
    de precio: son cantidades fisicas con objetivos publicados.

    Cada escenario `n` sortea, para cada dia del futuro, un dia historico del mismo mes y
    toma su radiacion y su viento. Con capacidad futura eso da otra generacion potencial,
    otra residual y otro precio. La variabilidad de la banda es METEOROLOGICA, que es la
    unica que este modulo puede defender con datos.
    """
    p = panel()
    if potencial is None:
        potencial, _ = rendimientos(p)
    d = con_residual(p, potencial)
    if precio is None:
        precio, _ = curva_oferta(d)
    _ETA = potencial.coef
    if molde is None:
        molde = _meteo_molde(d)
    if perfil is None:
        perfil = perfil_demanda(d)

    fechas = pd.date_range(desde, hasta, freq="D")
    fut = pd.DataFrame({"dia": np.repeat(fechas, 24),
                        "hora": np.tile(np.arange(24), len(fechas))})
    fut["ano"] = fut.dia.dt.year
    fut["mes"], fut["dm"] = fut.dia.dt.month, fut.dia.dt.day
    fut["tipo"] = np.where(fut.dia.dt.dayofweek >= 5, "finde", "laborable")

    falta = sorted({a for a in fut.ano.unique()
                    for esc in (gas, demanda, solar_gw, eolica_gw) if a not in esc})
    if falta:
        raise KeyError(f"faltan años en algun escenario: {falta}")

    for nom, esc in [("gas", gas), ("dem_anual", demanda),
                     ("solar_gw", solar_gw), ("eolica_gw", eolica_gw)]:
        fut[nom] = fut.ano.map(esc).astype(float)
    fut["demanda"] = fut.dem_anual * pd.MultiIndex.from_arrays(
        [fut.mes, fut.tipo, fut.hora]).map(perfil).to_numpy()
    if fut.demanda.isna().any():
        fut["demanda"] = fut.demanda.fillna(fut.dem_anual)

    # ── el molde, como matriz (dia, hora) ─────────────────────────────────────
    # Una sola vez. El bucle anterior hacia un `.loc` por (escenario, dia futuro): 1,46
    # millones de accesos para veinte años, o sea horas. Aqui se sortean indices y numpy
    # hace un `gather`.
    M = (molde.pivot_table(index="dia", columns="hora",
                           values=["ssrd_meteo", "wind100_meteo"], aggfunc="mean")
         .interpolate(axis=1, limit_direction="both"))
    dias_m = M.index.to_numpy()
    SS = M["ssrd_meteo"].reindex(columns=range(24)).to_numpy("float32")
    VV = M["wind100_meteo"].reindex(columns=range(24)).to_numpy("float32")
    SS = np.nan_to_num(SS, nan=float(np.nanmean(SS)))
    VV = np.nan_to_num(VV, nan=float(np.nanmean(VV)))

    # candidatos por dia de calendario, en una tabla rectangular con relleno para poder
    # sortear de golpe. `largo` dice cuantos son de verdad en cada fila.
    cal = pd.DataFrame({"dia": dias_m, "mes": pd.DatetimeIndex(dias_m).month,
                        "dm": pd.DatetimeIndex(dias_m).day, "i": np.arange(len(dias_m))})
    por_dm = {k: v.i.to_numpy() for k, v in cal.groupby(["mes", "dm"])}
    por_mes = {k: v.i.to_numpy() for k, v in cal.groupby("mes")}

    dfut = fut.drop_duplicates("dia")[["dia", "mes", "dm"]].reset_index(drop=True)
    listas = [por_dm.get((m, dm)) if len(por_dm.get((m, dm), [])) else por_mes[m]
              for m, dm in zip(dfut.mes, dfut.dm)]
    largo = np.array([len(x) for x in listas])
    ancho = int(largo.max())
    CAND = np.zeros((len(listas), ancho), dtype="int32")
    for j, x in enumerate(listas):
        CAND[j, :len(x)] = x

    J = len(dfut)
    sol_gw = fut.solar_gw.to_numpy().reshape(J, 24)
    eol_gw = fut.eolica_gw.to_numpy().reshape(J, 24)
    dem = fut.demanda.to_numpy().reshape(J, 24)
    gas_v = fut.gas.to_numpy().reshape(J, 24)

    rng = np.random.default_rng(SEMILLA)
    sims = np.empty((n, len(fut)), dtype="float32")
    LOTE = max(1, min(25, n))                     # acota la memoria: (LOTE, J, 24)
    hecho = 0
    while hecho < n:
        b = min(LOTE, n - hecho)
        # un dia historico por (escenario, dia futuro), del mismo dia de calendario
        pick = CAND[np.arange(J), (rng.random((b, J)) * largo).astype("int32")]
        ss, vv = SS[pick], VV[pick]               # (b, J, 24)
        sol = np.maximum(_ETA[0] * ss * sol_gw, 0.0)
        eol = np.maximum(_ETA[1] * np.minimum(vv ** 3, _ETA[2]) * eol_gw, 0.0)
        res = (dem - sol - eol).reshape(b, -1)
        sims[hecho:hecho + b] = precio(res, np.broadcast_to(gas_v.ravel(), res.shape),
                                       rng=rng)
        hecho += b
        if verbose:
            print(f"    escenario {hecho}/{n}")

    out = fut[["dia", "hora"]].copy()
    for q in percentiles:
        out[f"p{q}"] = np.percentile(sims, q, axis=0)
    out["origen"] = "fundamental"
    # `crudo` devuelve ademas los n escenarios sin resumir. Hace falta para cualquier
    # afirmacion sobre la DISTRIBUCION -- cuantas horas a cero, que spread -- porque el
    # P50 es una mediana entre escenarios y borra justo lo que se quiere medir: una hora
    # solo sale cero en el P50 si mas de la mitad de los escenarios coinciden en cero.
    # Mismo motivo por el que la bateria hay que operarla escenario a escenario.
    return (out, sims) if crudo else out


# ─────────────────────────────────────────────────────────────────────────────
# 5 · validacion
# ─────────────────────────────────────────────────────────────────────────────

def backtest(ano: int = 2025, n: int = 200, verbose=True) -> dict:
    """Esconder un año conocido: ajustar con lo anterior y simularlo a ciegas.

    Es lo unico que convierte esto en un resultado y no en una grafica. Los escenarios que
    se le dan son los valores REALES de ese año -- gas, demanda y capacidad -- porque lo
    que se valida es el modelo de precio, no el acierto del escenario.
    """
    p = panel()
    prev = p[p.ano < ano]
    if not len(prev):
        raise ValueError(f"no hay historico anterior a {ano}")
    potencial, info_r = rendimientos(prev)
    d_prev = con_residual(prev, potencial)
    precio, info_c = curva_oferta(d_prev)

    real = p[p.ano == ano]
    esc = {ano: float(real.gas_mibgas.mean())}
    dem = {ano: float(real.demanda.mean())}
    sol = {ano: float(real.solar_gw.mean())}
    eol = {ano: float(real.eolica_gw.mean())}

    sim, sims = simular(f"{ano}-01-01", f"{ano}-12-31", esc, dem, sol, eol,
                        potencial=potencial, precio=precio,
                        molde=_meteo_molde(d_prev), perfil=perfil_demanda(d_prev),
                        n=n, percentiles=(1, 10, 50, 90, 99), verbose=verbose,
                        crudo=True)
    j = sim.merge(real[["dia", "hora", "precio"]], on=["dia", "hora"])
    # el spread se calcula EN CADA ESCENARIO y luego se promedia. Calcularlo sobre el
    # P50 da un numero mas bajo que no le ocurre a nadie.
    esc_sp = np.mean([_spread(sim.assign(x=sims[k]), "x") for k in range(len(sims))])
    return {"j": j, "sims": sims, "rend": info_r, "curva": info_c,
            "media_real": float(j.precio.mean()),
            "media_sim": float(sims.mean()), "media_p50": float(j.p50.mean()),
            "neg_real": float((j.precio <= 0).mean() * 100),
            "neg_sim": float((sims <= 0).mean() * 100),
            "neg_p50": float((j.p50 <= 0).mean() * 100),
            "cero_real": float((j.precio == 0).mean() * 100),
            "cero_sim": float((sims == 0).mean() * 100),
            "cob_80": float(((j.precio >= j.p10) & (j.precio <= j.p90)).mean() * 100),
            "cob_98": float(((j.precio >= j.p1) & (j.precio <= j.p99)).mean() * 100),
            "spread_real": float(_spread(j, "precio")),
            "spread_sim": float(esc_sp), "spread_p50": float(_spread(j, "p50"))}


def contraste(ano: int, n: int = 200, d: pd.DataFrame | None = None,
              precio=None, verbose=False, beta: float | None = None) -> dict:
    """Aplicar la curva de precio sobre la residual REALMENTE observada de un año.

    Es la mitad del backtest, y la mitad que importa para validar el precio: no sortea
    meteorologia, asi que no mezcla el error del modelo con el de haber sorteado un año
    tipico cuando el real fue atipico. Enero-agosto de 2026 tuvo +1.400 MW de renovable
    potencial sobre la media de los seis años disponibles, casi todo eolica; contra eso, un
    simulador que produce el año tipico tiene que salir caro, y no es culpa suya.

    La curva se ajusta EXCLUYENDO el año contrastado.
    """
    p = panel() if d is None else d
    if "residual" not in p.columns:
        pot, _ = rendimientos(p[p.ano != ano])
        p = con_residual(p, pot)
    if precio is None:
        precio, _ = curva_oferta(p[p.ano != ano], beta=beta)
    real = p[p.ano == ano]
    rng = np.random.default_rng(SEMILLA)
    res = np.tile(real.residual.to_numpy(), (n, 1))
    gas = np.tile(real.gas_mibgas.to_numpy(), (n, 1))
    sims = precio(res, gas, rng=rng)
    if verbose:
        print(f"  contraste {ano}: {len(real):,} horas x {n} sorteos de precio")
    return {"real": real, "sims": sims,
            "media_real": float(real.precio.mean()), "media_sim": float(sims.mean()),
            "neg_real": float((real.precio <= 0).mean() * 100),
            "neg_sim": float((sims <= 0).mean() * 100),
            "cero_real": float((real.precio == 0).mean() * 100),
            "cero_sim": float((sims == 0).mean() * 100)}


def _spread(j: pd.DataFrame, col: str) -> float:
    """Pico menos valle sobre la media del dia, promediado. Es lo que cobra la bateria."""
    r = j[col] - j.groupby("dia")[col].transform("mean")
    return r[j.hora.between(19, 21)].mean() - r[j.hora.between(12, 15)].mean()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ajuste", action="store_true", help="que sale de los datos")
    ap.add_argument("--backtest", type=int, metavar="ANO")
    ap.add_argument("--curva", nargs=2, type=int, metavar=("INI", "FIN"))
    ap.add_argument("--n", type=int, default=200)
    a = ap.parse_args()

    if a.ajuste or not (a.backtest or a.curva):
        p = panel()
        potencial, ir = rendimientos(p)
        d = con_residual(p, potencial)
        _, ic = curva_oferta(d)
        print(f"\n  PANEL  {len(p):,} horas · {p.ano.min()}-{p.ano.max()}")
        print(f"\n  RENDIMIENTO (ajustado con {ir['horas_limpias']:,} horas de "
              f"{ir['de']:,}, las de precio > {PRECIO_LIMPIO:g})")
        for k_ in ("eta_solar", "R2_solar", "eta_eolica", "R2_eolica"):
            print(f"    {k_:14s} {ir[k_]}")
        print(f"\n  CURVA DE OFERTA  ({ic['bins']} bins, {ic['horas']:,} horas, "
              f"ajustada {ic['ajustada_desde']}-{ic['hasta']})")
        print(f"    residual de {ic['residual_p5']:,.0f} a {ic['residual_p95']:,.0f} MW")
        print(f"    k (precio/gas)  de {ic['k_min']} a {ic['k_max']}")
        print(f"    P(precio<=0)    de {ic['p0_max']} a {ic['p0_min']}")
        print(f"    beta gas        {ic['beta_gas']}  (precio ~ gas^beta; estimado con "
              f"el historico entero, donde el gas si varia)")
        print(f"    sigma log       {ic['sigma_log']}   ·  ceros exactos "
              f"{ic['frac_cero_exacto']}")
        print(f"    ACF del residuo  retardo 1 = {ic['acf1_residuo']} · retardo 24 = "
              f"{ic['acf24_residuo']}  -> NO es ruido blanco, se remuestrea por bloques "
              f"de {BLOQUE_RUIDO} h")
        print(f"    sd del residuo por tramo (de residual baja a alta):")
        print(f"      {ic['sd_por_tramo']}")
        print(f"      no es constante -> la marginal se toma del tramo de cada hora")
        print(f"    calibracion de la banda  x{ic['calibra'][0]} por abajo · "
              f"x{ic['calibra'][1]} por arriba  (medida sobre un año excluido)")
        print(f"\n  {'residual MW':>12s} {'k':>7s} {'P(<=0)':>8s}")
        for i in range(0, len(ic["centro"]), max(1, len(ic["centro"]) // 12)):
            print(f"  {ic['centro'][i]:12,.0f} {ic['k'][i]:7.3f} {ic['p0'][i]:8.3f}")

    if a.backtest:
        r = backtest(a.backtest, n=a.n)
        print(f"\n  BACKTEST {a.backtest} · ajustado solo con años anteriores\n")
        print(f"  {'':24s} {'real':>9s} {'simulado':>9s}")
        print("  " + "-" * 46)
        print(f"  {'':24s} {'real':>9s} {'escenarios':>11s} {'P50':>9s}")
        print("  " + "-" * 56)
        for et, kr, ks, kp in [("media EUR/MWh", "media_real", "media_sim", "media_p50"),
                               ("horas <= 0  %", "neg_real", "neg_sim", "neg_p50"),
                               ("spread pico-valle", "spread_real", "spread_sim",
                                "spread_p50")]:
            print(f"  {et:24s} {r[kr]:9.2f} {r[ks]:11.2f} {r[kp]:9.2f}")
        print(f"  {'horas = 0 exacto %':24s} {r['cero_real']:9.2f} "
              f"{r['cero_sim']:11.2f}")
        print("  " + "-" * 56)
        print(f"  cobertura P10-P90 {r['cob_80']:.1f}%  (ideal 80)")
        print(f"  cobertura P1-P99  {r['cob_98']:.1f}%  (ideal 98)")

    if a.curva:
        from curva_precios import por_anclas
        i, f = a.curva
        esc = dict(gas=por_anclas({2027: 30, 2035: 28, 2046: 26}, i, f),
                   demanda=por_anclas({2027: 28500, 2035: 32000, 2046: 36000}, i, f),
                   solar_gw=por_anclas({2027: 67, 2030: 76, 2040: 110, 2046: 125}, i, f),
                   eolica_gw=por_anclas({2027: 34, 2030: 43, 2040: 55, 2046: 62}, i, f))
        c, sims = simular(f"{i}-01-01", f"{f}-12-31", **esc, n=a.n, crudo=True)
        an = c.assign(y=c.dia.dt.year).groupby("y")[["p10", "p50", "p90"]].mean()
        # media y horas a cero sobre los ESCENARIOS, no sobre el P50: la mediana entre
        # escenarios no puede valer cero salvo que la mitad coincidan, y eso borra
        # justamente la masa en el suelo que este modelo existe para reproducir.
        y = c.dia.dt.year.to_numpy()
        an["media"] = [sims[:, y == k].mean() for k in an.index]
        an["h_cero_%"] = [(sims[:, y == k] <= 0).mean() * 100 for k in an.index]
        an = an[["p10", "p50", "media", "p90", "h_cero_%"]]
        print(f"\n  {len(c):,} horas · {c.dia.nunique():,} dias\n")
        print(an.round(2).to_string())


if __name__ == "__main__":
    main()
