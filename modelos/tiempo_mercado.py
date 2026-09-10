"""Instantes de mercado y persistencia por hora civil, sin borrar el cambio de hora."""
from datetime import timedelta

import pandas as pd

TZ = "Europe/Madrid"


def indice_local(values):
    """Legado sin zona: inferir SOLO si hay ambas horas de octubre; no inventarlas."""
    try:
        idx = pd.DatetimeIndex(values)
    except ValueError:
        # Drivers pueden devolver offsets +01/+02 diferentes en un mismo resultado.
        timestamps = [pd.Timestamp(value) for value in values]
        if not all(ts.tzinfo is not None for ts in timestamps):
            raise
        idx = pd.DatetimeIndex(pd.to_datetime(timestamps, utc=True))
    if idx.tz is None:
        try:
            idx = idx.tz_localize(TZ, ambiguous="infer", nonexistent="NaT")
        except ValueError:
            idx = idx.tz_localize(TZ, ambiguous="NaT", nonexistent="NaT")
        except Exception as exc:
            # pandas/pytz pueden emitir AmbiguousTimeError, no derivado de ValueError.
            if type(exc).__name__ != "AmbiguousTimeError":
                raise
            idx = idx.tz_localize(TZ, ambiguous="NaT", nonexistent="NaT")
    return idx.tz_convert(TZ)


def serie_local(s):
    result = s.copy()
    result.index = indice_local(s.index)
    result = result[~result.index.isna()].sort_index()
    if result.index.has_duplicates:
        raise ValueError("Instantes de mercado duplicados")
    return result


def periodos_dia(day, minutos=60):
    if minutos not in (15, 60):
        raise ValueError("Resolucion admitida: 15 o 60 minutos")
    start = pd.Timestamp(day).normalize().tz_localize(TZ)
    end = pd.Timestamp(start.date() + timedelta(days=1)).tz_localize(TZ)
    return pd.date_range(start, end, freq=f"{minutos}min", inclusive="left")


def dia_completo(index, minutos=60):
    idx = indice_local(index)
    if idx.empty or idx.hasnans or idx.has_duplicates:
        return False
    return idx.sort_values().equals(periodos_dia(idx[0].date(), minutos))


def cierre_prediccion(day):
    """Corte operativo D-1 12:00 Madrid del pipeline actual (no fecha de liquidacion)."""
    return pd.Timestamp(day - timedelta(days=1)).tz_localize(TZ) + pd.Timedelta(hours=12)


def naive_local(real, objetivo, dias=1):
    """Misma hora civil de ayer. Octubre: primera ocurrencia; marzo: hueco, sin imputar.

    Solo el indice auxiliar del baseline se agrupa; los precios reales conservan todos
    sus instantes. Las dos horas de octubre reciben el mismo precio civil de ayer.
    """
    real = serie_local(real)
    idx = indice_local(objetivo)
    civil = real.index.tz_localize(None)
    reference = pd.Series(real.to_numpy(), index=civil)
    reference = reference[~reference.index.duplicated(keep="first")]
    previous = idx.tz_localize(None) - pd.Timedelta(days=dias)
    return pd.Series(reference.reindex(previous).to_numpy(dtype=float), index=idx)
