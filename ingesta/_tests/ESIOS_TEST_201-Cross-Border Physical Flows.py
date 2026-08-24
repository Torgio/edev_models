"""
TEST — Comparativa flujos ENTSO-E 30 julio: bug actual (filtrar antes de
promediar) vs. fix correcto (promediar antes de filtrar).
"""

import sys
import json
from pathlib import Path
from datetime import date, datetime, timedelta, timezone

import pandas as pd
from entsoe import EntsoePandasClient

sys.path.append(str(Path(__file__).parent.parent))
from config import load_config

TARGET = date(2026, 7, 30)
COUNTRY = "ES"
COUNTRY_FR = "FR"
COUNTRY_PT = "PT"


def expected_hours_utc(target: date) -> set:
    from zoneinfo import ZoneInfo
    TZ_SPAIN = ZoneInfo("Europe/Madrid")
    start_spain = datetime(target.year, target.month, target.day, 0, 0, 0, tzinfo=TZ_SPAIN)
    end_spain   = datetime(target.year, target.month, target.day, 23, 0, 0, tzinfo=TZ_SPAIN)
    start_utc   = start_spain.astimezone(timezone.utc)
    end_utc     = end_spain.astimezone(timezone.utc)
    hours = set()
    current = start_utc
    while current <= end_utc:
        hours.add(current)
        current += timedelta(hours=1)
    return hours


def to_ts_range(target: date):
    start = pd.Timestamp(str(target - timedelta(days=1)), tz="Europe/Madrid")
    end   = pd.Timestamp(str(target + timedelta(days=1)), tz="Europe/Madrid")
    return start, end


def filter_to_target_day(df, target: date):
    expected = expected_hours_utc(target)
    if df.index.tzinfo is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert("UTC")
    return df[df.index.isin(expected)]


def resample_hourly(series: pd.Series) -> pd.Series:
    if series.empty:
        return series
    return series.resample("h").mean()


_, db_config = load_config()
creds = json.load(open(Path(__file__).parent.parent / "credentials.json"))
client = EntsoePandasClient(api_key=creds["entsoe_token"])
ts_start, ts_end = to_ts_range(TARGET)
df_flow = client.query_crossborder_flows(COUNTRY_FR, COUNTRY, start=ts_start, end=ts_end)

print(f"Total muestras crudas devueltas: {len(df_flow)}")
print(f"Primeras 8 muestras crudas (FR->ES):")
print(df_flow.head(8))

# BUG ACTUAL: filtrar primero, luego "promediar" (solo queda 1 muestra/hora)
filtrado_bug = filter_to_target_day(df_flow.copy(), TARGET)
resultado_bug = resample_hourly(filtrado_bug)

# FIX: promediar primero, luego filtrar
resampled_fix = resample_hourly(df_flow.copy())
resultado_fix = filter_to_target_day(resampled_fix.to_frame(name="flow"), TARGET)["flow"]

print(f"\n{'Hora UTC':<22} {'BUG (actual)':>15} {'FIX (correcto)':>15} {'Diferencia':>12}")
comparativa = pd.DataFrame({"bug": resultado_bug, "fix": resultado_fix}).dropna()
comparativa["diff"] = comparativa["fix"] - comparativa["bug"]
for idx, row in comparativa.iterrows():
    print(f"{str(idx):<22} {row['bug']:>15.2f} {row['fix']:>15.2f} {row['diff']:>12.2f}")

print(f"\nDiferencia media absoluta: {comparativa['diff'].abs().mean():.2f} MW")
print(f"Diferencia maxima absoluta: {comparativa['diff'].abs().max():.2f} MW")