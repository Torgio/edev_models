import assert from 'node:assert/strict';
import test from 'node:test';
import { dailyHourSlots } from './daily-price.ts';
import { marketWindows, operationIntervals } from './market-summary.ts';

const day = '2026-09-10';
const rows = (date, values) => [...dailyHourSlots(date)].map((instant, i) => ({ datetime: new Date(instant).toISOString(), predictions: { ensemble: values[i] ?? null } }));

test('compares fixed windows, preserves negative and zero prices, and uses selected model', () => {
  const data = rows(day, [-3, 0, 3, 30, 60, 90]);
  const result = marketWindows(data, day, 'ensemble');
  assert.deepEqual(result.cheapest, { label: '00:00–03:00', average: 0 });
  assert.deepEqual(result.priciest, { label: '03:00–06:00', average: 60 });
  assert.equal(result.covered, 6);
  assert.equal(marketWindows(data, day, 'missing').cheapest, null);
});

test('does not bridge gaps, duplicate timestamps, or borrow from an adjacent day', () => {
  const data = rows(day, [1, 2, null, 4, 5]);
  assert.equal(marketWindows(data, day, 'ensemble').cheapest, null);
  const complete = rows(day, [1, 2, 3]);
  complete.push({ ...complete[0], predictions: { ensemble: -1000 } });
  complete.push(...rows('2026-09-11', [-100, -100, -100]));
  assert.equal(marketWindows(complete, day, 'ensemble').cheapest.average, 2);
});

test('handles both clock changes with 23/25 real hourly slots and explicit offsets', () => {
  for (const [date, length] of [['2026-03-29', 23], ['2026-10-25', 25]]) {
    const result = marketWindows(rows(date, Array(length).fill(10)), date, 'ensemble');
    assert.equal(result.expected, length);
    assert.equal(result.covered, length);
    assert.match(result.cheapest.label, /GMT/);
  }
});

test('groups only consecutive operations and retains midnight endpoints', () => {
  const slots = [...dailyHourSlots(day)];
  assert.equal(operationIntervals([slots[13], slots[12], slots[13], slots[19], slots[23]]), '12:00–14:00, 19:00–20:00, 23:00–00:00');
  assert.equal(operationIntervals([]), '');
});
