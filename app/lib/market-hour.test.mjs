import { test } from 'node:test';
import assert from 'node:assert/strict';
import { marketHourClockLabel, marketHourLabel } from './market-hour.ts';

test('market hours start at H1 and retain the actual number of daily slots', () => {
  assert.equal(marketHourLabel(0), 'H1');
  assert.equal(marketHourLabel(23), 'H24');
  assert.equal(marketHourLabel(24), 'H25');
});

test('invalid positions do not produce a misleading market hour', () => {
  assert.equal(marketHourLabel(-1), '');
  assert.equal(marketHourLabel(1.5), '');
});

test('market labels retain the civil clock time supplied by the API', () => {
  assert.equal(marketHourClockLabel(21, '21:00'), 'H22 · 21:00');
});

test('repeated DST clock hours remain distinguishable by their market position', () => {
  assert.equal(marketHourClockLabel(2, '02:00'), 'H3 · 02:00');
  assert.equal(marketHourClockLabel(3, '02:00'), 'H4 · 02:00');
});

test('invalid or absent clock values fall back to the market label', () => {
  assert.equal(marketHourClockLabel(0, undefined), 'H1');
  assert.equal(marketHourClockLabel(0, '24:00'), 'H1');
});
