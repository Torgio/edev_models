/**
 * Estudio anual de arbitraje BESS, 1 MW de potencia, tres duraciones.
 * Origen: data_temp/bess_resumen.csv. Son cifras de un año completo bajo los
 * supuestos del simulador, NO extrapolables a un día concreto: se muestran como
 * contexto del panel diario y deben ir siempre etiquetadas como estudio anual.
 */
export type DurationStudy = {
  hours: number; powerMw: number;
  model: number; naive: number; oracle: number;
  capture: number; captureNaive: number;
};

export const BESS_DURATION_STUDY: DurationStudy[] = [
  { hours: 1, powerMw: 1, model: 28807, naive: 25961, oracle: 31433, capture: 91.6, captureNaive: 82.6 },
  { hours: 2, powerMw: 1, model: 55757, naive: 50537, oracle: 59383, capture: 93.9, captureNaive: 85.1 },
  { hours: 4, powerMw: 1, model: 100809, naive: 92035, oracle: 105601, capture: 95.5, captureNaive: 87.2 },
];

/** Lo que cuesta equivocarse en un año: techo del oráculo menos ingreso del modelo. */
export const durationErrorCost = (row: DurationStudy) => row.oracle - row.model;

export const euro = (value: number | null | undefined) =>
  typeof value === 'number' && Number.isFinite(value)
      ? `${Math.round(value).toLocaleString('es-ES', { useGrouping: 'always' })} €`
    : '—';