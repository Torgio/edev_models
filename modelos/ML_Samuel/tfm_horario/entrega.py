"""Construccion del entregable que pide Prod.txt.

Cada modelo produce tres ficheros en `entregables/<modelo_id>/`:

    modelo.joblib          el modelo entrenado
    pred_val_2025.csv      8760 filas, las horas UTC de 2025
    metadata.json          la plantilla del mensaje 2

Este modulo NO calcula metricas. Es deliberado: Prod.txt dice que el MAE y la
captura de arbitraje los calcula una sola persona con un unico script para los 12
modelos, y que precisamente por calcularlos cada uno a su manera salian 86,5 % y
81 % para el mismo modelo. Lo unico que se hace aqui es un control de sanidad
(que no haya NaN ni una prediccion constante), que no es una metrica de calidad y
no viaja en el PR.
"""

from __future__ import annotations

import json
import logging
import platform
from datetime import date
from importlib import metadata as _meta
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from . import ajustes as config, data

log = logging.getLogger(__name__)


def _version(paquete: str) -> str:
    try:
        return f"{paquete}=={_meta.version(paquete)}"
    except Exception:                                   # noqa: BLE001
        return paquete


def _serie_a_rejilla_2025(pred: pd.Series) -> pd.Series:
    """Lleva la prediccion a las 8760 horas UTC exactas de 2025.

    Las predicciones vienen con indice sin tz (se le quita en el tratamiento para
    que statsmodels acepte la frecuencia), asi que primero se reetiqueta como UTC
    -- que es lo que siempre fue -- y despues se reindexa contra la rejilla oficial.

    Si faltan horas se interpolan y se avisa: el fichero DEBE tener 8760 filas, y es
    preferible una hora interpolada y registrada en el log que un CSV que el
    evaluador rechaza.
    """
    idx = pd.DatetimeIndex(pred.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    else:
        idx = idx.tz_convert("UTC")
    pred = pd.Series(np.asarray(pred, dtype=float), index=idx).sort_index()
    pred = pred[~pred.index.duplicated(keep="first")]

    rejilla = data.rejilla_val_2025()
    fuera = len(pred.index.difference(rejilla))
    if fuera:
        log.info("se descartan %d horas predichas fuera de 2025", fuera)

    alineada = pred.reindex(rejilla)
    faltan = int(alineada.isna().sum())
    if faltan:
        log.warning("faltaban %d horas de 2025 en la prediccion: se interpolan", faltan)
        alineada = alineada.interpolate().bfill().ffill()

    if alineada.isna().any():
        raise RuntimeError("no se pudo completar la rejilla de 2025: quedan NaN")
    return alineada


def _control_de_sanidad(pred: pd.Series) -> None:
    """Comprobaciones de que el fichero no esta roto. NO son metricas de calidad."""
    if pred.nunique() <= 1:
        raise RuntimeError("la prediccion es constante: algo ha fallado en el entrenamiento")
    log.info("control interno (no es una metrica, no va al PR): min=%.2f media=%.2f max=%.2f",
             pred.min(), pred.mean(), pred.max())


def id_con_modo(base: str, modo: str) -> str:
    """`ridge_horario` con el modo por defecto; `ridge_horario_spearman` con otro.

    Cada modo produce un modelo distinto y merece su propia entrada en el
    leaderboard: si los cuatro escribieran en la misma carpeta se pisarian, y el
    revisor no podria saber cual se entreno con que seleccion.
    """
    return base if modo == config.MODO_SELECCION else f"{base}_{modo}"


def guardar_entregable(
    modelo_id: str,
    modelo,
    pred: pd.Series,
    features: list[str],
    librerias: list[str],
    entrenado_desde,
    p10: pd.Series | None = None,
    p90: pd.Series | None = None,
) -> Path:
    """Escribe los tres ficheros y devuelve la carpeta del entregable."""
    base = next((b for b in config.MODELOS if modelo_id == b or modelo_id.startswith(f"{b}_")), None)
    if base is None:
        raise ValueError(f"modelo_id {modelo_id!r} no deriva de ninguno de {list(config.MODELOS)}")

    config.preparar_entorno()
    destino = config.ENTREGABLES_DIR / modelo_id
    destino.mkdir(parents=True, exist_ok=True)

    pred = _serie_a_rejilla_2025(pred)
    _control_de_sanidad(pred)

    # --- pred_val_2025.csv ---------------------------------------------------
    # datetime_utc en ISO con Z, exactamente como el ejemplo de Prod.txt.
    # p10/p90 vacios: ninguno de estos cuatro modelos da intervalo de prediccion.
    csv = pd.DataFrame({
        "modelo_id": modelo_id,
        "datetime_utc": pred.index.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "precio_pred": pred.to_numpy().round(2),
        "p10": _serie_a_rejilla_2025(p10).to_numpy().round(2) if p10 is not None else "",
        "p90": _serie_a_rejilla_2025(p90).to_numpy().round(2) if p90 is not None else "",
    })
    if len(csv) != config.HORAS_VAL_2025:
        raise RuntimeError(f"pred_val_2025.csv tendria {len(csv)} filas, deben ser {config.HORAS_VAL_2025}")

    ruta_csv = destino / "pred_val_2025.csv"
    csv.to_csv(ruta_csv, index=False)

    # --- metadata.json -------------------------------------------------------
    metadata = {
        "modelo_id": modelo_id,
        "familia": config.MODELOS[base],
        "autor": config.AUTOR,
        "libreria": ", ".join(librerias),
        "python": ".".join(platform.python_version_tuple()[:2]),
        "entrenado_desde": str(entrenado_desde),
        "entrenado_hasta": "2024-12-31",
        "semilla": config.SEMILLA,
        "features": list(features),
        "features_dudosas": data.features_dudosas(features),
    }
    (destino / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # --- modelo entrenado ----------------------------------------------------
    joblib.dump(modelo, destino / "modelo.joblib")

    log.info("entregable listo en %s (%d filas, %d features, %d dudosas)",
             destino, len(csv), len(metadata["features"]), len(metadata["features_dudosas"]))
    return destino


def librerias_de(*paquetes: str) -> list[str]:
    """`['scikit-learn==1.8.0', ...]` con las versiones realmente instaladas."""
    return [_version(p) for p in paquetes]


def fecha_inicio(y_train: pd.Series) -> date:
    """Primera fecha con dato en train, para `entrenado_desde`."""
    return pd.Timestamp(y_train.index.min()).date()


def verificar_entregables() -> pd.DataFrame:
    """Repasa lo que hay en `entregables/` antes de abrir el PR.

    Comprueba los tres ficheros, el numero de filas y que la primera y ultima hora
    sean las de 2025. Se repasa lo que HAYA en entregables/, incluidas las variantes
    por modo de seleccion (`ridge_horario_spearman`, etc.). Es la ultima red antes
    de subirlo.
    """
    filas = []
    if not config.ENTREGABLES_DIR.exists():
        return pd.DataFrame()
    presentes = sorted(p.name for p in config.ENTREGABLES_DIR.iterdir() if p.is_dir())
    for modelo_id in presentes or list(config.MODELOS):
        carpeta = config.ENTREGABLES_DIR / modelo_id
        ruta_csv = carpeta / "pred_val_2025.csv"
        fila = {
            "modelo_id": modelo_id,
            "modelo.joblib": (carpeta / "modelo.joblib").exists(),
            "metadata.json": (carpeta / "metadata.json").exists(),
            "pred_val_2025.csv": ruta_csv.exists(),
            "filas": None,
            "primera": None,
            "ultima": None,
            "ok": False,
        }
        if ruta_csv.exists():
            df = pd.read_csv(ruta_csv)
            fila["filas"] = len(df)
            fila["primera"] = df["datetime_utc"].iloc[0]
            fila["ultima"] = df["datetime_utc"].iloc[-1]
            fila["ok"] = (
                fila["modelo.joblib"] and fila["metadata.json"]
                and len(df) == config.HORAS_VAL_2025
                and fila["primera"] == "2025-01-01T00:00:00Z"
                and fila["ultima"] == "2025-12-31T23:00:00Z"
                and df["precio_pred"].notna().all()
            )
        filas.append(fila)
    return pd.DataFrame(filas).set_index("modelo_id")
