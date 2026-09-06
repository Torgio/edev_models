type PriceHour = { datetime: string; actual: number | null; predictions: Record<string, number | null> };
const madridDate = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Europe/Madrid', year: 'numeric', month: '2-digit', day: '2-digit',
});

// Enumerate actual hourly instants: a Madrid calendar day can have 23, 24 or 25.
export function dailyHourSlots(day: string): Set<number> {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return new Set();
  const midnight = Date.parse(`${day}T00:00:00Z`);
  if (!Number.isFinite(midnight)) return new Set();
  const slots = new Set<number>();
  for (let hour = -3; hour <= 27; hour++) {
    const instant = midnight + hour * 3_600_000;
    const parts = madridDate.formatToParts(instant);
    const part = (type: string) => parts.find(p => p.type === type)?.value;
    if (`${part('year')}-${part('month')}-${part('day')}` === day) slots.add(instant);
  }
  return slots;
}

export function dailyPrice(hours: PriceHour[], day: string, model = 'ensemble') {
  const slots = dailyHourSlots(day);
  const seen = new Set<number>();
  const predicted: number[] = [];
  const actual: number[] = [];
  const pairedPredicted: number[] = [];
  const pairedActual: number[] = [];
  for (const point of hours) {
    const instant = Date.parse(point.datetime);
    if (!slots.has(instant) || seen.has(instant)) continue;
    seen.add(instant);
    const prediction = point.predictions[model];
    const real = point.actual;
    const hasPrediction = typeof prediction === 'number' && Number.isFinite(prediction);
    const hasActual = typeof real === 'number' && Number.isFinite(real);
    if (hasPrediction) predicted.push(prediction);
    if (hasActual) actual.push(real);
    if (hasPrediction && hasActual) {
      pairedPredicted.push(prediction);
      pairedActual.push(real);
    }
  }
  const mean = (values: number[]) => values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
  const pairedPrediction = mean(pairedPredicted);
  const pairedReal = mean(pairedActual);
  return {
    predicted: mean(predicted), actual: mean(actual),
    predictedHours: predicted.length, actualHours: actual.length, expectedHours: slots.size,
    pairedHours: pairedPredicted.length, pairedPrediction, pairedReal,
    difference: pairedPrediction !== null && pairedReal !== null ? pairedPrediction - pairedReal : null,
  };
}
