type SignalHour = {
  actual: number | null;
  predictions: Record<string, number | null | undefined>;
};

export function forecastRamp(hours: SignalHour[], model: string) {
  let best: { from: number; to: number; increase: number; value: number } | null = null;
  for (let index = 1; index < hours.length; index++) {
    const previous = hours[index - 1].predictions[model];
    const current = hours[index].predictions[model];
    if (!Number.isFinite(previous) || !Number.isFinite(current)) continue;
    const increase = current! - previous!;
    if (increase > 0 && (!best || increase > best.increase)) {
      best = { from: index - 1, to: index, increase, value: current! };
    }
  }
  return best;
}

export function negativePriceHours(hours: SignalHour[], model: string) {
  const entries = hours.flatMap((row, index) => {
    const prediction = row.predictions[model];
    const predicted = Number.isFinite(prediction) && prediction! < 0;
    const actual = Number.isFinite(row.actual) && row.actual! < 0;
    return predicted || actual ? [{ index, predicted, actual }] : [];
  });
  return {
    entries,
    predicted: entries.filter(row => row.predicted).length,
    actual: entries.filter(row => row.actual).length,
  };
}
