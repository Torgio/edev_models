import { test } from 'node:test';
import assert from 'node:assert/strict';
import { dailyHourSlots, dailyPrice } from './daily-price.ts';
const day = '2026-08-31';
const row = (hour, prediction, actual) => ({ datetime: `2026-08-31T${hour}:00:00+02:00`, predictions: { ensemble: prediction }, actual });

test('daily means and signed difference use hourly prices without rounding early', () => {
  const result = dailyPrice([row('00', 10, 8), row('01', 20, 18)], day);
  assert.equal(result.predicted, 15);
  assert.equal(result.actual, 13);
  assert.equal(result.difference, 2);
  assert.equal(result.predictedHours, 2);
  assert.equal(result.expectedHours, 24);
});
test('difference compares the same hours, not two differently covered averages', () => {
  const result = dailyPrice([row('00', 10, 12), row('01', 100, null), row('02', null, 50)], day);
  assert.equal(result.predicted, 55);
  assert.equal(result.actual, 31);
  assert.equal(result.difference, -2);
  assert.equal(result.pairedHours, 1);
  assert.equal(result.pairedPrediction, 10);
  assert.equal(result.pairedReal, 12);
});
test('missing data stays missing; zero and negative prices remain valid', () => {
  const result = dailyPrice([row('00', 0, -10), row('01', NaN, Infinity), row('02', null, null)], day);
  assert.equal(result.predicted, 0);
  assert.equal(result.actual, -10);
  assert.equal(result.predictedHours, 1);
  assert.equal(dailyPrice([row('00', 20, null)], day).actual, null);
  assert.equal(dailyPrice([row('00', 20, null)], day).difference, null);
  assert.equal(dailyPrice([], day).predicted, null);
});
test('complete days respect Madrid DST and keep the two repeated clock hours', () => {
  for (const [date, expected] of [['2026-03-29', 23], ['2026-10-25', 25], [day, 24]]) {
    const hours = [...dailyHourSlots(date)].map(instant => ({ datetime: new Date(instant).toISOString(), actual: 10, predictions: { ensemble: 15 } }));
    const result = dailyPrice(hours, date);
    assert.equal(result.expectedHours, expected);
    assert.equal(result.pairedHours, expected);
    assert.equal(result.difference, 5);
  }
});
test('duplicates, another day and invalid timestamps cannot inflate coverage', () => {
  const point = row('00', 10, 10);
  const result = dailyPrice([point, point, { ...point, datetime: '2026-09-01T00:00:00+02:00' }, { ...point, datetime: 'invalid' }], day);
  assert.equal(result.actualHours, 1);
});
test('coverage keeps absent Ensemble and partial real prices distinct from complete data', () => {
  const hours = [...dailyHourSlots(day)].map((instant, index) => ({
    datetime: new Date(instant).toISOString(), actual: index < 20 ? 0 : null,
    predictions: { gru: 10 },
  }));
  const result = dailyPrice(hours, day);
  assert.equal(result.actualHours, 20);
  assert.equal(result.predictedHours, 0);
  assert.equal(result.expectedHours, 24);
  assert.equal(result.pairedHours, 0);
  const empty = dailyPrice([], day);
  assert.equal(empty.actualHours, 0);
  assert.equal(empty.predictedHours, 0);
  assert.equal(empty.expectedHours, 24);
});
