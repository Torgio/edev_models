import { dailyHourSlots } from './daily-price.ts';

type Hour = { datetime: string; predictions: Record<string, number | null> };
const clock = new Intl.DateTimeFormat('es-ES', { timeZone: 'Europe/Madrid', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });

export function marketInterval(start: number, end: number) {
  const offset = new Intl.DateTimeFormat('es-ES', { timeZone: 'Europe/Madrid', timeZoneName: 'shortOffset' });
  const zone = (time: number) => offset.formatToParts(time).find(part => part.type === 'timeZoneName')?.value;
  return zone(start) === zone(end)
    ? `${clock.format(start)}–${clock.format(end)}`
    : `${clock.format(start)} (${zone(start)})–${clock.format(end)} (${zone(end)})`;
}

/** Compare equal three-hour windows; never bridge missing hours or another day. */
export function marketWindows(hours: Hour[], day: string, model: string) {
  const slots = [...dailyHourSlots(day)];
  const values = new Map<number, number>();
  for (const point of hours) {
    const value = point.predictions[model];
    const instant = Date.parse(point.datetime);
    if (typeof value === 'number' && Number.isFinite(value) && !values.has(instant)) values.set(instant, value);
  }
  type Window = { label: string; average: number };
  let cheapest: Window | null = null;
  let priciest: Window | null = null;
  for (let index = 0; index <= slots.length - 3; index++) {
    const window = slots.slice(index, index + 3);
    if (!window.every(instant => values.has(instant))) continue;
    const average = window.reduce((sum, instant) => sum + values.get(instant)!, 0) / 3;
    const result = { label: marketInterval(window[0], window[2] + 3_600_000), average };
    if (!cheapest || average < cheapest.average) cheapest = result;
    if (!priciest || average > priciest.average) priciest = result;
  }
  return { cheapest, priciest, covered: slots.filter(instant => values.has(instant)).length, expected: slots.length };
}

export function operationIntervals(instants: number[]) {
  const sorted = [...new Set(instants)].filter(Number.isFinite).sort((a, b) => a - b);
  const groups: string[] = [];
  for (let i = 0; i < sorted.length;) {
    const start = sorted[i];
    let end = start;
    while (i + 1 < sorted.length && sorted[i + 1] === end + 3_600_000) end = sorted[++i];
    groups.push(marketInterval(start, end + 3_600_000));
    i++;
  }
  return groups.join(', ');
}
