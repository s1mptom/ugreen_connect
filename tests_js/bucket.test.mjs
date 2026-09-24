/* What the chart does to readings before it draws them.
 *
 * This is the one piece of card code with an answer that can be wrong rather
 * than ugly, and it was: a mean taken over readings rather than over time made
 * the same settled hour come out at 2.5 W on the one-hour range and 0.6 W on
 * the twenty-four-hour one, and a grid pinned to `now` re-cut every bucket on
 * every redraw, so the past moved while you watched it.
 *
 * Run with `node --test tests_js/`.
 */

import assert from 'node:assert/strict';
import test from 'node:test';

const { bucket } = await import('../custom_components/ugreen_connect/www/ugreen-ui.js');

const HOUR = 3600e3;
const NOW = Date.UTC(2026, 8, 24, 12, 0, 0);

/* A charger's own recording pattern: the recorder writes on change, so an idle
 * port writes every few minutes and a busy one writes every few seconds. That
 * difference is exactly what a count-weighted mean gets wrong. */
function history() {
  const points = [];
  for (let at = NOW - 24 * HOUR; at < NOW; ) {
    const minute = (at - (NOW - 24 * HOUR)) / 60e3;
    const busy = minute % 60 < 6;              // six busy minutes an hour
    const value = busy ? 120 : 1.5;
    points.push({ x: at, y: value });
    at += busy ? 5e3 : 300e3;
  }
  return points;
}

/* The true time-weighted average of a stretch, worked out from the readings
 * themselves rather than from any bucketing. */
function truth(points, from, to) {
  let area = 0;
  for (let i = 0; i < points.length - 1; i += 1) {
    const a = Math.max(points[i].x, from);
    const b = Math.min(points[i + 1].x, to);
    if (b > a) area += points[i].y * (b - a);
  }
  return area / (to - from);
}

const average = (cells) => cells.reduce((sum, cell) => sum + cell.y, 0) / cells.length;

test('the same past reads the same whichever range asks for it', () => {
  const points = history();
  const from = NOW - 2 * HOUR;
  const expected = truth(points, from, NOW);

  for (const hours of [3, 6, 24]) {
    const cells = bucket(points, 90, { from: NOW - hours * HOUR, to: NOW })
      .filter((cell) => cell.x >= from);
    const got = average(cells);
    assert.ok(
      Math.abs(got - expected) < expected * 0.1,
      `${hours} h range averages ${got.toFixed(1)} W over the last two hours, `
      + `against ${expected.toFixed(1)} W of actual charging`,
    );
  }
});

test('a settled past does not move when the chart is redrawn', () => {
  const points = history();
  const before = bucket(points, 90, { from: NOW - 3 * HOUR, to: NOW });
  const after = bucket(points, 90, { from: NOW - 3 * HOUR + 30e3, to: NOW + 30e3 });
  const settled = before.filter((cell) => cell.x < NOW - HOUR);

  for (const cell of settled) {
    const same = after.find((other) => other.x === cell.x);
    assert.ok(same, `the bucket at ${new Date(cell.x).toISOString()} was re-cut by a redraw`);
    assert.equal(same.y.toFixed(3), cell.y.toFixed(3));
  }
});

test('a peak survives being averaged into a wide bucket', () => {
  const points = history();
  for (const hours of [1, 3, 24]) {
    const cells = bucket(points, 90, { from: NOW - hours * HOUR, to: NOW });
    const peak = Math.max(...cells.map((cell) => cell.hi));
    assert.equal(peak, 120, `the ${hours} h range lost the 120 W peak (kept ${peak})`);
  }
});

test('a gap in the recording holds the last reading rather than dropping to nothing', () => {
  const points = [
    { x: NOW - 3 * HOUR, y: 40 },
    { x: NOW - 3 * HOUR + 60e3, y: 40 },
    // Nothing for two hours: the charger was steady, so the recorder was quiet.
    { x: NOW - HOUR, y: 40 },
  ];
  const cells = bucket(points, 90, { from: NOW - 3 * HOUR, to: NOW });
  assert.ok(cells.every((cell) => cell.y === 40),
    'a quiet stretch drew something other than the value that was standing');
});

test('an empty history draws nothing rather than throwing', () => {
  assert.deepEqual(bucket([], 90, { from: NOW - HOUR, to: NOW }), []);
  assert.deepEqual(bucket(null, 90, { from: NOW - HOUR, to: NOW }), []);
});
