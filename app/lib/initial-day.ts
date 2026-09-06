export type AvailableDay = {
  date: string;
  models: number;
  rows: number;
  rows_with_actual: number;
  hours: number;
  actual_hours: number;
  expected_hours: number;
  closed: boolean;
};

export function initialDashboardDay(days: AvailableDay[]): string | null {
  const latestClosed = [...days].reverse().find(day => day.closed);
  return latestClosed?.date ?? days.at(-1)?.date ?? null;
}
