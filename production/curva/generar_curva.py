"""Publicar la curva de 20 años como ARTEFACTO, para que la optimizacion no la recalcule.

POR QUE UN ARTEFACTO Y NO UNA FUNCION
Hasta ahora, optimizar una bateria obligaba a reconstruir la curva entera: leer la matriz,
ajustar rendimientos, ajustar la curva de oferta y sortear 50 escenarios de 175.320 horas. Son
minutos, y ademas hace que el resultado dependa de que el que optimiza tenga toda la cadena
montada.

Separandolo, el cron publica la curva una vez al dia y quien optimiza solo lee un fichero. La
bateria deja de necesitar la matriz, la base de datos ni los modelos: le basta el precio.

QUE SE PUBLICA
    curva_escenarios.npy    (escenarios, horas) en float32 -- 33 MB con 50 escenarios
    curva_indice.parquet    el eje temporal, una fila por hora
    curva_meta.json         que escenarios de gas, demanda y capacidad se usaron, y cuando

Los percentiles NO se guardan aparte: se calculan del `.npy` en un instante y guardarlos
seria una copia mas que se puede desincronizar. El CSV de percentiles que consume la memoria
lo escribe el notebook, que es donde se presenta.

POR QUE LOS ESCENARIOS Y NO EL P50
Porque una bateria hay que operarla escenario a escenario y promediar el ingreso despues. El
P50 es una mediana entre escenarios: aplana los extremos, y los extremos son de donde sale el
dinero. Guardar solo percentiles obligaria a operar sobre el P50 y subestimaria el negocio.

    python production/curva/generar_curva.py                  # hasta 2046, 50 escenarios
    python production/curva/generar_curva.py --hasta 2040 --escenarios 100
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
# `production/curva/x.py` -> el repo esta dos niveles arriba. El motor de la curva
# (`curva_fundamental`) se queda en `scripts/`, compartido con los notebooks.
sys.path.append(str(REPO / "scripts"))

SALIDA = REPO / "data" / "gold" / "curva_futuro"

# LA MATRIZ DE PRODUCCION, no el nucleo. El nucleo esta congelado para que los escaladores
# sigan casando con los modelos entrenados; la curva no usa ninguno de ellos -- ajusta su
# propia curva de oferta desde el panel -- asi que la congelacion no le aporta nada. Y le
# quita algo: las anclas salen del ultimo año OBSERVADO, y con el nucleo parado en julio la
# curva se anclaria a julio para siempre, alejandose de la realidad un dia mas cada dia.
MATRIZ = "produccion"
ESCENARIOS = 50
ANO_FIN = 2046


def escenarios_por_defecto(panel, a0: int, a1: int) -> dict:
    """Los cuatro escenarios fisicos, anclados al ultimo año OBSERVADO.

    Anclar al observado y no a un numero escrito a mano es lo que hace que la curva empalme
    con la realidad, y lo que permite republicarla a diario sin tocar nada: cada dia que
    entra dato nuevo, el ancla se mueve sola.
    """
    from curva_precios import por_anclas
    o = panel[panel.ano == panel.ano.max()]
    gas, dem = float(o.gas_mibgas.mean()), float(o.demanda.mean())
    sol, eol = float(o.solar_gw.mean()), float(o.eolica_gw.mean())
    return dict(
        gas=por_anclas({a0: gas, 2035: gas * .82, a1: gas * .74}, a0, a1),
        demanda=por_anclas({a0: dem, a1: dem * 1.01 ** (a1 - a0)}, a0, a1),
        solar_gw=por_anclas({a0: sol, 2030: 76, 2035: 95, 2040: 110, a1: 125}, a0, a1),
        eolica_gw=por_anclas({a0: eol, 2030: 43, 2040: 55, a1: 62}, a0, a1))


def publicar(hasta: int = ANO_FIN, n: int = ESCENARIOS, salida: Path = SALIDA,
             escenarios: dict | None = None, matriz: str = MATRIZ, verbose=True) -> dict:
    import curva_fundamental as cfun

    P = cfun.panel(matriz)
    meta_m = cfun.meta_matriz(matriz)
    ultimo = pd.Timestamp(P.dia.max())
    desde = ultimo + pd.Timedelta(days=1)
    a0 = desde.year
    esc = escenarios or escenarios_por_defecto(P, a0, hasta)

    if verbose:
        print(f"  matriz                : {matriz} · hash {meta_m.get('hash', '?')} · "
              f"generada {str(meta_m.get('generada', '?'))[:16]}")
        print(f"  ultimo dato observado : {ultimo:%Y-%m-%d}")
        print(f"  tramo simulado        : {desde:%Y-%m-%d} -> {hasta}-12-31")
        print(f"  escenarios            : {n}")

    potencial, info_r = cfun.rendimientos(P)
    D = cfun.con_residual(P, potencial)
    precio, info_c = cfun.curva_oferta(D)
    CF, sims = cfun.simular(desde, f"{hasta}-12-31", **esc, potencial=potencial,
                            precio=precio, n=n, verbose=False, crudo=True)

    salida.mkdir(parents=True, exist_ok=True)
    np.save(salida / "curva_escenarios.npy", sims.astype("float32"))
    idx = CF[["dia", "hora"]].copy()
    idx["ts"] = idx.dia + pd.to_timedelta(idx.hora, unit="h")
    idx.to_parquet(salida / "curva_indice.parquet", index=False)

    meta = {
        "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ultimo_dato_observado": f"{ultimo:%Y-%m-%d}",
        "desde": f"{desde:%Y-%m-%d}", "hasta": f"{hasta}-12-31",
        "escenarios": int(n), "horas": int(sims.shape[1]),
        "dias": int(CF.dia.nunique()),
        # del meta.json de la matriz, NO de `D.attrs`: un DataFrame leido de disco tiene
        # `.attrs` vacio y esto habria sido None siempre, con la columna existiendo en la
        # tabla y nadie dandose cuenta
        "matriz": matriz,
        "matriz_hash": meta_m.get("hash"),
        "matriz_generada": str(meta_m.get("generada", "")),
        "rendimientos": info_r,
        "curva_oferta": {k: v for k, v in info_c.items()
                         if not isinstance(v, np.ndarray)},
        "escenario_gas": {str(k): round(v, 2) for k, v in esc["gas"].items()},
        "escenario_demanda": {str(k): round(v, 0) for k, v in esc["demanda"].items()},
        "escenario_solar_gw": {str(k): round(v, 1) for k, v in esc["solar_gw"].items()},
        "escenario_eolica_gw": {str(k): round(v, 1) for k, v in esc["eolica_gw"].items()},
    }
    (salida / "curva_meta.json").write_text(
        json.dumps(meta, indent=1, ensure_ascii=False, default=str), encoding="utf-8")

    if verbose:
        mb = (salida / "curva_escenarios.npy").stat().st_size / 2 ** 20
        print(f"\n  {sims.shape[0]} x {sims.shape[1]:,} = {sims.size/1e6:.1f} M precios "
              f"· {mb:.1f} MB")
        print(f"  media {sims.mean():.2f} EUR/MWh · horas <= 0: "
              f"{(sims <= 0).mean():.1%}")
        print(f"  publicado en {salida}")
    return meta, sims, idx


def registrar(meta: dict, sims, idx, salida: Path = SALIDA, verbose=True) -> str | None:
    """Deja constancia de la curva en la base y devuelve su `curve_id`.

    Es la trazabilidad que permite reproducir un resultado: la curva se republica a diario y
    sus escenarios cambian, asi que dos casos ejecutados con dos semanas de diferencia no son
    comparables si no se sabe sobre que curva corrio cada uno.

    Los percentiles horarios si van a la base -- son 175.320 filas y es lo que consulta la
    web -- pero los escenarios no: 8,8 millones de flotantes se quedan en el `.npy`.
    """
    import json as _json
    sys.path.append(str(REPO / "ingesta"))
    try:
        from config import load_config
        import psycopg2
    except ImportError:
        if verbose:
            print("  (sin psycopg2: la curva queda solo en disco)")
        return None
    _, db = load_config()
    con = psycopg2.connect(**db)
    try:
        with con.cursor() as cur:
            cur.execute("SELECT to_regclass('public.app_curve')")
            if cur.fetchone()[0] is None:
                if verbose:
                    print("  (no existe app_curve: la curva queda solo en disco)")
                return None
            # UNA sola curva y sin id: se borra la fila que hubiera y se escribe la
            # nueva. Sin `curve_id` no hay clave foranea que respetar, asi que aqui si se
            # puede borrar -- lo que los casos guardan es una FOTO (`curve_generated_at` y
            # `curve_matrix_hash`), no una referencia.
            campos = ("generated_at, last_observed_date, date_from, date_to, n_scenarios, "
                      "n_hours, engine, matrix_name, matrix_hash, artifact_path, "
                      "gas_scenario, demand_scenario, solar_scenario, wind_scenario")
            vals = (meta["generado"], meta["ultimo_dato_observado"], meta["desde"],
                    meta["hasta"], meta["escenarios"], meta["horas"], "fundamental",
                    meta.get("matriz"), meta.get("matriz_hash"),
                    str((salida / "curva_escenarios.npy").resolve()),
                    _json.dumps(meta["escenario_gas"]),
                    _json.dumps(meta["escenario_demanda"]),
                    _json.dumps(meta["escenario_solar_gw"]),
                    _json.dumps(meta["escenario_eolica_gw"]))
            cur.execute("DELETE FROM app_curve")
            cur.execute("DELETE FROM app_curve_hourly")
            cur.execute(f"INSERT INTO app_curve ({campos}) "
                        f"VALUES ({','.join(['%s'] * len(vals))})", vals)

            ts = (pd.to_datetime(idx.dia) + pd.to_timedelta(idx.hora, unit="h"))
            p10, p50, p90 = np.percentile(sims, [10, 50, 90], axis=0)
            # `.tolist()` da floats de Python. Con numpy 2, un `np.float64` sin adaptador se
            # serializa como `np.float64(41.88)` y Postgres responde `schema "np" does not
            # exist` -- un error desconcertante para lo que es un problema de tipos.
            filas = list(zip(list(ts.dt.to_pydatetime()),
                             p10.tolist(), p50.tolist(), p90.tolist()))
            # `execute_values` y no `executemany`: este hace una ida y vuelta POR FILA, y con
            # 178.000 filas contra un servidor remoto son minutos de latencia pura.
            from psycopg2.extras import execute_values
            execute_values(cur, "INSERT INTO app_curve_hourly (datetime, p10, p50, p90) "
                                "VALUES %s", filas, page_size=5000)
        con.commit()
        if verbose:
            print(f"  registrada · {len(filas):,} filas de percentiles "
                  f"(se pisa la anterior: una sola curva)")
        return meta["generado"]
    finally:
        con.close()


def leer(salida: Path = SALIDA):
    """Devuelve (escenarios, indice, meta). Es lo unico que necesita el optimizador."""
    salida = Path(salida)
    if not (salida / "curva_escenarios.npy").exists():
        raise FileNotFoundError(
            f"no hay curva publicada en {salida}.\n"
            f"  Ejecuta primero:  python production/curva/generar_curva.py")
    sims = np.load(salida / "curva_escenarios.npy")
    idx = pd.read_parquet(salida / "curva_indice.parquet")
    meta = json.loads((salida / "curva_meta.json").read_text(encoding="utf-8"))
    return sims, idx, meta


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--hasta", type=int, default=ANO_FIN)
    ap.add_argument("--escenarios", type=int, default=ESCENARIOS)
    ap.add_argument("--salida", default=str(SALIDA))
    ap.add_argument("--matriz", default=MATRIZ,
                    help="produccion (la que se reconstruye a diario) o nucleo (congelada)")
    ap.add_argument("--info", action="store_true", help="solo leer lo publicado")
    ap.add_argument("--registrar", action="store_true",
                    help="ademas de escribir el .npy, deja constancia en app_curve")
    a = ap.parse_args()

    if a.info:
        sims, idx, meta = leer(Path(a.salida))
        print(f"\n  curva publicada el {meta['generado']}")
        print(f"  {meta['escenarios']} escenarios x {meta['horas']:,} horas "
              f"({meta['desde']} -> {meta['hasta']})")
        print(f"  ultimo dato observado: {meta['ultimo_dato_observado']}")
        print(f"  media {sims.mean():.2f} EUR/MWh · horas <= 0 {(sims<=0).mean():.1%}")
        return
    if a.matriz == "nucleo":
        print("  AVISO: el nucleo esta congelado y sus anclas se quedan en la fecha de\n"
              "  congelacion. Para una curva que se republica a diario, la buena es\n"
              "  'produccion'. Sigo, por si es a proposito.")
    meta, sims, idx = publicar(a.hasta, a.escenarios, Path(a.salida), matriz=a.matriz)
    if a.registrar:
        registrar(meta, sims, idx, Path(a.salida))


if __name__ == "__main__":
    main()
