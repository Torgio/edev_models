"""Registrar y ejecutar casos de estudio de bateria. Un solo comando, varios subcomandos.

    python scripts/caso.py usuario   --email tu@correo.es
    python scripts/caso.py bateria   --code LFP-4H --duracion 4 --potencia 1
    python scripts/caso.py consumo   --code FABRICA --fichero curva.xlsx --anual 350
    python scripts/caso.py generacion --code FV-250 --fichero fv.xlsx --mwp 0.25
    python scripts/caso.py crear     --code EST-01 --bateria LFP-4H --modo autoconsumo \
                                     --consumo FABRICA --generacion FV-250 \
                                     --desde 2024-01-01 --hasta 2035-12-31
    python scripts/caso.py ejecutar  --code EST-01 --escenarios 20
    python scripts/caso.py listar

EL REPARTO DEL PRECIO, QUE ES LO UNICO DELICADO
Un caso puede empezar en el pasado, y entonces el precio de cada dia viene de un sitio
distinto:

    pasado        `spot_price`      el PMD publicado. UNA sola realizacion.
    ya predicho   `predictions`     el ensemble del dia. Una sola realizacion.
    futuro        la curva          N escenarios.

Los dos primeros tramos se replican identicos en los N escenarios. No es un desperdicio de
memoria: es lo correcto. En el pasado el precio fue el que fue, asi que los N escenarios dan
el mismo resultado, los percentiles colapsan a un punto y la banda desaparece sola -- que es
justo lo que tiene que pasar. Si se guardara una banda sobre dias cuyo precio ya se conoce,
seria incertidumbre inventada.

`app_case_run.split_date` deja escrito donde esta la frontera, y `app_case_result_annual.origin`
marca de que fuente salio cada año.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from psycopg2.extras import execute_values

REPO = Path(__file__).resolve().parents[2]
# `production/app/x.py` -> el repo esta dos niveles arriba. Los MOTORES viven en `scripts/`
# y no se duplican aqui: los comparten los notebooks, y dos copias acabarian divergiendo.
sys.path.append(str(REPO / "ingesta"))
sys.path.append(str(REPO / "scripts"))
sys.path.append(str(Path(__file__).resolve().parent))   # los hermanos de esta carpeta
sys.path.append(str(REPO / "production" / "curva"))     # `generar_curva`, que publica
                                                        # el artefacto que aqui se lee


def _adaptadores_numpy():
    """Enseñar a psycopg2 a insertar escalares de numpy.

    Sin esto, un `np.float64` no tiene adaptador y psycopg2 cae en su `repr`. Con numpy 2 ese
    repr es `np.float64(41.88)`, que Postgres interpreta como una referencia al esquema `np`
    y responde `schema "np" does not exist`. Un error desconcertante para lo que en realidad
    es un problema de tipos, y que aparece solo al insertar -- las consultas funcionan.

    Se registra una vez, aqui, en vez de convertir a mano en cada INSERT: convertir a mano
    funciona hasta que alguien añade una columna y se olvida.
    """
    import numpy as _np
    from psycopg2.extensions import register_adapter, AsIs
    for t in (_np.int8, _np.int16, _np.int32, _np.int64,
              _np.float16, _np.float32, _np.float64):
        register_adapter(t, lambda v: AsIs(repr(v.item())))
    register_adapter(_np.bool_, lambda v: AsIs("TRUE" if v else "FALSE"))
    register_adapter(_np.ndarray, lambda v: AsIs(repr(v.tolist())))


def conexion():
    from config import load_config
    import psycopg2
    _, db = load_config()
    _adaptadores_numpy()
    return psycopg2.connect(**db)


def _uno(cur, sql, args=()):
    cur.execute(sql, args)
    r = cur.fetchone()
    return r[0] if r else None


# ─────────────────────────────────────────────────────────────────────────────
# registro
# ─────────────────────────────────────────────────────────────────────────────

def id_usuario(cur, email, crear=True) -> int:
    u = _uno(cur, "SELECT user_id FROM app_user WHERE email = %s", (email,))
    if u is None and crear:
        u = _uno(cur, "INSERT INTO app_user (email) VALUES (%s) RETURNING user_id", (email,))
        print(f"  usuario creado: {email} (id {u})")
    if u is None:
        raise SystemExit(f"no existe el usuario {email}")
    return u


def guardar_forma(cur, tabla, col_id, ident, forma: pd.DataFrame):
    """Vuelca las 576 filas de la forma normalizada, reemplazando lo que hubiera."""
    cur.execute(f"DELETE FROM {tabla} WHERE {col_id} = %s", (ident,))
    filas = [(ident, int(r.mes), r.tipo, int(r.hora), float(r.pu))
             for r in forma.itertuples()]
    execute_values(cur, f"INSERT INTO {tabla} ({col_id}, month, day_type, hour, "
                        f"value_pu) VALUES %s", filas)
    return len(filas)


def cmd_bateria(cur, uid, a):
    campos = dict(power_mw=a.potencia, duration_h=a.duracion,
                  charge_max_pct=a.carga_max, discharge_max_pct=a.descarga_max,
                  power_min_pct=a.p_min, efficiency_rt=a.eficiencia,
                  soc_min=a.soc_min, soc_max=a.soc_max, cycle_life=a.ciclos,
                  degradation_per_1000=a.degrada_1000,
                  degradation_annual=a.degrada_anual, capex_eur_mwh=a.capex)
    cols = ", ".join(campos)
    cur.execute(
        f"INSERT INTO app_battery_model (user_id, code, name, notes, {cols}) "
        f"VALUES (%s,%s,%s,%s,{','.join(['%s']*len(campos))}) "
        f"ON CONFLICT (user_id, code) DO UPDATE SET "
        f"{', '.join(f'{k}=EXCLUDED.{k}' for k in campos)}, name=EXCLUDED.name, "
        f"notes=EXCLUDED.notes "
        f"RETURNING battery_id",
        (uid, a.code, a.nombre or a.code, a.notas, *campos.values()))
    bid = cur.fetchone()[0]
    E = a.potencia * a.duracion
    util = E * (a.soc_max - a.soc_min)
    print(f"  bateria '{a.code}' (id {bid}) · {E:.1f} MWh · {util:.2f} utiles")
    print(f"  coste de ciclo: {(E*a.capex)/(a.ciclos*util):.1f} EUR/MWh descargado")
    if a.p_min > 0:
        print(f"  AVISO: minimo tecnico del {a.p_min:.0f}% -> el problema pasa a MILP y va")
        print(f"  entre 20 y 40 veces mas lento, para un 0,04% de margen. Ver la ficha.")


def cmd_instalacion(cur, uid, a, es_consumo: bool):
    from cargar_perfil import cargar, a_forma, auditar
    s = cargar(a.fichero, unidad=a.unidad)
    if a.auditar:
        auditar(s)
    f = a_forma(s)

    if es_consumo:
        cur.execute(
            "INSERT INTO app_consump_inst (user_id, code, name, annual_mwh, growth_pct, "
            "tariff_markup_eur_mwh, export_price_pct, contracted_power_mw, source_file) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (user_id, code) DO UPDATE SET annual_mwh=EXCLUDED.annual_mwh, "
            "growth_pct=EXCLUDED.growth_pct, "
            "tariff_markup_eur_mwh=EXCLUDED.tariff_markup_eur_mwh, "
            "export_price_pct=EXCLUDED.export_price_pct, source_file=EXCLUDED.source_file "
            "RETURNING consump_id",
            (uid, a.code, a.nombre or a.code, a.anual, a.crecimiento,
             a.recargo, a.excedente_pct, a.potencia_contratada,
             str(a.fichero)))
        i = cur.fetchone()[0]
        n = guardar_forma(cur, "app_consump_shape", "consump_id", i, f)
        print(f"  consumo '{a.code}' (id {i}) · {a.anual:,.0f} MWh/año · {n} filas de forma")
        print(f"  tarifa: +{a.recargo:.0f} EUR/MWh al importar · "
              f"{a.excedente_pct:.0f}% del spot al exportar")
    else:
        cur.execute(
            "INSERT INTO app_gen_inst (user_id, code, name, technology, capacity_mwp, "
            "degradation_pct, export_limit_mw, source_file) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (user_id, code) DO UPDATE SET capacity_mwp=EXCLUDED.capacity_mwp, "
            "degradation_pct=EXCLUDED.degradation_pct, source_file=EXCLUDED.source_file "
            "RETURNING gen_id",
            (uid, a.code, a.nombre or a.code, a.tecnologia, a.mwp, a.degradacion,
             a.limite_vertido, str(a.fichero)))
        i = cur.fetchone()[0]
        n = guardar_forma(cur, "app_gen_shape", "gen_id", i, f)
        print(f"  generacion '{a.code}' (id {i}) · {a.mwp:.3f} MWp · {n} filas de forma")


def cmd_crear(cur, uid, a):
    bid = _uno(cur, "SELECT battery_id FROM app_battery_model WHERE user_id=%s AND code=%s",
               (uid, a.bateria))
    if bid is None:
        raise SystemExit(f"no tienes ninguna bateria con el codigo '{a.bateria}'")
    cid = gid = None
    if a.consumo:
        cid = _uno(cur, "SELECT consump_id FROM app_consump_inst WHERE user_id=%s AND code=%s",
                   (uid, a.consumo))
        if cid is None:
            raise SystemExit(f"no existe el consumo '{a.consumo}'")
    if a.generacion:
        gid = _uno(cur, "SELECT gen_id FROM app_gen_inst WHERE user_id=%s AND code=%s",
                   (uid, a.generacion))
        if gid is None:
            raise SystemExit(f"no existe la generacion '{a.generacion}'")

    cur.execute(
        "INSERT INTO app_study_case (user_id, code, name, mode, battery_id, consump_id, "
        "gen_id, date_from, date_to, charge_policy, window_days, discount_rate, "
        "opex_pct, notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (user_id, code) DO UPDATE SET name=EXCLUDED.name, mode=EXCLUDED.mode, "
        "battery_id=EXCLUDED.battery_id, consump_id=EXCLUDED.consump_id, "
        "gen_id=EXCLUDED.gen_id, date_from=EXCLUDED.date_from, date_to=EXCLUDED.date_to, "
        "charge_policy=EXCLUDED.charge_policy, window_days=EXCLUDED.window_days "
        "RETURNING case_id",
        (uid, a.code, a.nombre or a.code, a.modo, bid, cid, gid, a.desde, a.hasta,
         a.politica, a.ventana, a.tasa, a.opex, a.notas))
    kid = cur.fetchone()[0]
    hoy = date.today()
    d0 = pd.Timestamp(a.desde).date()
    print(f"  caso '{a.code}' (id {kid}) · {a.modo} · {a.desde} -> {a.hasta}")
    if d0 < hoy:
        print(f"  empieza en el PASADO: el tramo hasta hoy es un BACKTEST sobre precio real,")
        print(f"  con una sola realizacion y sin banda de percentiles.")


# ─────────────────────────────────────────────────────────────────────────────
# el reparto del precio
# ─────────────────────────────────────────────────────────────────────────────

def precios(con, desde, hasta, n_esc, modelo="ensemble", verbose=True):
    """Cose las tres fuentes en un array (escenarios, dias, 24).

    El pasado y los dias ya predichos se replican identicos en los N escenarios. No es
    desperdicio: en el pasado el precio fue el que fue, los N escenarios dan el mismo
    resultado y los percentiles colapsan a un punto -- que es exactamente lo que debe pasar.
    """
    desde, hasta = pd.Timestamp(desde), pd.Timestamp(hasta)
    dias = pd.date_range(desde, hasta, freq="D")
    origen = pd.Series("simulado", index=dias)

    px = np.full((n_esc, len(dias), 24), np.nan, dtype="float32")

    # 1 · el precio real publicado
    real = pd.read_sql(
        "SELECT (datetime AT TIME ZONE 'Europe/Madrid')::date AS dia, "
        "EXTRACT(hour FROM datetime AT TIME ZONE 'Europe/Madrid')::int AS hora, "
        "es_esios FROM spot_price WHERE es_esios IS NOT NULL "
        "AND datetime >= %s AND datetime < %s", con, params=(desde, hasta + pd.Timedelta(days=1)))
    if len(real):
        piv = real.pivot_table(index="dia", columns="hora", values="es_esios", aggfunc="mean")
        piv.index = pd.to_datetime(piv.index)
        comun = dias.intersection(piv.index)
        pos = dias.get_indexer(comun)
        px[:, pos, :] = piv.reindex(comun).reindex(columns=range(24)).ffill(axis=1).to_numpy("float32")
        origen.loc[comun] = "historico"

    # 2 · los dias ya predichos que aun no tienen precio
    pred = pd.read_sql(
        "SELECT (datetime AT TIME ZONE 'Europe/Madrid')::date AS dia, "
        "EXTRACT(hour FROM datetime AT TIME ZONE 'Europe/Madrid')::int AS hora, "
        "prediction FROM predictions WHERE model = %s "
        "AND datetime >= %s AND datetime < %s", con,
        params=(modelo, desde, hasta + pd.Timedelta(days=1)))
    if len(pred):
        piv = pred.pivot_table(index="dia", columns="hora", values="prediction", aggfunc="mean")
        piv.index = pd.to_datetime(piv.index)
        faltan = dias[origen.to_numpy() == "simulado"].intersection(piv.index)
        if len(faltan):
            pos = dias.get_indexer(faltan)
            px[:, pos, :] = piv.reindex(faltan).reindex(columns=range(24)).ffill(axis=1).to_numpy("float32")
            origen.loc[faltan] = "modelo"

    # 3 · el futuro, de la curva publicada
    pend = dias[origen.to_numpy() == "simulado"]
    if len(pend):
        from generar_curva import leer
        sims, idx, meta = leer()
        dcurva = pd.DatetimeIndex(pd.to_datetime(idx.dia).unique())
        s = sims[:n_esc].reshape(min(n_esc, len(sims)), -1, 24)
        if len(s) < n_esc:
            s = np.repeat(s, int(np.ceil(n_esc / len(s))), axis=0)[:n_esc]
        comun = pend.intersection(dcurva)
        if len(comun) < len(pend):
            raise SystemExit(
                f"la curva publicada no cubre {len(pend)-len(comun)} dias del caso.\n"
                f"  Cubre {meta['desde']} -> {meta['hasta']}. Republica con:\n"
                f"    python scripts/production/curva/generar_curva.py --hasta {hasta.year}")
        px[:, dias.get_indexer(comun), :] = s[:, dcurva.get_indexer(comun), :]

    if np.isnan(px).any():
        malos = dias[np.isnan(px[0]).any(axis=1)]
        raise SystemExit(f"{len(malos)} dias sin precio de ninguna fuente, "
                         f"el primero {malos[0]:%Y-%m-%d}")

    corte = origen[origen == "simulado"]
    split = corte.index[0].date() if len(corte) else None
    if verbose:
        print(f"  precio: " + " · ".join(
            f"{k} {int(v)} dias" for k, v in origen.value_counts().items()))
        if split:
            print(f"  frontera en {split}: antes hay una realizacion, despues {n_esc}")
    return px, dias, origen, split


# ─────────────────────────────────────────────────────────────────────────────
# ejecutar
# ─────────────────────────────────────────────────────────────────────────────

def cmd_ejecutar(con, cur, uid, a):
    import time
    from optimiza_bateria import optimizar, sin_bateria, coste_ciclo

    c = pd.read_sql(
        "SELECT * FROM app_study_case WHERE user_id = %s AND code = %s", con,
        params=(uid, a.code))
    if c.empty:
        raise SystemExit(f"no tienes ningun caso con el codigo '{a.code}'")
    c = c.iloc[0]
    b = pd.read_sql("SELECT * FROM app_battery_model WHERE battery_id = %s", con,
                    params=(int(c.battery_id,),)).iloc[0]

    bat = dict(potencia_mw=float(b.power_mw), duracion_h=float(b.duration_h),
               p_carga_max_pct=float(b.charge_max_pct),
               p_descarga_max_pct=float(b.discharge_max_pct),
               p_min_pct=float(b.power_min_pct), eficiencia_rt=float(b.efficiency_rt),
               soc_min=float(b.soc_min), soc_max=float(b.soc_max),
               ciclos_vida=int(b.cycle_life), degrada_1000cic=float(b.degradation_per_1000),
               capex_eur_mwh=float(b.capex_eur_mwh),
               degrada_anual=float(b.degradation_annual))
    cc = float(c.cycle_cost_eur_mwh) if pd.notna(c.cycle_cost_eur_mwh) else coste_ciclo(bat)

    print(f"\n  caso '{c.code}' · {c['mode']} · {c.date_from} -> {c.date_to}")
    print(f"  bateria '{b.code}': {b.power_mw:.1f} MW / {b.duration_h:.0f} h · "
          f"coste de ciclo {cc:.1f} EUR/MWh")

    px, dias, origen, split = precios(con, c.date_from, c.date_to, a.escenarios)
    # La FOTO de la curva, no una referencia: la curva se pisa en cada publicacion, asi que
    # una clave foranea apuntaria a un contenido que ya es otro. Estos dos datos, copiados
    # ahora, si dicen sobre que se ejecuto.
    cur.execute("SELECT generated_at, matrix_hash FROM app_curve LIMIT 1")
    _c = cur.fetchone() or (None, None)
    curva_gen, curva_hash = _c

    consumo = generacion = None
    sitio = dict(recargo_tarifa=0.0, precio_excedente_pct=100.0,
                 politica_carga=c.charge_policy)
    if c["mode"] == "autoconsumo":
        from cargar_perfil import proyectar
        if pd.notna(c.consump_id):
            ci = pd.read_sql("SELECT * FROM app_consump_inst WHERE consump_id=%s", con,
                             params=(int(c.consump_id),)).iloc[0]
            fc = pd.read_sql("SELECT month mes, day_type tipo, hour hora, value_pu pu "
                             "FROM app_consump_shape WHERE consump_id=%s", con,
                             params=(int(c.consump_id),))
            consumo = proyectar(fc, float(ci.annual_mwh), dias[0], dias[-1],
                                float(ci.growth_pct)).valor.to_numpy().reshape(-1, 24)
            sitio.update(recargo_tarifa=float(ci.tariff_markup_eur_mwh),
                         precio_excedente_pct=float(ci.export_price_pct),
                         potencia_contratada=(float(ci.contracted_power_mw)
                                              if pd.notna(ci.contracted_power_mw) else None))
            print(f"  consumo '{ci.code}': {ci.annual_mwh:,.0f} MWh/año · "
                  f"+{ci.tariff_markup_eur_mwh:.0f} EUR/MWh al importar")
        if pd.notna(c.gen_id):
            gi = pd.read_sql("SELECT * FROM app_gen_inst WHERE gen_id=%s", con,
                             params=(int(c.gen_id),)).iloc[0]
            fg = pd.read_sql("SELECT month mes, day_type tipo, hour hora, value_pu pu "
                             "FROM app_gen_shape WHERE gen_id=%s", con,
                             params=(int(c.gen_id),))
            generacion = proyectar(fg, float(gi.capacity_mwp) * 1600, dias[0], dias[-1],
                                   -float(gi.degradation_pct)).valor.to_numpy().reshape(-1, 24)
            sitio["limite_vertido"] = (float(gi.export_limit_mw)
                                       if pd.notna(gi.export_limit_mw) else None)
            print(f"  generacion '{gi.code}': {gi.capacity_mwp:.3f} MWp")
        if consumo is None:
            consumo = np.zeros_like(generacion)
        if generacion is None:
            generacion = np.zeros_like(consumo)

    t0 = time.time()
    ns = len(px)
    margen = np.zeros((ns, len(dias)))
    ciclos = np.zeros_like(margen)
    carga = np.zeros_like(margen)
    descarga = np.zeros_like(margen)
    importado = np.zeros_like(margen)
    exportado = np.zeros_like(margen)
    ahorro = np.zeros_like(margen)
    perfiles = {}
    for s in range(ns):
        r, det = optimizar(px[s], bat, sitio, consumo, generacion, cc,
                           ventana=int(c.window_days), detalle=True)
        base = (sin_bateria(px[s], consumo, generacion, sitio)
                if c["mode"] == "autoconsumo" else None)
        margen[s] = r.margen_eur.to_numpy()
        ahorro[s] = margen[s] - (base.margen_eur.to_numpy() if base is not None else 0.0)
        ciclos[s] = r.ciclos.to_numpy()
        carga[s] = r.carga_mwh.to_numpy()
        descarga[s] = r.descarga_mwh.to_numpy()
        importado[s] = r.importado_mwh.to_numpy()
        exportado[s] = r.exportado_mwh.to_numpy()
        perfiles[s] = det
        print(f"    escenario {s+1}/{ns} ({time.time()-t0:.0f}s)", end="\r")
    seg = time.time() - t0
    print(f"\n  resuelto en {seg:.0f}s")
    # en autoconsumo lo que se reporta es el AHORRO frente a no tener bateria; en
    # standalone no hay contrafactual y el ahorro es el propio margen
    valor = ahorro if c["mode"] == "autoconsumo" else margen

    anos = dias.year.to_numpy()

    # ── vida util y finanzas ────────────────────────────────────────────────
    # La vida sale de la degradacion COMBINADA: ciclado mas calendario, lo que llegue antes
    # al 20 % de perdida. Con el uso tipico manda el calendario, no los ciclos.
    cd = float(ciclos.mean())
    ejes = np.arange(0, 41, .25)
    perd = (cd * 365 * ejes / 1000 * bat["degrada_1000cic"]
            + bat["degrada_anual"] * ejes)
    vida = float(ejes[np.argmax(perd >= 20)]) if (perd >= 20).any() else 40.0

    E_tot = bat["potencia_mw"] * bat["duracion_h"]
    capex = E_tot * bat["capex_eur_mwh"]
    tasa, opex = float(c.discount_rate), float(c.opex_pct)
    por_ano_esc = np.stack([valor[:, anos == y].sum(axis=1) for y in np.unique(anos)],
                           axis=1)
    completos = np.array([int((anos == y).sum()) >= 365 for y in np.unique(anos)])

    def _van(fila):
        # VAN de una trayectoria. Solo los años completos y solo la vida util: la bateria se
        # muere antes del horizonte, asi que contar veinte años seria regalarle doce.
        fl = fila[completos][:int(vida)] - opex * capex
        if not len(fl):
            return np.nan
        return float(-capex + np.sum(fl / (1 + tasa) ** np.arange(1, len(fl) + 1)))

    vans = np.array([_van(por_ano_esc[k]) for k in range(ns)])
    vans = vans[~np.isnan(vans)]
    # bruto = neto mas el desgaste que ya se le habia descontado; compararlo otra vez con el
    # CAPEX seria contarlo dos veces, porque el coste de ciclo ES la amortizacion
    mwh_vida = float(descarga.mean(axis=0).sum()) / len(dias) * 365 * vida
    bruto_vida = (float(valor.mean(axis=0).sum()) / len(dias) * 365 * vida
                  + cc * mwh_vida)

    # ── guardar ─────────────────────────────────────────────────────────────
    n_hist = int((origen != "simulado").sum())
    cur.execute(
        "INSERT INTO app_case_run (case_id, curve_generated_at, curve_matrix_hash, "
        "run_at, split_date, "
        "days_historical, days_simulated, n_scenarios, solver, solver_seconds, "
        "margin_total_mean, margin_annual_mean, savings_vs_no_batt, cycles_per_day, "
        "life_years, npv_p10, npv_p50, npv_p90, npv_positive_pct, capex_coverage_pct, "
        "notes) VALUES (%s,%s,%s, now(), %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "RETURNING run_id",
        (int(c.case_id), curva_gen, curva_hash, split, n_hist, len(dias) - n_hist, ns,
         "highs-milp" if b.power_min_pct > 0 else "highs-lp", seg,
         float(margen.sum(axis=1).mean()),
         float(valor.sum(axis=1).mean() / len(dias) * 365 / bat["potencia_mw"]),
         float(ahorro.sum(axis=1).mean()) if c["mode"] == "autoconsumo" else None,
         cd, vida,
         # el `*` desempaqueta, asi que el condicional tiene que ir DENTRO de los
         # parentesis o Python lo lee como `*gen if cond else tupla` y no compila
         *((float(np.percentile(vans, q)) for q in (10, 50, 90))
           if len(vans) else (None, None, None)),
         float((vans > 0).mean() * 100) if len(vans) else None,
         float(bruto_vida / capex * 100),
         a.notas))
    rid = cur.fetchone()[0]

    filas = []
    for y in np.unique(anos):
        m = anos == y
        tot = valor[:, m].sum(axis=1)
        org = origen[m].mode()[0]
        # Los percentiles SOLO en los años simulados. En el tramo historico el precio fue el
        # que fue, los N escenarios dan lo mismo y una banda seria incertidumbre inventada.
        simulado = org == "simulado"
        filas.append((rid, int(y), org, int(m.sum()), float(tot.mean()),
                      *[float(np.percentile(tot, q)) if simulado else None
                        for q in (5, 10, 25, 50, 75, 90, 95)],
                      float(ciclos[:, m].mean()),
                      float(carga[:, m].sum(axis=1).mean()),
                      float(descarga[:, m].sum(axis=1).mean()),
                      float(importado[:, m].sum(axis=1).mean()),
                      float(exportado[:, m].sum(axis=1).mean())))
    execute_values(
        cur,
        "INSERT INTO app_case_result_annual (run_id, year, origin, days, margin_mean, "
        "p5,p10,p25,p50,p75,p90,p95, cycles_per_day, energy_charged_mwh, "
        "energy_discharged_mwh, grid_import_mwh, grid_export_mwh) VALUES %s "
        "ON CONFLICT (run_id, year) DO NOTHING", filas)

    # ── el despacho horario, solo de tres escenarios ────────────────────────
    # El P10, el P50 y el P90 por margen TOTAL, no año a año: una trayectoria cosida de
    # percentiles anuales no le ocurre a nadie. Con tres son 526.000 filas por ejecucion;
    # con los 50 serian 8,8 millones.
    if a.guardar_despacho:
        tot_esc = valor.sum(axis=1)
        orden = np.argsort(tot_esc)
        elegidos = sorted({int(orden[int(.10 * ns)]), int(orden[ns // 2]),
                           int(orden[min(int(.90 * ns), ns - 1)])})
        ts = np.repeat(dias.to_numpy(), 24) + np.tile(
            np.arange(24) * np.timedelta64(1, "h"), len(dias))
        n_d = 0
        for k in elegidos:
            det = perfiles[k]
            # `.tolist()` da floats de Python: no depende del adaptador y va mas rapido
            # que dejar que psycopg2 convierta ocho millones de escalares de numpy
            cero = [0.0] * len(ts)
            fil = list(zip(
                [rid] * len(ts), [k] * len(ts), pd.DatetimeIndex(ts).to_pydatetime(),
                px[k].ravel().tolist(), det["carga"].ravel().tolist(),
                det["descarga"].ravel().tolist(), det["soc"].ravel().tolist(),
                det["imp"].ravel().tolist(), det["exp"].ravel().tolist(),
                consumo.ravel().tolist() if consumo is not None else cero,
                generacion.ravel().tolist() if generacion is not None else cero))
            # `executemany` de psycopg2 hace UNA ida y vuelta POR FILA. Con 61.000 filas
            # por escenario y un servidor remoto son minutos de latencia pura.
            # `execute_values` las manda por lotes: la misma insercion baja a segundos.
            execute_values(
                cur,
                "INSERT INTO app_case_dispatch (run_id, scenario, datetime, price, "
                "charge_mw, discharge_mw, soc_mwh, grid_import_mwh, grid_export_mwh, "
                # sin ON CONFLICT: si dos filas chocan es un fallo real y hay que verlo.
                # Con el `DO NOTHING` que habia, las horas del cambio de hora se perdian en
                # silencio y la tabla parecia completa.
                "load_mwh, generation_mwh) VALUES %s",
                fil, page_size=2000)
            n_d += len(fil)
        print(f"  despacho horario de {len(elegidos)} escenarios: {n_d:,} filas")

    print(f"\n  guardado en app_case_run id {rid} · {len(filas)} años")
    print(f"  vida util {vida:.0f} años · {cd:.2f} ciclos/dia")
    if len(vans):
        print(f"  VAN al {tasa:.0%}: P10 {np.percentile(vans,10):,.0f} · "
              f"P50 {np.percentile(vans,50):,.0f} · P90 {np.percentile(vans,90):,.0f} EUR")
        print(f"  escenarios con VAN positivo: {(vans>0).mean():.0%} · "
              f"cobertura del CAPEX {bruto_vida/capex:.0%}")
    d = pd.DataFrame(filas, columns=["run", "año", "origen", "dias", "media",
                                     "p5", "p10", "p25", "p50", "p75", "p90", "p95",
                                     "ciclos", "carga", "descarga",
                                     "importado", "exportado"]).set_index("año")
    print(d[["origen", "dias", "media", "p10", "p50", "p90", "ciclos"]].round(1).to_string())
    print(f"\n  los percentiles van a NULL en los años historicos: ahi el precio fue el que")
    print(f"  fue y solo hay una realizacion.")


def cmd_listar(con, uid):
    d = pd.read_sql("SELECT * FROM app_case_summary WHERE email = "
                    "(SELECT email FROM app_user WHERE user_id = %s)", con, params=(uid,))
    if d.empty:
        print("  todavia no hay casos")
        return
    print(d.to_string(index=False))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("comando", choices=["usuario", "bateria", "consumo", "generacion",
                                        "crear", "ejecutar", "listar"])
    ap.add_argument("--email", default=None)
    ap.add_argument("--code"); ap.add_argument("--nombre")
    # bateria
    ap.add_argument("--potencia", type=float, default=1.0)
    ap.add_argument("--duracion", type=float, default=4.0)
    ap.add_argument("--carga-max", type=float, default=100.0)
    ap.add_argument("--descarga-max", type=float, default=100.0)
    ap.add_argument("--p-min", type=float, default=0.0)
    ap.add_argument("--eficiencia", type=float, default=0.90)
    ap.add_argument("--soc-min", type=float, default=0.05)
    ap.add_argument("--soc-max", type=float, default=0.95)
    ap.add_argument("--ciclos", type=int, default=6000)
    ap.add_argument("--degrada-1000", type=float, default=3.0)
    ap.add_argument("--degrada-anual", type=float, default=1.5)
    ap.add_argument("--capex", type=float, default=200000.0)
    # instalaciones
    ap.add_argument("--fichero"); ap.add_argument("--unidad", default="auto")
    ap.add_argument("--auditar", action="store_true")
    ap.add_argument("--anual", type=float, default=350.0)
    ap.add_argument("--crecimiento", type=float, default=1.0)
    ap.add_argument("--recargo", type=float, default=70.0)
    ap.add_argument("--excedente-pct", type=float, default=80.0)
    ap.add_argument("--mwp", type=float, default=0.25)
    ap.add_argument("--tecnologia", default="fv")
    ap.add_argument("--degradacion", type=float, default=0.5)
    # caso
    ap.add_argument("--modo", default="standalone",
                    choices=["standalone", "autoconsumo"])
    ap.add_argument("--bateria"); ap.add_argument("--consumo"); ap.add_argument("--generacion")
    ap.add_argument("--desde"); ap.add_argument("--hasta")
    ap.add_argument("--politica", default="libre",
                    choices=["libre", "prefiere_excedente", "solo_excedente"])
    ap.add_argument("--ventana", type=int, default=7)
    ap.add_argument("--tasa", type=float, default=0.07)
    ap.add_argument("--opex", type=float, default=0.015)
    ap.add_argument("--escenarios", type=int, default=20)
    ap.add_argument("--notas", default=None)
    ap.add_argument("--potencia-contratada", type=float, default=None,
                    help="MW: tope de importacion de la acometida")
    ap.add_argument("--limite-vertido", type=float, default=None,
                    help="MW: tope de exportacion a red")
    ap.add_argument("--sin-despacho", dest="guardar_despacho", action="store_false",
                    help="no volcar app_case_dispatch (526.000 filas por ejecucion)")
    a = ap.parse_args()

    import os
    email = a.email or os.environ.get("TFM_EMAIL")
    if not email:
        raise SystemExit("hace falta --email (o la variable de entorno TFM_EMAIL)")

    con = conexion()
    try:
        with con.cursor() as cur:
            uid = id_usuario(cur, email, crear=(a.comando == "usuario"))
            if a.comando == "usuario":
                print(f"  usuario {email} (id {uid})")
            elif a.comando == "bateria":
                cmd_bateria(cur, uid, a)
            elif a.comando in ("consumo", "generacion"):
                cmd_instalacion(cur, uid, a, a.comando == "consumo")
            elif a.comando == "crear":
                cmd_crear(cur, uid, a)
            elif a.comando == "ejecutar":
                cmd_ejecutar(con, cur, uid, a)
            elif a.comando == "listar":
                cmd_listar(con, uid)
        con.commit()
    finally:
        con.close()


if __name__ == "__main__":
    main()
