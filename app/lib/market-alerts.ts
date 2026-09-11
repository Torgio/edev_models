import { formatEnergyPrice } from './price-format';

type PriceHour = { actual: number | null; predictions: Record<string, number | null> };

export type PriceSeries = { kind: 'real' | 'forecast'; model: string; values: (number | null)[] };

export type MarketAlert = {
  id: 'peak' | 'negative' | 'ramp' | 'difference';
  title: string;
  detail: string;
  from: number;
  to: number;
};

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

/**
 * Serie que se dibuja como "precio del día". Si el día está liquidado por completo
 * manda el precio real; en cuanto falta una hora se usa la previsión del modelo
 * elegido, nunca una mezcla de ambos: el gráfico rotula la serie con `kind` y un
 * híbrido haría que la etiqueta mintiera.
 */
export function preferredPriceSeries(hours: PriceHour[], model = 'ensemble'): PriceSeries {
  const actual = hours.map(point => (finite(point.actual) ? point.actual : null));
  const closed = hours.length > 0 && actual.every(value => value !== null);
  if (closed) return { kind: 'real', model, values: actual };
  return {
    kind: 'forecast',
    model,
    values: hours.map(point => {
      const value = point.predictions[model];
      return finite(value) ? value : null;
    }),
  };
}

/** Desvío medio hora a hora entre dos días, solo sobre las horas comparables. */
export function pairedDifference(current: (number | null)[], compared: (number | null)[]) {
  const differences: number[] = [];
  const length = Math.min(current.length, compared.length);
  for (let index = 0; index < length; index += 1) {
    const a = current[index];
    const b = compared[index];
    if (finite(a) && finite(b)) differences.push(a - b);
  }
  if (!differences.length) return { difference: null, hours: 0 };
  return {
    difference: differences.reduce((total, value) => total + value, 0) / differences.length,
    hours: differences.length,
  };
}

/**
 * Hechos destacables del día, en el orden en que interesan al operador: dónde está
 * el máximo, si hay horas bajo cero, cuál es la subida más brusca y en qué hora se
 * separa más del día comparado. Cada alerta apunta a un rango de índices del gráfico,
 * así que `from` y `to` son posiciones de la serie, no horas de reloj.
 */
export function marketAlerts(current: (number | null)[], compared: (number | null)[] = []): MarketAlert[] {
  const alerts: MarketAlert[] = [];

  let peak = -1;
  current.forEach((value, index) => {
    if (finite(value) && (peak < 0 || value > current[peak]!)) peak = index;
  });
  if (peak >= 0) alerts.push({
    id: 'peak', from: peak, to: peak,
    title: 'Máximo del día',
    detail: formatEnergyPrice(current[peak]!),
  });

  const negatives = current.flatMap((value, index) => (finite(value) && value < 0 ? [index] : []));
  if (negatives.length) alerts.push({
    id: 'negative', from: negatives[0], to: negatives[negatives.length - 1],
    title: 'Horas bajo cero',
    detail: `${negatives.length} ${negatives.length === 1 ? 'hora' : 'horas'} · mínimo ${formatEnergyPrice(Math.min(...negatives.map(index => current[index]!)))}`,
  });

  let ramp = { from: -1, to: -1, rise: 0 };
  for (let index = 1; index < current.length; index += 1) {
    const previous = current[index - 1];
    const value = current[index];
    if (!finite(previous) || !finite(value)) continue;
    const rise = value - previous;
    if (rise > ramp.rise) ramp = { from: index - 1, to: index, rise };
  }
  if (ramp.from >= 0) alerts.push({
    id: 'ramp', from: ramp.from, to: ramp.to,
    title: 'Mayor subida',
    detail: `${formatEnergyPrice(ramp.rise, { sign: true })} en una hora`,
  });

  let gap = { index: -1, size: 0, value: 0 };
  const length = Math.min(current.length, compared.length);
  for (let index = 0; index < length; index += 1) {
    const a = current[index];
    const b = compared[index];
    if (!finite(a) || !finite(b)) continue;
    const size = Math.abs(a - b);
    if (size > gap.size) gap = { index, size, value: a - b };
  }
  if (gap.index >= 0) alerts.push({
    id: 'difference', from: gap.index, to: gap.index,
    title: 'Mayor desvío',
    detail: `${formatEnergyPrice(gap.value, { sign: true })} frente al día comparado`,
  });

  return alerts;
}