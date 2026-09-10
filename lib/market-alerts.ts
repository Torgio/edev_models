export type MarketAlert = {
  id: 'peak' | 'ramp' | 'negative' | 'difference';
  title: string;
  detail: string;
  from: number;
  to: number;
  value: number;
};

export function preferredPriceSeries(
  hours: Array<{ actual: number | null; predictions: Record<string, number | null | undefined> }>,
  model: string,
) {
  const actualCoverage = hours.filter(row => Number.isFinite(row.actual)).length;
  const useActual = actualCoverage === hours.length && hours.length > 0;
  return {
    kind: useActual ? 'real' as const : 'previsto' as const,
    values: hours.map(row => {
      const value = useActual ? row.actual : row.predictions[model];
      return Number.isFinite(value) ? value as number : null;
    }),
  };
}

export function marketAlerts(current: Array<number | null>, comparison: Array<number | null> = []) {
  const alerts: MarketAlert[] = [];
  const valid = current.flatMap((value, index) => Number.isFinite(value) ? [{ index, value: value! }] : []);
  const peak = valid.sort((a, b) => b.value - a.value)[0];
  if (peak && peak.value >= 150) alerts.push({ id: 'peak', title: 'Pico relevante', detail: `Máximo de ${peak.value.toFixed(2)} €/MWh`, from: peak.index, to: peak.index, value: peak.value });

  let ramp: { from: number; to: number; value: number } | null = null;
  for (let index = 1; index < current.length; index++) {
    if (!Number.isFinite(current[index - 1]) || !Number.isFinite(current[index])) continue;
    const increase = current[index]! - current[index - 1]!;
    if (increase >= 50 && (!ramp || increase > ramp.value)) ramp = { from: index - 1, to: index, value: increase };
  }
  if (ramp) alerts.push({ id: 'ramp', title: 'Rampa rápida', detail: `Subida de ${ramp.value.toFixed(2)} €/MWh`, ...ramp });

  const negative = valid.filter(point => point.value < 0);
  if (negative.length) alerts.push({ id: 'negative', title: 'Precio bajo cero', detail: `${negative.length} hora${negative.length === 1 ? '' : 's'} negativa${negative.length === 1 ? '' : 's'}`, from: negative[0].index, to: negative.at(-1)!.index, value: Math.min(...negative.map(point => point.value)) });

  const gaps = current.flatMap((value, index) => Number.isFinite(value) && Number.isFinite(comparison[index])
    ? [{ index, value: value! - comparison[index]! }] : []);
  const gap = gaps.sort((a, b) => Math.abs(b.value) - Math.abs(a.value))[0];
  if (gap && Math.abs(gap.value) >= 35) alerts.push({ id: 'difference', title: 'Diferencia entre días', detail: `${gap.value >= 0 ? '+' : ''}${gap.value.toFixed(2)} €/MWh frente al día comparado`, from: gap.index, to: gap.index, value: gap.value });
  return alerts;
}

export function pairedDifference(current: Array<number | null>, comparison: Array<number | null>) {
  const pairs = current.flatMap((value, index) => Number.isFinite(value) && Number.isFinite(comparison[index])
    ? [{ current: value!, comparison: comparison[index]! }] : []);
  if (!pairs.length) return { difference: null, hours: 0 };
  return { difference: pairs.reduce((sum, pair) => sum + pair.current - pair.comparison, 0) / pairs.length, hours: pairs.length };
}
