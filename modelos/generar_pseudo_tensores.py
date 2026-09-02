"""
Fase B del generador de pseudo-tensores: construye tensores "tipo ECMWF" para el
período 2020-01-01 a 2024-03-31 (donde no existe ECMWF real), inyectando sobre el
ERA5 real el patrón de error medido en la Fase A (moldes_error_ecmwf/).

Generaliza pseudo_prevision() de pseudo_prevision.py: en vez de sumar un número por
variable, se suma un tensor completo (8, 33, 57, 11) — el molde de un día real del
solape, elegido preferentemente del mismo mes (mismo criterio del equipo: "un
frente de invierno se predice peor que un anticiclón de julio").

Salida: un .npy por mes, mismo formato que era5_tensor_YYYY-MM.npy
(n_días×8, 33, 57, 11), nombrado pseudo_ecmwf_tensor_YYYY-MM.npy — mantiene el
grano de 3h de ERA5, no se inventa resolución horaria que la fuente no tiene.

REPRODUCIBILIDAD: semilla fija (SEMILLA) — dos corridas dan el mismo resultado.

Requiere: moldes_error_ecmwf/ (salida de la Fase A) y los .npy reales de ERA5 en
ERA5_BASE_DIR. Correr en el VPS.
"""

import calendar
import json
import os

import numpy as np

SEMILLA = 42
INICIO = (2020, 1)
FIN = (2024, 3)  # inclusive

ERA5_BASE_DIR = os.path.expanduser("~") + "/scripts/ingesta/tensors/era5"
MOLDES_DIR = "moldes_error_ecmwf"
SALIDA_DIR = "pseudo_ecmwf_tensors"

TENSOR_VAR_ORDER = ["t2m", "d2m", "u10", "v10", "u100", "v100", "wind_gust10",
                    "ssrd", "tcc", "tp", "msl"]
_IDX = {v: i for i, v in enumerate(TENSOR_VAR_ORDER)}

# Recortes físicos tras inyectar el error — ver RECORTES de pseudo_prevision.py.
# Distinto del original: acá u10/v10/u100/v100 son COMPONENTES con signo (no
# magnitudes), no se recortan. wind_gust10 sí (es magnitud, siempre >=0).
RECORTES_TENSOR = {
    "wind_gust10": (0.0, None),
    "ssrd": (0.0, None),
    "tcc": (0.0, 1.0),
    "tp": (0.0, None),
}


def _meses(inicio, fin):
    y, m = inicio
    while (y, m) <= fin:
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


def _cargar_moldes(moldes_dir):
    with open(os.path.join(moldes_dir, "moldes_error_index.json")) as fh:
        indice = json.load(fh)
    por_mes = {}
    for d in indice:
        por_mes.setdefault(d["mes"], []).append(d["archivo"])
    return indice, por_mes


def _aplicar_recortes(pseudo):
    """pseudo: (n, 33, 57, 11). Recorta canal por canal según RECORTES_TENSOR."""
    for nombre, (lo, hi) in RECORTES_TENSOR.items():
        i = _IDX[nombre]
        pseudo[..., i] = np.clip(pseudo[..., i], lo, hi)
    return pseudo


def generar_mes(anio, mes, moldes_dir, por_mes_disponibles, rng,
                era5_base_dir=None, salida_dir=None, verbose=True):
    """Genera el pseudo-tensor de un mes completo. Devuelve (array, informe)."""
    era5_base_dir = era5_base_dir or ERA5_BASE_DIR
    salida_dir = salida_dir or SALIDA_DIR

    ruta_era5 = os.path.join(era5_base_dir, f"era5_tensor_{anio}-{mes:02d}.npy")
    era5 = np.load(ruta_era5)  # (n_dias*8, 33, 57, 11)
    n_dias = era5.shape[0] // 8
    assert era5.shape[0] == n_dias * 8, (
        f"{ruta_era5}: shape {era5.shape} no es múltiplo de 8 -- revisar granularidad"
    )
    dias_calendario = calendar.monthrange(anio, mes)[1]
    assert n_dias == dias_calendario, (
        f"{ruta_era5}: {n_dias} días en el archivo, pero el calendario dice "
        f"{dias_calendario} días para {anio}-{mes:02d}"
    )

    disponibles_global = [a for lista in por_mes_disponibles.values() for a in lista]
    candidatos = por_mes_disponibles.get(mes) or disponibles_global
    usados_mismo_mes = bool(por_mes_disponibles.get(mes))

    pseudo = np.empty_like(era5)
    moldes_usados = []
    for dia in range(n_dias):
        archivo_molde = candidatos[rng.integers(len(candidatos))]
        molde = np.load(os.path.join(moldes_dir, archivo_molde))  # (8, 33, 57, 11)
        pseudo[dia * 8:(dia + 1) * 8] = era5[dia * 8:(dia + 1) * 8] + molde
        moldes_usados.append(archivo_molde)

    pseudo = _aplicar_recortes(pseudo)

    os.makedirs(salida_dir, exist_ok=True)
    nombre_salida = f"pseudo_ecmwf_tensor_{anio}-{mes:02d}.npy"
    np.save(os.path.join(salida_dir, nombre_salida), pseudo)

    informe = {
        "anio": anio, "mes": mes, "dias": n_dias,
        "molde_del_mismo_mes": usados_mismo_mes,
        "moldes_usados": moldes_usados,
        "archivo_salida": nombre_salida,
    }
    if verbose:
        print(f"  {anio}-{mes:02d}: {n_dias} días · molde "
              f"{'del mismo mes' if usados_mismo_mes else 'de FALLBACK (otro mes)'} "
              f"· guardado en {nombre_salida}")
    return pseudo, informe


def main():
    rng = np.random.default_rng(SEMILLA)
    _, por_mes = _cargar_moldes(MOLDES_DIR)

    print("Moldes disponibles por mes:")
    for m in sorted(por_mes):
        print(f"  mes {m:02d}: {len(por_mes[m])} moldes")
    faltantes = [m for m in range(1, 13) if m not in por_mes]
    if faltantes:
        print(f"\nMeses SIN molde propio (usarán fallback de otro mes): {faltantes}")

    informes = []
    for anio, mes in _meses(INICIO, FIN):
        _, informe = generar_mes(anio, mes, MOLDES_DIR, por_mes, rng)
        informes.append(informe)

    os.makedirs(SALIDA_DIR, exist_ok=True)
    with open(os.path.join(SALIDA_DIR, "pseudo_tensores_informe.json"), "w") as fh:
        json.dump(informes, fh, indent=1)

    total_meses = len(informes)
    con_molde_propio = sum(1 for i in informes if i["molde_del_mismo_mes"])
    print(f"\n{total_meses} meses generados · {con_molde_propio} con molde del mismo mes, "
          f"{total_meses - con_molde_propio} con fallback")


if __name__ == "__main__":
    main()
