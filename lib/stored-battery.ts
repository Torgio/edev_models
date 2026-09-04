import { numeric } from './stored-evaluations.ts';

type PlanValue = { ingreso_eur: number | null; soc_mwh: number | null };

/** Aggregate only a complete stored plan. Missing rows never become zero. */
export function storedPlanSummary(rows: PlanValue[]) {
  const incomeComplete = rows.length > 0 && rows.every(row => numeric(row.ingreso_eur));
  const socComplete = rows.length > 0 && rows.every(row => numeric(row.soc_mwh));
  return {
    income: incomeComplete ? rows.reduce((total, row) => total + row.ingreso_eur!, 0) : null,
    chargeCost: incomeComplete ? rows.filter(row => row.ingreso_eur! < 0).reduce((total, row) => total + row.ingreso_eur!, 0) : null,
    dischargeRevenue: incomeComplete ? rows.filter(row => row.ingreso_eur! > 0).reduce((total, row) => total + row.ingreso_eur!, 0) : null,
    maxSoc: socComplete ? Math.max(...rows.map(row => row.soc_mwh!)) : null,
    observations: rows.length,
  };
}

export function batteryModels(plan: Array<{ model: string }>, results: Array<{ model: string }>) {
  return [...new Set([...plan.map(row => row.model), ...results.map(row => row.model)])].sort();
}

/**
 * Open BESS on an operational plan, not on the first alphabetic evaluation.
 * A deliberate user choice is preserved even when that model only has a result.
 */
export function preferredBatteryModel(
  plan: Array<{ model: string }>,
  results: Array<{ model: string }>,
  chosenModel = '',
) {
  const models = batteryModels(plan, results);
  if (chosenModel && models.includes(chosenModel)) return chosenModel;

  const planModels = batteryModels(plan, []);
  return (planModels.includes('ensemble11') ? 'ensemble11' : undefined)
    ?? (planModels.includes('ensemble') ? 'ensemble' : undefined)
    ?? planModels[0]
    ?? (models.includes('ensemble11') ? 'ensemble11' : undefined)
    ?? (models.includes('ensemble') ? 'ensemble' : undefined)
    ?? models[0]
    ?? '';
}
