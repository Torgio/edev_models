export type StudyRun = {
  run_id: number; case_id: number; run_at: string; curve_generated_at: string | null;
  curve_matrix_hash: string | null; split_date: string | null; n_scenarios: number;
  npv_p10: number | null; npv_p50: number | null; npv_p90: number | null;
  npv_positive_pct: number | null; savings_vs_no_batt: number | null;
  cycles_per_day: number | null; life_years: number | null; notes: string | null;
  days_historical?: number | null; days_simulated?: number | null;
};
export type StudyAnnual = {
  ano: number; origen: string; dias: number; margen: number | null;
  p10: number | null; p50: number | null; p90: number | null;
  ciclos_dia: number | null;
};
export type StudyInputs = {
  schema_version: 1; captured_at: string;
  case: { case_id: number; code: string; name: string; mode: string };
  period: { date_from: string; date_to: string; calendar: 'nominal_24h' };
  battery: { name: string; code: string; power_mw: number; capacity_mwh: number; duration_h: number; capex_eur_mwh: number };
  consumption: { name: string; code: string; annual_mwh: number } | null;
  generation: { name: string; code: string; technology: string; capacity_mwp: number } | null;
};
export type StudyResult = { run: StudyRun; anual: StudyAnnual[]; inputs?: unknown };
export function studyInputs(data: StudyResult): StudyInputs | null {
  const input = data.inputs as Partial<StudyInputs> | null | undefined;
  const validDate = (value: unknown): value is string => typeof value === 'string'
    && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value))
    && new Date(value).toISOString().slice(0, 10) === value;
  if (!input || input.schema_version !== 1 || input.case?.case_id !== data.run.case_id
    || !input.battery || !validDate(input.period?.date_from) || !validDate(input.period?.date_to)
    || input.period.date_to < input.period.date_from || input.period.calendar !== 'nominal_24h'
    || !('consumption' in input) || !('generation' in input)) return null;
  return input as StudyInputs;
}
export const toKilo = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? value * 1000 : null;
/** Coverage is not an exact date range: annual rows may describe partial years. */
export function studyCoverage(annual: StudyAnnual[]) {
  const years = [...new Set(annual.map(row => row.ano).filter(year => Number.isInteger(year) && year > 0))].sort((a, b) => a - b);
  return years.length ? years.join(' · ') : 'No disponibles';
}
export type StudyDispatch = {
  run_id: number; escenario: number; horas: number; t: string[];
  precio: (number | null)[]; carga: (number | null)[]; descarga: (number | null)[];
  soc: (number | null)[]; consumo: (number | null)[]; generacion: (number | null)[];
  importado: (number | null)[]; exportado: (number | null)[];
};
export function studyPoints(data: StudyDispatch) {
  const keys = ['precio', 'carga', 'descarga', 'soc', 'consumo', 'generacion', 'importado', 'exportado'] as const;
  if (!Array.isArray(data.t) || data.horas !== data.t.length
      || keys.some(k => !Array.isArray(data[k]) || data[k].length !== data.t.length)) {
    throw new Error('El despacho recibido tiene series incompletas.');
  }
  const converted = (v: number | null, factor = 1) => typeof v === 'number' && Number.isFinite(v) ? v * factor : null;
  return data.t.map((timestamp, i) => {
    // Nominal hours are labels, NOT UTC or Europe/Madrid instants (including DST days).
    const m = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}):\d{2}:\d{2}$/.exec(timestamp);
    if (!m || Number(m[2]) > 23) throw new Error('El despacho no usa el calendario nominal esperado.');
    return {
      timestamp, day: m[1], label: `${m[1].slice(8, 10)}/${m[1].slice(5, 7)} · h${Number(m[2]) + 1}`,
      price: converted(data.precio[i]), charge: converted(data.carga[i], 1000),
      discharge: converted(data.descarga[i], 1000), soc: converted(data.soc[i], 1000),
      load: converted(data.consumo[i], 1000), generation: converted(data.generacion[i], 1000),
    };
  });
}
export const nominalDayOffset = (day: string, offset: number) => {
  const date = new Date(`${day}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + offset);
  return date.toISOString().slice(0, 10);
};
