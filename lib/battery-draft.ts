export type BatteryDraft = {
  powerKw: number;
  durationH: number;
  capexEurMwh: number;
  efficiencyPct: number;
  cycles: number;
  socMinPct: number;
  socMaxPct: number;
  chargeMaxPct: number;
  dischargeMaxPct: number;
  minimumPowerPct: number;
};

export const DEFAULT_BATTERY: BatteryDraft = {
  powerKw: 100, durationH: 4, capexEurMwh: 200000, efficiencyPct: 90,
  cycles: 6000, socMinPct: 5, socMaxPct: 95,
  chargeMaxPct: 100, dischargeMaxPct: 100, minimumPowerPct: 0,
};

export function batterySummary(value: BatteryDraft) {
  const capacityMwh = value.powerKw / 1000 * value.durationH;
  const usableMwh = capacityMwh * (value.socMaxPct - value.socMinPct) / 100;
  const totalCostEur = capacityMwh * value.capexEurMwh;
  const cycleCostEurMwh = usableMwh > 0 && value.cycles > 0 ? totalCostEur / value.cycles / usableMwh : null;
  return { capacityMwh, usableMwh, totalCostEur, cycleCostEurMwh };
}

export function batteryIssues(value: BatteryDraft) {
  const issues: string[] = [];
  if (!Number.isFinite(value.powerKw) || value.powerKw < 5 || value.powerKw > 100000) issues.push('La potencia debe estar entre 5 y 100.000 kW.');
  if (![1, 2, 3, 4, 6, 8].includes(value.durationH)) issues.push('La duración seleccionada no es válida.');
  if (!Number.isFinite(value.capexEurMwh) || value.capexEurMwh < 20000 || value.capexEurMwh > 2000000) issues.push('El precio debe estar entre 20.000 y 2.000.000 €/MWh.');
  if (!Number.isFinite(value.efficiencyPct) || value.efficiencyPct < 70 || value.efficiencyPct > 99) issues.push('El rendimiento debe estar entre 70 % y 99 %.');
  if (!Number.isInteger(value.cycles) || value.cycles < 1000 || value.cycles > 15000) issues.push('Los ciclos de vida deben estar entre 1.000 y 15.000.');
  if (value.socMinPct < 0 || value.socMinPct > 30 || value.socMaxPct < 70 || value.socMaxPct > 100 || value.socMinPct >= value.socMaxPct) issues.push('La ventana de carga útil no es válida.');
  if (value.chargeMaxPct < 10 || value.chargeMaxPct > 100 || value.dischargeMaxPct < 10 || value.dischargeMaxPct > 100) issues.push('Los límites de carga y descarga deben estar entre 10 % y 100 %.');
  if (value.minimumPowerPct < 0 || value.minimumPowerPct > 50) issues.push('El mínimo técnico debe estar entre 0 % y 50 %.');
  return issues;
}
