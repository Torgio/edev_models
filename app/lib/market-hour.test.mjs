import { test } from 'node:test';
import assert from 'node:assert/strict';
import { marketHourLabel } from './market-hour.ts';

test('market hours start at H1 and retain the actual number of daily slots', () => {
  assert.equal(marketHourLabel(0), 'H1');
  assert.equal(marketHourLabel(23), 'H24');
  assert.equal(marketHourLabel(24), 'H25');
});

test('invalid positions do not produce a misleading market hour', () => {
  assert.equal(marketHourLabel(-1), '');
  assert.equal(marketHourLabel(1.5), '');
});
