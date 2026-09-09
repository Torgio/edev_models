/** Etiqueta secuencial usada por el mercado: la primera hora del día es H1. */
export function marketHourLabel(index: number) {
  return Number.isInteger(index) && index >= 0 ? `H${index + 1}` : '';
}

/** Une la hora secuencial del mercado con su hora civil sin perder días de 23/25 horas. */
export function marketHourClockLabel(index: number, clock: unknown) {
  const market = marketHourLabel(index);
  if (!market) return '';
  return typeof clock === 'string' && /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(clock)
    ? `${market} · ${clock}`
    : market;
}
