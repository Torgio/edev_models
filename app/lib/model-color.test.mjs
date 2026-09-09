import assert from 'node:assert/strict';
import test from 'node:test';

import { MODEL_STYLES, modelColor } from './model-color.ts';

test('a model keeps the same deterministic color in every view', () => {
  assert.equal(modelColor('gru'), '#43a99f');
  assert.equal(modelColor('gru'), MODEL_STYLES.find(row => row.key === 'gru').color);
  assert.equal(modelColor('modelo_nuevo'), modelColor('modelo_nuevo'));
  assert.match(modelColor('modelo_nuevo'), /^hsl\(\d+ 46% 46%\)$/);
});
