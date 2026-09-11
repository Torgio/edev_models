import { dailyHourSlots } from './daily-price';
import { marketHourClockLabel } from './market-hour';

type PriceHour = { datetime: string; hour?: string; actual: number | null; predictions: Record<string, number | null> };

export type MarketWindow = { from: number; to: number; label: string; average: number };

const BLOCK = 3;

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

/**
 * Tramos consecutivos de tres horas, ordenados por precio previsto medio.
 * Solo entran las horas que pertenecen al día natural pedido: un día de Madrid
 * puede tener 23, 24 o 25 horas y `hours` puede traer el borde del día vecino.
 * Un tramo necesita las tres horas con previsión para no competir con ventaja.
 */
export function marketWindows(hours: PriceHour[], day: string, model = 'ensemble') {
  const slots = dailyHourSlots(day);
  const series = hours.map((point, index) => {
    const stamp = Date.parse(point.datetime);
    const inDay = Number.isFinite(stamp) ? slots.has(stamp) : true;
    const value = point.predictions[model];
    return { index, clock: point.hour, value: inDay && finite(value) ? value : null };
  });

  const windows: MarketWindow[] = [];
  for (let start = 0; start + BLOCK <= series.length; start += 1) {
    const block = series.slice(start, start + BLOCK);
    if (block.some(hour => hour.value === null)) continue;
    const first = block[0];
    const last = block[block.length - 1];
    const startLabel = marketHourClockLabel(first.index, first.clock);
    const endLabel = marketHourClockLabel(last.index, last.clock);
    windows.push({
      from: first.index,
      to: last.index,
      label: startLabel && endLabel ? `${startLabel}–${endLabel}` : `H${first.index + 1}–H${last.index + 1}`,
      average: block.reduce((total, hour) => total + hour.value!, 0) / BLOCK,
    });
  }

  const byPrice = [...windows].sort((a, b) => a.average - b.average);
  return {
    model,
    blockSize: BLOCK,
    windows,
    cheapest: byPrice[0] ?? null,
    priciest: byPrice[byPrice.length - 1] ?? null,
  };
}