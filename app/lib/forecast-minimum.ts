type ForecastHour = { hour: string; predictions: Record<string, number | null> };

// This is a descriptive minimum, not a battery charging recommendation.
export function forecastMinimum(hours: ForecastHour[], model = 'ensemble') {
  const models = new Set<string>();
  let minimum: { index: number; hour: string; value: number } | null = null;
  let observations = 0;
  let ties = 0;
  for (const [index, point] of hours.entries()) {
    for (const [key, value] of Object.entries(point.predictions)) {
      if (typeof value === 'number' && Number.isFinite(value)) models.add(key);
    }
    const value = point.predictions[model];
    if (typeof value !== 'number' || !Number.isFinite(value)) continue;
    observations++;
    if (!minimum || value < minimum.value) {
      minimum = { index, hour: point.hour, value };
      ties = 1;
    } else if (value === minimum.value) ties++;
  }
  return { model, modelCount: models.size, minimum, observations, totalHours: hours.length, ties };
}
