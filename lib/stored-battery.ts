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
