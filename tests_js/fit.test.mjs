/* Fitting a photo to the charger's 560 by 170 screen.
 *
 * The one promise the fitting step makes is that whatever is sent has no blank
 * corner: the frame always lies inside the photo, at any zoom, any offset and
 * any angle. That was easy while the photo only turned in quarter turns; turned
 * freely, the photo has to grow as it turns, and a formula that forgot the
 * angle left one to three corners of the screen black.
 *
 * Checked here by geometry rather than by pixels: each corner of the frame,
 * taken into the photo's own axes, has to land within the photo.
 */

import assert from 'node:assert/strict';
import test from 'node:test';

const { clampOffset, coverScale } = await import('../custom_components/ugreen_connect/www/ugreen-ui.js');

const FRAME = { w: 626, h: 190 };
const PHOTOS = [
  { width: 1600, height: 1200 },   // landscape, the common case
  { width: 3024, height: 4032 },   // a phone held upright
  { width: 560, height: 170 },     // exactly the screen
  { width: 800, height: 800 },     // square
];
const DEGREES = [0, 5, -12, 37, 45, 89.5, 90, 135, -145, 180];
const PUSHES = [[0, 0], [9e4, 0], [-9e4, 0], [0, 9e4], [0, -9e4], [9e4, 9e4], [-9e4, 9e4], [9e4, -9e4], [-9e4, -9e4]];

/* Whether every corner of the frame is inside the photo, the photo centred on
 * the frame plus `offset`, turned by `angle` and scaled by `scale`. */
function covered(photo, angle, scale, offset) {
  const half = { w: (photo.width * scale) / 2, h: (photo.height * scale) / 2 };
  return [[-1, -1], [1, -1], [-1, 1], [1, 1]].every(([sx, sy]) => {
    // The corner, relative to the photo's centre, turned into its axes.
    const x = sx * (FRAME.w / 2) - offset.x;
    const y = sy * (FRAME.h / 2) - offset.y;
    const ux = Math.cos(-angle) * x - Math.sin(-angle) * y;
    const uy = Math.sin(-angle) * x + Math.cos(-angle) * y;
    return Math.abs(ux) <= half.w + 1e-6 && Math.abs(uy) <= half.h + 1e-6;
  });
}

test('at its smallest zoom the photo covers the frame at any angle', () => {
  for (const photo of PHOTOS) {
    for (const deg of DEGREES) {
      const angle = (deg * Math.PI) / 180;
      const scale = coverScale(photo, FRAME, angle);
      assert.ok(covered(photo, angle, scale, { x: 0, y: 0 }),
        `${photo.width}x${photo.height} at ${deg} degrees leaves a corner blank at its cover`);
    }
  }
});

test('no drag, however far, pulls the photo off a corner', () => {
  for (const photo of PHOTOS) {
    for (const deg of DEGREES) {
      const angle = (deg * Math.PI) / 180;
      for (const zoom of [1, 1.5, 4]) {
        const scale = coverScale(photo, FRAME, angle) * zoom;
        for (const [x, y] of PUSHES) {
          const offset = clampOffset(photo, FRAME, angle, scale, { x, y });
          assert.ok(covered(photo, angle, scale, offset),
            `${photo.width}x${photo.height}, ${deg} degrees, zoom ${zoom}, pushed to (${x}, ${y})`);
        }
      }
    }
  }
});

test('the cover is no larger than it has to be', () => {
  // Square on, a landscape photo just fills the frame's width or height --
  // bigger would throw away picture the screen could have shown.
  const photo = { width: 1600, height: 1200 };
  const scale = coverScale(photo, FRAME, 0);
  assert.equal(scale, Math.max(FRAME.w / photo.width, FRAME.h / photo.height));
  // And a turned photo needs more than a square one.
  assert.ok(coverScale(photo, FRAME, (12 * Math.PI) / 180) > scale);
});

test('a photo left alone stays where it was put', () => {
  const photo = { width: 1600, height: 1200 };
  const angle = (-12 * Math.PI) / 180;
  const scale = coverScale(photo, FRAME, angle) * 2;
  const offset = { x: 20, y: -10 };
  const kept = clampOffset(photo, FRAME, angle, scale, offset);
  assert.ok(Math.abs(kept.x - offset.x) < 1e-9 && Math.abs(kept.y - offset.y) < 1e-9,
    'an offset already inside the limits was moved');
});
