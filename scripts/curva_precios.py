"""Curva de precio horario para cualquier rango: pasado, mañana o dentro de veinte años.

Una sola entrada, `curva(desde, hasta)`, que decide sola de dónde sale cada día:

    historico    el PMD publicado en `spot_price`
    modelo       lo que predijeron los modelos, de la tabla `predictions`
    simulado     generado, con banda de percentiles

POR QUE NO SE EXTRAPOLAN LOS MODELOS DE D+1
Los modelos entrenados reciben los 7 días previos observados y las previsiones de D+1. Para
llegar a 2046 habría que realimentar sus propias predicciones 7.300 veces: el error se
compone y en pocos días la serie se aplana a la media. Y los exógenos de D+1 -- demanda
prevista, eólica, gas, CO2 -- no existen para dentro de veinte años. Un modelo de D+1 explota
la persistencia; una curva a largo plazo tiene que ignorarla.

COMO SE GENERA EL TRAMO SIMULADO
La descomposición estándar del sector, en tres piezas separadas a propósito:

    precio(d,h)  =  nivel(año)  x  factor_mes(m)  +  forma(m, tipo_dia, h)  +  residuo

    nivel        NO SE PREDICE, SE APORTA. Sale de los futuros MIBEL (cotizan a 3-4 años) o
                 de un escenario fundamental. Si no se pasa, se usa la media de los ultimos
                 12 meses mantenida plana, que es un MARCADOR DE POSICION, no una prevision.
    factor_mes   estacionalidad, del historico
    forma        el perfil intradiario, y es la parte interesante: se esta deformando. El
                 valle de mediodia paso de -0,29 EUR/MWh en 2020 a -49,08 en 2026 por la
                 canibalizacion solar. Por eso la ventana de referencia son los ULTIMOS años,
                 no todo el historico: promediar 2020 con 2026 da un perfil que no existe.
    residuo      remuestreo por bloques de 24 h, que preserva la autocorrelacion dentro del
                 dia. De ahi salen los percentiles.

    python scripts/curva_precios.py --desde 2027-01-01 --hasta 2027-12-31
    python scripts/curva_precios.py --desde 2030-01-01 --hasta 2030-01-31 --nivel 2030=72
    python scripts/curva_precios.py --deformacion
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO / "ingesta"))

TZ = "Europe/Madrid"
REFERENCIA_ANOS = 2          # años de historico para estimar la forma
SEMILLA = 42
# cuanto del ensanchamiento del spread se lleva el valle. 0 = el valle no baja mas y
# todo el crecimiento va al pico; 1 = simetrico, que es lo que daba valles imposibles.
SATURA_VALLE = 0.30
# cuanto de la variabilidad diaria historica se traslada al futuro. 1 = toda.
RUIDO_DIARIO = 1.0


def _con():
    from config import load_config
    import psycopg2
    _, db = load_config()
    return psycopg2.connect(**db)


def historico() -> pd.DataFrame:
    """El PMD publicado, en hora peninsular. Columnas: dia, hora, precio."""
    with _con() as con:
        d = pd.read_sql("SELECT datetime, es_esios FROM spot_price "
                        "WHERE es_esios IS NOT NULL ORDER BY datetime", con)
    t = pd.to_datetime(d.datetime, utc=True).dt.tz_convert(TZ)
    return pd.DataFrame({"dia": t.dt.normalize().dt.tz_localize(None),
                         "hora": t.dt.hour,
                         "precio": d.es_esios.astype(float).to_numpy()})


def deformacion(h: pd.DataFrame | None = None) -> pd.DataFrame:
    """Cuanto se ha hundido el valle de mediodia, año a año.

    Es el hallazgo que justifica no promediar todo el historico para la forma, y de paso
    el argumento del capitulo de baterias: lo que las hace rentables no es que el precio
    suba, es que el SPREAD se abra.
    """
    h = historico() if h is None else h
    h = h.assign(ano=h.dia.dt.year)
    h["rel"] = h.precio - h.groupby("dia").precio.transform("mean")
    v = h[h.hora.between(12, 15)].groupby("ano").rel.mean()
    p = h[h.hora.between(19, 21)].groupby("ano").rel.mean()
    n = h.groupby("ano").precio.mean()
    return pd.DataFrame({"nivel_medio": n.round(1), "valle_12_15h": v.round(2),
                         "pico_19_21h": p.round(2), "spread": (p - v).round(2)})


def drivers() -> pd.DataFrame:
    """Capacidad instalada mensual junto a la forma del precio de ese mes.

    Es la matriz que hace falta para PROYECTAR la forma en vez de congelarla: una fila por
    mes, la capacidad solar y eolica como variables, y el spread intradiario como objetivo.
    Ochenta filas y tres columnas -- nada que ver con la matriz horaria de 133 columnas que
    usan los modelos de D+1, porque el problema es otro.
    """
    with _con() as con:
        cap = pd.read_sql("""SELECT date, solar_pv_mw, COALESCE(autoconsume_solar_pv_mw,0) auto,
                                    wind_mw FROM esios_capacity_installed ORDER BY date""", con)
    cap["mes"] = pd.to_datetime(cap.date).dt.to_period("M")
    cap = cap.groupby("mes")[["solar_pv_mw", "auto", "wind_mw"]].mean()
    cap["solar_gw"] = (cap.solar_pv_mw + cap.auto) / 1000
    cap["eolica_gw"] = cap.wind_mw / 1000

    h = historico()
    h["mes"] = h.dia.dt.to_period("M")
    h["rel"] = h.precio - h.groupby("dia").precio.transform("mean")
    v = h[h.hora.between(12, 15)].groupby("mes").rel.mean()
    p = h[h.hora.between(19, 21)].groupby("mes").rel.mean()
    return (pd.DataFrame({"valle": v, "pico": p, "spread": p - v})
            .join(cap[["solar_gw", "eolica_gw"]], how="inner").dropna())


def modelo_spread(d: pd.DataFrame | None = None):
    """spread ~ solar instalada. Devuelve (predice, info).

    OJO AL LEERLO: la eolica correlaciona 0,77 con el spread y la solar 0,73, y las dos
    crecen con el tiempo. El tiempo confunde a las dos, asi que esto NO demuestra que sea la
    solar la que abre el spread -- solo que el spread crece con el parque renovable. Como
    modelo de primer orden para proyectar sirve; como afirmacion causal, no.
    """
    d = drivers() if d is None else d
    c = np.polyfit(d.solar_gw, d.spread, 1)
    pred = np.polyval(c, d.solar_gw)
    r2 = 1 - ((d.spread - pred) ** 2).sum() / ((d.spread - d.spread.mean()) ** 2).sum()
    return (lambda gw: float(np.polyval(c, gw))), {
        "pendiente_EUR_por_GW": round(float(c[0]), 3), "intercepto": round(float(c[1]), 2),
        "R2": round(float(r2), 3), "meses": len(d),
        "corr_solar": round(float(d.spread.corr(d.solar_gw)), 3),
        "corr_eolica": round(float(d.spread.corr(d.eolica_gw)), 3),
        "solar_gw_hoy": round(float(d.solar_gw.iloc[-1]), 1),
        # El spread de referencia es el que el MODELO predice para la capacidad de hoy, no
        # el observado del ultimo mes: un mes suelto puede ser extremo y entonces todos los
        # factores de amplitud salen sesgados. Asi la referencia es coherente con la
        # proyeccion, que es lo unico que importa para una razon entre las dos.
        "spread_ref": round(float(np.polyval(c, d.solar_gw.iloc[-1])), 1),
        "spread_observado_ultimo_mes": round(float(d.spread.iloc[-1]), 1),
        "spread_observado_12m": round(float(d.spread.tail(12).mean()), 1)}


def por_anclas(anclas: dict, ano_ini: int, ano_fin: int) -> dict:
    """Rellena un escenario año a año interpolando entre puntos de anclaje.

    Pedir veinte años no puede obligar a escribir veinte numeros. Se dan los que se saben
    -- los futuros MIBEL para los primeros, el PNIEC para 2030, una hipotesis para el final
    -- y el resto se interpola linealmente. Fuera del rango de anclas se mantiene el valor
    del extremo, sin extrapolar: extrapolar una recta veinte años es como no decir nada.

        por_anclas({2027: 66, 2030: 60, 2046: 52}, 2027, 2046)
    """
    a = sorted(anclas)
    ys = np.arange(ano_ini, ano_fin + 1)
    return {int(y): float(v) for y, v in
            zip(ys, np.interp(ys, a, [anclas[k] for k in a]))}


def _tipo_dia(d: pd.Series) -> pd.Series:
    return np.where(d.dt.dayofweek >= 5, "finde", "laborable")


def perfil(h: pd.DataFrame, anos: int = REFERENCIA_ANOS):
    """Forma intradiaria y estacionalidad, de los ultimos `anos`.

    Devuelve (forma, factor_mes, residuos):
      forma       EUR/MWh que cada hora se desvia de la media de su dia, por mes y tipo
      factor_mes  multiplicador del nivel anual en cada mes
      residuos    lo que queda sin explicar, en bloques de 24 h, para el Monte Carlo
    """
    corte = h.dia.max() - pd.DateOffset(years=anos)
    r = h[h.dia > corte].copy()
    r["mes"], r["tipo"] = r.dia.dt.month, _tipo_dia(r.dia)
    r["media_dia"] = r.groupby("dia").precio.transform("mean")
    r["rel"] = r.precio - r.media_dia

    forma = r.groupby(["mes", "tipo", "hora"]).rel.mean()
    nivel = r.groupby("dia").precio.mean()
    # El factor estacional sale de MUY POCAS observaciones: con 2 años de referencia son
    # dos eneros, dos abriles... Un abril anómalo mueve el factor de todos los abriles
    # futuros. Se deja asi a proposito -- ampliar la ventana metería la crisis del gas de
    # 2021-2022, que distorsiona mas -- pero hay que saberlo al leer la curva.
    fac_mes = (nivel.groupby(nivel.index.month).mean() / nivel.mean())
    fac_mes.index.name = "mes"
    fac_mes.name = "factor"
    fac_mes.attrs["anos_de_muestra"] = round(len(nivel) / 365.25, 1)

    esperado = r.set_index(["mes", "tipo", "hora"]).index.map(forma)
    r["residuo"] = r.rel - esperado.to_numpy()
    return forma, fac_mes, r[["dia", "hora", "residuo"]]


def _bloques(res: pd.DataFrame) -> np.ndarray:
    """Los residuos agrupados en dias completos: (n_dias, 24).

    Se remuestrea el DIA ENTERO, no hora suelta. Un dia de precios altos lo es a todas
    horas: barajar horas por separado destruiria esa correlacion y las bandas saldrian
    demasiado estrechas.
    """
    p = res.pivot_table(index="dia", columns="hora", values="residuo")
    return p.reindex(columns=range(24)).interpolate(axis=1, limit_direction="both").dropna().to_numpy()


def dias_molde(h: pd.DataFrame, anos: int = REFERENCIA_ANOS):
    """Dias historicos completos, guardados como (mes, tipo, media, 24 desviaciones).

    Se remuestrean DIAS REALES, no residuos sueltos sumados a una media. La diferencia
    importa: un dia real trae su forma entera -- el valle donde toca, el pico donde toca, y
    los episodios de precio negativo tal y como ocurrieron. Sumar residuos independientes
    generaba colas de -100 EUR/MWh que nunca se han visto: el minimo historico es -15.
    """
    r = h[h.dia > h.dia.max() - pd.DateOffset(years=anos)].copy()
    p = r.pivot_table(index="dia", columns="hora", values="precio")
    p = p.reindex(columns=range(24)).interpolate(axis=1, limit_direction="both").dropna()
    media = p.mean(axis=1)
    dev = p.sub(media, axis=0).to_numpy(dtype="float32")
    return {"mes": p.index.month.to_numpy(), "tipo": np.where(p.index.dayofweek >= 5, 1, 0),
            "media": media.to_numpy(dtype="float32"), "dev": dev,
            "sd": dev.std(axis=1), "sd_media": float(dev.std()),
            # media por MES, no global: el dia muestreado ya trae el nivel de su mes, asi
            # que al trasladarlo al futuro hay que restarle la media DE SU MES. Restar la
            # global contaba la estacionalidad dos veces -- con `fac_mes` de abril en 0,499
            # los dias de abril se hundian 42 EUR/MWh y topaban con el suelo.
            "media_mes": {m: float(media[p.index.month == m].mean()) for m in range(1, 13)}}


def simular(desde, hasta, nivel: dict, molde, fac_mes,
            n: int = 500, percentiles=(10, 50, 90), semilla=SEMILLA,
            amplitud: dict | None = None, suelo: float = -20.0) -> pd.DataFrame:
    """Monte Carlo por remuestreo de DIAS reales.

    Para cada dia futuro: se elige un dia historico del mismo mes y tipo, se le toma su
    forma intradiaria completa, se escala a la amplitud proyectada y se monta sobre el
    nivel del escenario.

    `amplitud` ensancha la forma por año: {2040: 1.4}. Sale de proyectar el spread con
    `modelo_spread` a partir de la capacidad solar.

    `suelo` es el precio minimo admisible. NO es un detalle cosmetico: el valle de mediodia
    NO baja indefinidamente al entrar solar. Se satura cerca de cero -- cuando el precio se
    hunde, los generadores dejan de ofertar -- y el spread sigue abriendose porque SUBE EL
    PICO. Extrapolar el spread linealmente sin suelo da valles de -100 EUR/MWh que no
    existen: el minimo de toda la serie 2020-2026 es -15. Cuantas horas topan con el suelo
    se informa, y si son muchas la hipotesis de amplitud es demasiado agresiva.
    """
    rng = np.random.default_rng(semilla)
    dias = pd.date_range(desde, hasta, freq="D")
    mes = dias.month.to_numpy()
    tipo = np.where(dias.dayofweek >= 5, 1, 0)
    amp = np.array([1.0 if not amplitud else amplitud.get(d.year, 1.0) for d in dias],
                   dtype="float32")
    lvl = np.array([nivel[d.year] * fac_mes.get(d.month, 1.0) for d in dias], dtype="float32")

    # candidatos por (mes, tipo): remuestrear un dia de julio con otro de julio
    cand = {}
    for m in range(1, 13):
        for t in (0, 1):
            i = np.where((molde["mes"] == m) & (molde["tipo"] == t))[0]
            cand[(m, t)] = i if len(i) else np.where(molde["mes"] == m)[0]

    sim = np.empty((n, len(dias), 24), dtype="float32")
    for k in range(n):
        pick = np.array([rng.choice(cand[(m, t)]) for m, t in zip(mes, tipo)])
        # la forma del dia elegido, reescalada a la amplitud del año, sobre el nivel
        # La forma del dia elegido SIN normalizar. Normalizarla a una sd comun forzaba a
        # todos los dias a tener la misma amplitud y mataba una fuente real de
        # variabilidad -- hay dias planos y dias violentos. Medido en backtest sobre 2025:
        # con normalizacion la sd simulada salia 33,4 frente a 47,6 real.
        base = molde["dev"][pick]
        # AMPLITUD ASIMETRICA. El spread crece porque SUBE EL PICO, no porque el valle baje
        # sin fondo: cuando el precio de mediodia se acerca a cero los generadores dejan de
        # ofertar, asi que el valle se satura. Ensanchar la forma de forma simetrica hundia
        # el valle a -100 y hacia que el 11 % de las horas topara con el suelo.
        # Al pico se le aplica la amplitud entera; al valle, solo el 30 % del exceso.
        arriba = amp[:, None]
        abajo = (1.0 + (amp - 1.0) * SATURA_VALLE)[:, None]
        forma = np.where(base >= 0, base * arriba, base * abajo)
        # la variabilidad del nivel diario: no todos los dias de un mes valen lo mismo
        # el nivel del dia elegido respecto a la media de su molde: la variabilidad
        # dia a dia dentro del mes, que tambien es real
        ref_mes = np.array([molde["media_mes"][m] for m in mes], dtype="float32")
        ruido = molde["media"][pick] - ref_mes
        sim[k] = lvl[:, None] + forma + (ruido * RUIDO_DIARIO)[:, None]

    sim = np.maximum(sim, suelo)
    plano = sim.reshape(n, -1)
    out = pd.DataFrame({f"p{q}": np.percentile(plano, q, axis=0).round(2)
                        for q in percentiles})
    out.insert(0, "hora", np.tile(range(24), len(dias)))
    out.insert(0, "dia", np.repeat(dias, 24))
    out.insert(2, "origen", "simulado")
    out.attrs["horas_en_el_suelo"] = int((plano <= suelo + 1e-6).mean() * 100 * len(plano[0]) / 100)
    out.attrs["pct_en_el_suelo"] = round(float((plano <= suelo + 1e-6).mean() * 100), 2)
    return out


def _de_predictions(desde, hasta, modelo="ensemble") -> pd.DataFrame:
    with _con() as con:
        d = pd.read_sql("""SELECT datetime, prediction FROM predictions
                           WHERE model = %(m)s AND datetime::date BETWEEN %(a)s AND %(b)s
                           ORDER BY datetime""",
                        con, params={"m": modelo, "a": desde, "b": hasta})
    if d.empty:
        return d
    t = pd.to_datetime(d.datetime, utc=True).dt.tz_convert(TZ)
    return pd.DataFrame({"dia": t.dt.normalize().dt.tz_localize(None), "hora": t.dt.hour,
                         "origen": "modelo", "p50": d.prediction.astype(float).round(2)})


def curva(desde, hasta, nivel: dict | None = None, modelo="ensemble",
          percentiles=(10, 50, 90), n=500, verbose=True,
          solar_gw: dict | None = None, suelo: float = -20.0) -> pd.DataFrame:
    """La curva del rango pedido, cosiendo los tres origenes."""
    desde, hasta = pd.Timestamp(desde), pd.Timestamp(hasta)
    h = historico()
    ultimo_real = h.dia.max()

    trozos = []
    if desde <= ultimo_real:
        fin = min(hasta, ultimo_real)
        r = h[(h.dia >= desde) & (h.dia <= fin)].copy()
        r["origen"], r["p50"] = "historico", r.precio.round(2)
        trozos.append(r[["dia", "hora", "origen", "p50"]])
        if verbose:
            print(f"  historico  {desde:%Y-%m-%d} -> {fin:%Y-%m-%d}  ({r.dia.nunique():,} dias)")

    if hasta > ultimo_real:
        ini = max(desde, ultimo_real + pd.Timedelta(days=1))
        m = _de_predictions(ini.date(), hasta.date(), modelo)
        if not m.empty:
            trozos.append(m)
            ini = m.dia.max() + pd.Timedelta(days=1)
            if verbose:
                print(f"  modelo     hasta {m.dia.max():%Y-%m-%d}  ({m.dia.nunique()} dias, {modelo})")

        if ini <= hasta:
            _, fac_mes, _ = perfil(h)
            molde = dias_molde(h)
            if nivel is None:
                ult = h[h.dia > h.dia.max() - pd.DateOffset(years=1)].precio.mean()
                nivel = {a: ult for a in range(ini.year, hasta.year + 1)}
                if verbose:
                    print(f"\n  AVISO: sin escenario de nivel. Se usa la media de los ultimos")
                    print(f"  12 meses ({ult:.1f} EUR/MWh) mantenida plana hasta {hasta.year}.")
                    print(f"  Eso es un MARCADOR DE POSICION, no una prevision: el nivel a")
                    print(f"  largo plazo sale de los futuros MIBEL o de un escenario, no de")
                    print(f"  esta serie. Pasalo con --nivel 2030=72 --nivel 2031=70\n")
            faltan = [a for a in range(ini.year, hasta.year + 1) if a not in nivel]
            if faltan:
                raise ValueError(f"falta el nivel de {faltan}. Pasalo con --nivel AÑO=EUR")
            amplitud = None
            if solar_gw:
                # la forma no se congela: se ensancha segun el spread que implica la
                # capacidad solar proyectada de cada año
                pred, info = modelo_spread()
                base = info["spread_ref"]
                amplitud = {a: pred(gw) / base for a, gw in solar_gw.items()}
                if verbose:
                    print(f"  forma proyectada · spread hoy {base:.1f} EUR/MWh "
                          f"con {info['solar_gw_hoy']:.0f} GW solar")
                    for a in sorted(amplitud):
                        print(f"     {a}: {solar_gw[a]:5.0f} GW -> spread "
                              f"{pred(solar_gw[a]):6.1f}  (x{amplitud[a]:.2f})")
            s = simular(ini, hasta, nivel, molde, fac_mes, n, percentiles,
                        amplitud=amplitud, suelo=suelo)
            trozos.append(s)
            if verbose:
                print(f"  simulado   {ini:%Y-%m-%d} -> {hasta:%Y-%m-%d}  "
                      f"({s.dia.nunique():,} dias · {n} escenarios · "
                      f"molde de {len(molde['media']):,} dias reales)")
                if s.attrs.get("pct_en_el_suelo", 0) > 0.5:
                    print(f"     {s.attrs['pct_en_el_suelo']:.1f}% de las horas topan con "
                          f"el suelo de {suelo:.0f} EUR/MWh -- si es mucho, la amplitud "
                          f"proyectada es demasiado agresiva")

    return pd.concat(trozos, ignore_index=True).sort_values(["dia", "hora"]).reset_index(drop=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--desde", default=str(date.today()))
    ap.add_argument("--hasta", default=str(date.today() + pd.Timedelta(days=365)))
    ap.add_argument("--nivel", action="append", default=[],
                    help="escenario de nivel anual, p.ej. --nivel 2030=72 (repetible)")
    ap.add_argument("--modelo", default="ensemble")
    ap.add_argument("--escenarios", type=int, default=500)
    ap.add_argument("--guardar")
    ap.add_argument("--deformacion", action="store_true",
                    help="solo la tabla de como se deforma el perfil, y sale")
    a = ap.parse_args()

    if a.deformacion:
        print("\n  Deformacion del perfil intradiario (EUR/MWh sobre la media del dia)\n")
        print(deformacion().to_string())
        print("\n  El valle de mediodia se hunde año a año: es la canibalizacion solar.")
        print("  Y el spread es lo que hace rentable una bateria, no el nivel del precio.")
        return

    nivel = {int(x.split("=")[0]): float(x.split("=")[1]) for x in a.nivel} or None
    c = curva(a.desde, a.hasta, nivel=nivel, modelo=a.modelo, n=a.escenarios)

    print(f"\n  {len(c):,} horas · {c.dia.nunique():,} dias")
    print(c.groupby("origen").agg(dias=("dia", "nunique"),
                                  media=("p50", "mean")).round(2).to_string())
    if a.guardar:
        c.to_csv(a.guardar, index=False)
        print(f"\n  guardado en {a.guardar}")
    else:
        print()
        print(c.head(6).to_string(index=False))


if __name__ == "__main__":
    main()
