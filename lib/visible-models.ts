/** Mantiene el modelo que gobierna los KPI dentro de las series del gráfico. */
export function modelsToPlot(available: string[], explicit: string[], reference: string, initialLimit = 3) {
  const availableSet = new Set(available);
  const chosen = [...new Set(explicit)].filter(model => availableSet.has(model));
  if (chosen.length) {
    return reference && availableSet.has(reference) && !chosen.includes(reference)
      ? [reference, ...chosen]
      : chosen;
  }
  return [reference, ...available.filter(model => model !== reference)]
    .filter((model, index, values) => model && availableSet.has(model) && values.indexOf(model) === index)
    .slice(0, initialLimit);
}
