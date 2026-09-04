/** Etiqueta secuencial usada por el mercado: la primera hora del día es H1. */
export function marketHourLabel(index: number) {
  return Number.isInteger(index) && index >= 0 ? `H${index + 1}` : '';
}
