import { test } from 'node:test';
import assert from 'node:assert/strict';
import { initialDashboardDay } from './initial-day.ts';

const day = (date, closed) => ({
  date, closed, models: 3, rows: 72, rows_with_actual: closed ? 72 : 0,
  hours: 24, actual_hours: closed ? 24 : 0, expected_hours: 24,
});

test('opens the latest closed day even when a later forecast is available', () => {
  assert.equal(initialDashboardDay([
    day('2026-08-31', true), day('2026-09-01', true), day('2026-09-02', false),
  ]), '2026-09-01');
});

test('does not treat the complete day-ahead price horizon as a closed operation day', () => {
  assert.equal(initialDashboardDay([
    day('2026-09-01', true), day('2026-09-02', false),
  ]), '2026-09-01');
});

test('falls back to the latest forecast when no closed day exists', () => {
  assert.equal(initialDashboardDay([day('2026-09-01', false), day('2026-09-02', false)]), '2026-09-02');
  assert.equal(initialDashboardDay([]), null);
});
