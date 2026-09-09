import test from 'node:test';
import assert from 'node:assert/strict';
import { formatEnergyPrice } from './price-format.ts';

test('energy prices share one Spanish two-decimal format', () => {
  assert.equal(formatEnergyPrice(122.41), '122,41 €/MWh');
  assert.equal(formatEnergyPrice(20.9), '20,90 €/MWh');
  assert.equal(formatEnergyPrice(14.65, { sign: true }), '+14,65 €/MWh');
  assert.equal(formatEnergyPrice(-7.6, { unit: false }), '-7,60');
  assert.equal(formatEnergyPrice(null), '—');
});
