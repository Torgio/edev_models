const price = new Intl.NumberFormat('es-ES', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function formatEnergyPrice(
  value: unknown,
  { unit = true, sign = false }: { unit?: boolean; sign?: boolean } = {},
) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  const prefix = sign && value > 0 ? '+' : '';
  return `${prefix}${price.format(value)}${unit ? ' €/MWh' : ''}`;
}
