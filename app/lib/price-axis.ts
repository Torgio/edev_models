const PRICE_AXIS_PADDING = 8;
const PRICE_AXIS_STEP = 10;

export function priceAxisLower(dataMin: number) {
  if (!Number.isFinite(dataMin)) return 0;
  return Math.floor((dataMin - PRICE_AXIS_PADDING) / PRICE_AXIS_STEP) * PRICE_AXIS_STEP;
}

export function priceAxisUpper(dataMax: number) {
  if (!Number.isFinite(dataMax)) return PRICE_AXIS_STEP;
  return Math.ceil((dataMax + PRICE_AXIS_PADDING) / PRICE_AXIS_STEP) * PRICE_AXIS_STEP;
}

export function priceAxisTick(value: number) {
  return Math.round(value).toLocaleString('es-ES');
}
