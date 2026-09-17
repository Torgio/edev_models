/** Inclusive calendar horizon; leap-day anniversaries clamp to February 28. */
export function studyHorizonEnd(from: string, years: number, availableTo: string) {
  const start = new Date(`${from}T00:00:00Z`);
  if (!Number.isFinite(start.getTime()) || !Number.isInteger(years) || years < 1) return '';
  const anniversary = new Date(start);
  anniversary.setUTCFullYear(start.getUTCFullYear() + years);
  if (anniversary.getUTCMonth() !== start.getUTCMonth()) anniversary.setUTCDate(0);
  anniversary.setUTCDate(anniversary.getUTCDate() - 1);
  const end = anniversary.toISOString().slice(0, 10);
  return availableTo && availableTo < end ? availableTo : end;
}
