r"""
TFM Energia UCM - Simulador simple de BESS (bateria de red) sobre el modelo horario (26-ago-2026)

Motivacion (ver docs/notas_memoria_tfm.md notas 23-25): las metricas de "captura de arbitraje" ya
dicen que % del spread teorico del dia se aprovecha, pero no dicen cuantos EUROS reales representa
eso para una bateria de un tamaño concreto. Este script traduce esa señal a euros, con una bateria
de referencia -- el usuario no dio un tamaño concreto todavia, asi que se usan valores tipicos del
mercado español de baterias de red (proyectos BESS recientes en España rondan 1-4h de duracion), y
quedan como constantes faciles de cambiar arriba del archivo.

SUPUESTOS DE LA VERSION 1 (documentados para poder revisarlos despues, no son la version final):
  - Potencia de referencia: 1 MW (se reporta todo "por MW instalado", asi que escalar a cualquier
    tamaño de proyecto real es multiplicar el resultado).
  - Duraciones probadas: 1h, 2h y 4h (categorias tipicas de licitaciones de BESS en España).
  - Eficiencia ida y vuelta (round-trip): 90% (tipico de ion-litio), aplicada integramente sobre
    la energia de DESCARGA (convencion simple: se pierde al devolver la energia a la red, no al
    cargarla).
  - Estrategia: UN solo ciclo de carga/descarga por dia (carga en las D horas de precio mas bajo,
    descarga en las D horas de precio mas alto, D = duracion en horas) -- no se modela ciclado
    multiple intradia, degradacion de la bateria, ni restricciones de red. Es una primera version,
    pensada para comparar estrategias (modelo vs. persistencia vs. oraculo perfecto), no para
    dimensionar un proyecto real.

Tres estrategias de decision (todas se liquidan sobre el PRECIO REAL, solo cambia con que
prediccion se decide CUANDO cargar/descargar):
  A) Modelo:       decide con las predicciones del LightGBM horario ganador.
  B) Persistencia: decide con el precio de la misma hora, ayer (naive D-24h).
  C) Oraculo:      decide con el precio real (limite teorico superior, imposible en la practica).

Uso:
    cd d:\POSGRADO\TFM\edev_models
    .venv\Scripts\python.exe modelos/simulador_bess_horario.py
"""

import sys
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

sys.path.append(str(Path(__file__).parent))
from construir_dataset_horario import construir_dataset_horario, dividir_train_val_test_horario

DIR_ARTEFACTOS = Path(__file__).parent / "artefactos"
DIR_DATA_TEMP = Path(__file__).parent.parent / "data_temp"

# --- Bateria de referencia (ver docstring) -- AJUSTAR AQUI si el equipo define un tamaño real ---
POTENCIA_MW = 1.0
DURACIONES_HORAS = [1, 2, 4]
EFICIENCIA_IDA_VUELTA = 0.90


def _revenue_dia(real: np.ndarray, decision: np.ndarray, duracion: int) -> float:
    """real: 24 precios reales EUR/MWh. decision: 24 precios usados para DECIDIR cuando cargar
    y descargar (predichos o reales, segun la estrategia). Devuelve el ingreso del dia en EUR
    para una bateria de POTENCIA_MW MW y `duracion` horas de autonomia."""
    orden = np.argsort(decision)
    horas_carga = orden[:duracion]
    horas_descarga = orden[-duracion:]
    if set(horas_carga) & set(horas_descarga):
        return np.nan  # dia demasiado plano para separar D horas de carga y D de descarga sin solape
    coste_carga = real[horas_carga].sum() * POTENCIA_MW
    ingreso_descarga = real[horas_descarga].sum() * POTENCIA_MW * EFICIENCIA_IDA_VUELTA
    return ingreso_descarga - coste_carga


def _simular(df: pd.DataFrame, duracion: int) -> pd.DataFrame:
    """df: index = timestamp UTC, columnas 'real', 'pred_modelo', 'pred_naive'."""
    fecha_local = df.index.tz_convert("Europe/Madrid").date
    filas = []
    for fecha, grupo in df.groupby(fecha_local):
        grupo = grupo.sort_index()
        if len(grupo) < 24:
            continue
        real = grupo["real"].values
        filas.append({
            "fecha": fecha,
            "revenue_modelo": _revenue_dia(real, grupo["pred_modelo"].values, duracion),
            "revenue_naive": _revenue_dia(real, grupo["pred_naive"].values, duracion),
            "revenue_oraculo": _revenue_dia(real, real, duracion),
        })
    return pd.DataFrame(filas).set_index("fecha")


def main():
    print("Cargando modelo LightGBM horario ya entrenado...")
    art = joblib.load(DIR_ARTEFACTOS / "lightgbm_horario_final.joblib")
    modelo = art["modelo"]
    feature_cols = art["feature_cols"]
    medianas = art["medianas"]

    print("Construyendo dataset horario (pdbc='lag')...")
    dataset = construir_dataset_horario(pdbc="lag")
    _train, val, _test = dividir_train_val_test_horario(dataset)

    X_val = val[feature_cols].fillna(medianas)
    pred_modelo = modelo.predict(X_val)
    pred_naive = val["precio_propio_lag24h"].fillna(medianas.get("precio_propio_lag24h",
                                                                  val["precio"].median()))

    df = pd.DataFrame({"real": val["precio"].values, "pred_modelo": pred_modelo,
                        "pred_naive": pred_naive.values}, index=val.index)

    print(f"\nBateria de referencia: {POTENCIA_MW} MW, eficiencia ida y vuelta "
          f"{EFICIENCIA_IDA_VUELTA*100:.0f}%. Un ciclo de carga/descarga por dia.")
    print(f"Periodo simulado: validation, {df.index.tz_convert('Europe/Madrid').date.min()} -> "
          f"{df.index.tz_convert('Europe/Madrid').date.max()}\n")

    resumen = []
    for duracion in DURACIONES_HORAS:
        res = _simular(df, duracion)
        n_dias = res["revenue_oraculo"].notna().sum()
        anual = {c: res[c].sum() / n_dias * 365 for c in
                 ["revenue_modelo", "revenue_naive", "revenue_oraculo"]}
        captura_modelo = 100 * anual["revenue_modelo"] / anual["revenue_oraculo"]
        captura_naive = 100 * anual["revenue_naive"] / anual["revenue_oraculo"]
        print(f"=== Bateria de {duracion}h de autonomia ({POTENCIA_MW} MW / "
              f"{POTENCIA_MW*duracion} MWh) -- {n_dias} dias validos ===")
        print(f"  Ingreso anualizado con el MODELO:       {anual['revenue_modelo']:>10,.0f} EUR/año  "
              f"({captura_modelo:.1f}% del oraculo)")
        print(f"  Ingreso anualizado con PERSISTENCIA:    {anual['revenue_naive']:>10,.0f} EUR/año  "
              f"({captura_naive:.1f}% del oraculo)")
        print(f"  Ingreso anualizado con ORACULO perfecto: {anual['revenue_oraculo']:>10,.0f} EUR/año  "
              f"(limite teorico, imposible en la practica)\n")
        resumen.append({"duracion_h": duracion, "potencia_MW": POTENCIA_MW,
                         "revenue_anual_modelo_eur": round(anual["revenue_modelo"]),
                         "revenue_anual_naive_eur": round(anual["revenue_naive"]),
                         "revenue_anual_oraculo_eur": round(anual["revenue_oraculo"]),
                         "captura_modelo_pct": round(captura_modelo, 1),
                         "captura_naive_pct": round(captura_naive, 1)})
        res.to_csv(DIR_DATA_TEMP / f"bess_detalle_diario_{duracion}h.csv")

    df_resumen = pd.DataFrame(resumen)
    print("=== Resumen ===")
    print(df_resumen.to_string(index=False))
    df_resumen.to_csv(DIR_DATA_TEMP / "bess_resumen.csv", index=False)
    print(f"\nGuardado: data_temp/bess_resumen.csv")


if __name__ == "__main__":
    main()
