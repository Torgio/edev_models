"""Utilidades compartidas por los cuadernos de análisis exploratorio e ingeniería de variables.

Sigue la convención del máster (`FuncionesMineria.py`): las funciones viven en un módulo y
el cuaderno contiene únicamente el análisis. Aquí se recoge lo que **ambos** cuadernos
necesitan y que, de duplicarse, podría divergir entre ellos:

    - la carga del bronce con su verificación de integridad,
    - los ejes de calendario y la marca de régimen,
    - la resolución de nombres de columna,
    - la **frontera de disponibilidad**, que es el contrato entre los dos cuadernos,
    - la clasificación de horas baratas / intermedias / caras,
    - el registro de hallazgos y de decisiones.

El reparto de responsabilidades entre los dos cuadernos es:

    01_analisis_exploratorio    describe y diagnostica. No modifica ningún valor.
    02_ingenieria_variables     construye y trata. No descubre nada nuevo sobre el dato.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Constantes del proyecto
# ---------------------------------------------------------------------------

TZ = "Europe/Madrid"

#: Períodos que responden a otro mecanismo de formación de precios.
APAGON = (pd.Timestamp("2025-04-28", tz="UTC"), pd.Timestamp("2025-05-06 23:00", tz="UTC"))
#: Fechas del mecanismo excepcional de ajuste, según su publicación oficial.
EXCEPCION = (pd.Timestamp("2022-06-15", tz="UTC"), pd.Timestamp("2023-12-31 23:00", tz="UTC"))

PALETA = {"principal": "#1f4e79", "secundario": "#2b7bba", "acento": "#f5a623",
          "alerta": "#c0392b", "ok": "#2ca02c", "neutro": "#7f7f7f", "suave": "#d9d9d9"}

COLOR_FUGA = {"SIN FUGA": PALETA["ok"], "DESFASE D-2": PALETA["acento"],
              "CON FUGA": PALETA["alerta"], "CONDICIONAL": PALETA["neutro"],
              "TARGET": "#000000"}

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

#: Horas de carga y de descarga que se suponen a la batería en el análisis económico.
K_HORAS = 4


def raiz_repo(inicio: Path | None = None) -> Path:
    """Devuelve la raíz del repositorio, se ejecute desde `eda/` o desde la raíz."""
    actual = (inicio or Path.cwd()).resolve()
    for cand in [actual, *actual.parents]:
        if (cand / "data").is_dir() and (cand / "scripts").is_dir():
            return cand
    return actual


RAIZ = raiz_repo()


def configurar_matplotlib():
    """Estilo común a los dos cuadernos, para que las figuras de la memoria sean homogéneas."""
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "figure.figsize": (10, 3.8), "font.size": 9,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.alpha": 0.25,
        "axes.prop_cycle": plt.cycler(color=[PALETA["principal"], PALETA["acento"], PALETA["ok"],
                                             PALETA["alerta"], PALETA["secundario"],
                                             PALETA["neutro"]]),
    })
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 190)


# ---------------------------------------------------------------------------
# Carga
# ---------------------------------------------------------------------------

def cargar_bronce(ruta: Path | None = None) -> pd.DataFrame:
    """Carga la capa bronce, verifica su integridad y añade los ejes de calendario.

    La verificación de una fila por hora no es decorativa: si alguna tabla está declarada
    con el grano equivocado, el merge la multiplica, y los porcentajes y las correlaciones
    sobreviven a una duplicación uniforme sin dar ninguna señal. Es exactamente lo que
    ocurrió con un parquet de 1.393.183 filas que debía tener 58.319.

    Las columnas de calendario que se añaden aquí son **ejes de análisis**, no variables del
    modelo: el cuaderno de ingeniería las vuelve a construir con su codificación definitiva.
    """
    ruta = ruta or (RAIZ / "data" / "bronze" / "bronze_unificado.parquet")
    df = pd.read_parquet(ruta)
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)

    dup = df["ts_utc"].duplicated().sum()
    assert dup == 0, (f"{dup:,} horas repetidas: {len(df):,} filas para "
                      f"{df['ts_utc'].nunique():,} horas.")

    df["ts_local"] = df["ts_utc"].dt.tz_convert(TZ)
    df["hora"] = df["ts_local"].dt.hour
    df["dia"] = df["ts_local"].dt.date
    df["anio"] = df["ts_local"].dt.year
    df["mes"] = df["ts_local"].dt.month
    df["dow"] = df["ts_local"].dt.dayofweek
    # El período mensual es un eje de agrupación, no un instante: se descarta la zona horaria
    # de forma explícita para que pandas no avise de que la está perdiendo.
    df["mes_p"] = df["ts_local"].dt.tz_localize(None).dt.to_period("M")
    df["regimen"] = np.select(
        [df["ts_utc"].between(*APAGON), df["ts_utc"].between(*EXCEPCION)],
        ["apagón", "excepción ibérica"], default="normal")
    return df


def pick(d: pd.DataFrame, *candidatas):
    """Primera de `candidatas` que exista en `d`. Evita que un cambio de nombre rompa todo."""
    for x in candidatas:
        if x is not None and x in d.columns:
            return x
    return None


def resolver_columnas(df: pd.DataFrame) -> dict:
    """Resuelve una sola vez los nombres de columna que ambos cuadernos utilizan."""
    C = {
        "P":       pick(df, "spot_es_esios", "spot_es_omie", "spot_es_entsoe"),
        "DEM":     pick(df, "load_inter_entsoe_load"),
        "EOL":     pick(df, "entsoe_wind_mw"),
        "FV":      pick(df, "calc_solar_fv_mw"),
        "HID":     pick(df, "calc_hydro_dispatch_mw"),
        "CCGT":    pick(df, "esios_gen_ree_gccgas_mw"),
        "OTHERTH": pick(df, "esios_gen_ree_gotherthermal_mw"),
        "GAS_ENT": pick(df, "entsoe_gas_mw"),
        "GAS":     pick(df, "commodities_gas_ttf_m1"),
        "CO2":     pick(df, "commodities_co2_eua_dec"),
        "T2M":     pick(df, "era5_t2m_mean"),
        "AUTO":    pick(df, "calc_autoconsumo_mw"),
        "SOLREE":  pick(df, "esios_gen_ree_gsolar_mw"),
        "SOLTER":  pick(df, "esios_gen_ree_gsolter_mw"),
        "WREE":    pick(df, "esios_gen_ree_gwind_mw"),
        "HREE":    pick(df, "esios_gen_ree_ghidro_mw"),
        "PG":      pick(df, "entsoe_pumping_gen_mw"),
        "PC":      pick(df, "entsoe_pumping_cons_mw"),
        "FC_DEM":  pick(df, "forecast_demanda_mercado_prev_mw"),
        "FC_EOL":  pick(df, "forecast_gen_wind_prev_mw"),
        "FC_FV":   pick(df, "forecast_gen_solar_pv_prev_mw"),
    }
    assert C["P"] is not None, "Falta spot_price en el bronce: sin objetivo no hay análisis."
    return C


def drivers_de(df: pd.DataFrame, C: dict) -> dict:
    """Los once drivers del precio, cada uno con su etiqueta de disponibilidad."""
    bruto = {
        "demanda prevista": (C["FC_DEM"], "SIN FUGA"),
        "eólica prevista":  (C["FC_EOL"], "SIN FUGA"),
        "solar prevista":   (C["FC_FV"],  "SIN FUGA"),
        "gas":              (C["GAS"],    "DESFASE D-2"),
        "CO2":              (C["CO2"],    "DESFASE D-2"),
        "demanda real":     (C["DEM"],    "CON FUGA"),
        "eólica real":      (C["EOL"],    "CON FUGA"),
        "solar FV real":    (C["FV"],     "CON FUGA"),
        "hidráulica":       (C["HID"],    "CON FUGA"),
        "ciclo combinado":  (C["CCGT"],   "CON FUGA"),
        "temperatura":      (C["T2M"],    "CON FUGA"),
    }
    return {k: v for k, v in bruto.items() if v[0]}


# ---------------------------------------------------------------------------
# La frontera de disponibilidad: el contrato entre los dos cuadernos
# ---------------------------------------------------------------------------
#
# Se declara ANTES de mirar ninguna correlación, para que ningún resultado bonito la
# modifique a posteriori. El cuaderno exploratorio la usa para COLOREAR sus resultados; el
# de ingeniería la usa para FILTRAR columnas. Es la única regla que gobierna la admisión de
# una variable al modelo.

FRONTERA = pd.DataFrame([
    ("spot_price (España)",      "TARGET",      "Casación a las 12:00 de D, publicación ~12:45"),
    ("spot_price (11 zonas)",    "CON FUGA",    "Casación SDAC simultánea: el de D+1 no existe al predecir"),
    ("forecast (REE)",           "SIN FUGA",    "Publicada antes de las 11:00 de D-1 (Circular 4/2019 CNMC)"),
    ("entsoe_forecast_da",       "SIN FUGA*",   "Se reescribe hacia atrás: ver bloque 2.6 del EDA"),
    ("ecmwf_forecast_agg",       "SIN FUGA*",   "Sin histórico: ventana móvil de días. Ver bloque 5.2 del EDA"),
    ("esios_capacity_available", "CONDICIONAL", "D-01: el valor guardado conoce el propio día"),
    ("era5_weather_agg",         "CON FUGA",    "Reanálisis: el tiempo que ocurrió, no el previsto"),
    ("generation / load_inter",  "CON FUGA",    "Real: a las 12:00 sólo han pasado las horas 00-11"),
    ("esios_pdbc_gen",           "CON FUGA",    "Misma casación que el precio: circular"),
    ("commodities / trayport",   "DESFASE D-2", "Cierran ~17:30, tras el cierre eléctrico"),
], columns=["tabla", "veredicto", "motivo"])

#: Prefijo de columna -> veredicto de disponibilidad. El orden importa: se evalúa en secuencia.
PREFIJOS_FUGA = [
    ("forecast_", "SIN FUGA"), ("ecmwf_", "SIN FUGA"), ("cap_inst_", "SIN FUGA"),
    ("commodities_", "DESFASE D-2"), ("tp_", "DESFASE D-2"),
    ("era5_", "CON FUGA"), ("entsoe_", "CON FUGA"), ("esios_gen_", "CON FUGA"),
    ("load_inter_", "CON FUGA"), ("calc_", "CON FUGA"), ("spot_", "CON FUGA"),
    ("cap_disp_", "CONDICIONAL"),
]


def fuga_de(col: str, objetivo: str) -> str:
    """Veredicto de disponibilidad de una columna del bronce a las 12:00 del día D."""
    if col == objetivo:
        return "TARGET"
    for pre, v in PREFIJOS_FUGA:
        if col.startswith(pre):
            return v
    return "CONDICIONAL"


ADMISIBLES = ("SIN FUGA", "DESFASE D-2")


# ---------------------------------------------------------------------------
# Clasificación de horas: el objetivo categórico de la batería
# ---------------------------------------------------------------------------

def clasificar_horas(df: pd.DataFrame, objetivo: str, k: int = K_HORAS) -> pd.DataFrame:
    """Etiqueta cada hora como barata / intermedia / cara **por su rango dentro del día**.

    El rango se calcula dentro de la jornada y no sobre el histórico completo porque la
    batería no almacena de un mes para otro: lo relevante es la posición relativa del precio
    en su propio día, no su nivel absoluto.

    Devuelve el subconjunto de columnas de calendario más `rango`, `clase` y `es_cara`.
    """
    cl = df[["dia", "hora", "anio", objetivo]].dropna(subset=[objetivo]).copy()
    cl["rango"] = cl.groupby("dia")[objetivo].rank(method="first")
    cl["n"] = cl.groupby("dia")[objetivo].transform("size")
    cl["clase"] = "intermedia"
    cl.loc[cl["rango"] <= k, "clase"] = "barata"
    cl.loc[cl["rango"] > cl["n"] - k, "clase"] = "cara"
    cl["es_cara"] = (cl["clase"] == "cara").astype(int)
    return cl


def corr_segura(a: pd.Series, b: pd.Series) -> float:
    """Correlación de Pearson que devuelve NaN —sin avisar— si alguna serie no tiene recorrido.

    Al agrupar por hora o por año aparecen subconjuntos degenerados que son legítimos y no
    errores: la previsión solar vale cero en todas las horas nocturnas, de modo que su
    desviación típica es nula y la correlación no está definida. `pandas` la calcula igualmente
    y deja escapar un RuntimeWarning de división por cero que ensucia la salida sin aportar
    nada, porque el NaN resultante ya está contemplado en las figuras.
    """
    d = pd.DataFrame({"a": a, "b": b}).dropna()
    if len(d) < 3 or d["a"].std() == 0 or d["b"].std() == 0:
        return np.nan
    return float(d["a"].corr(d["b"]))


def v_cramer(v: pd.Series, target: pd.Series, q: int = 5) -> float:
    """V de Cramér entre dos series, discretizando por cuantiles las que sean continuas.

    Equivale a `FuncionesMineria.Vcramer` del máster, con dos diferencias que aquí importan:
    admite el número de tramos como parámetro y devuelve NaN —en lugar de fallar— cuando la
    variable no tiene recorrido suficiente, que es el caso de varias columnas del bronce.
    """
    from scipy.stats import chi2_contingency

    d = pd.DataFrame({"v": v, "t": target}).dropna()
    if len(d) < 200:
        return np.nan
    cv = sorted(set(d["v"].quantile(np.linspace(0, 1, q + 1))))
    ct = sorted(set(d["t"].quantile(np.linspace(0, 1, q + 1))))
    if len(cv) < 3 or len(ct) < 3:
        return np.nan
    tb = pd.crosstab(pd.cut(d["v"], bins=cv, include_lowest=True),
                     pd.cut(d["t"], bins=ct, include_lowest=True))
    return float(np.sqrt(chi2_contingency(tb)[0] / (tb.sum().sum() * (min(tb.shape) - 1))))


# ---------------------------------------------------------------------------
# Registro
# ---------------------------------------------------------------------------

class Registro:
    """Acumula hallazgos (EDA) o decisiones (ingeniería) y los publica como tabla.

    Sustituye a la lista global del cuaderno anterior. Cada anotación deja constancia en el
    momento en que se produce, de modo que la tabla final no se escribe a mano al cerrar:
    se deriva de lo que el cuaderno ha demostrado.
    """

    def __init__(self, titulo: str = "REGISTRO"):
        self.titulo = titulo
        self.filas: list[dict] = []

    def anotar(self, codigo: str, titulo: str, estado: str, consecuencia: str):
        self.filas.append({"código": codigo, "hallazgo": titulo,
                           "estado": estado, "consecuencia": consecuencia})
        print(f"  [{codigo}] {estado}: {titulo}")

    def tabla(self) -> pd.DataFrame:
        if not self.filas:
            return pd.DataFrame(columns=["código", "hallazgo", "estado", "consecuencia"])
        return pd.DataFrame(self.filas).set_index("código")

    def __len__(self):
        return len(self.filas)
