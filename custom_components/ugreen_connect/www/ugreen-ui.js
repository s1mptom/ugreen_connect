/* The pieces every card here is built from.
 *
 * One module, loaded once and shared: the cards are served from the same
 * folder and import it by relative path, so a browser fetches it once however
 * many of them a dashboard holds.
 *
 * What lives here is what more than one card needed -- finding a charger's
 * entities, saying a number the same way twice, drawing a bar or a chart, and
 * the guard that keeps a second load of a module from throwing. What a single
 * card needs stays in that card.
 */

/* Entities -------------------------------------------------------------- */

/* Whether this entity belongs to the charger a card was pointed at.
 *
 * `device_id` is on the state object for entities that carry it; where it is
 * not, the entity id is all there is to go on, which is why a setup with two
 * chargers wants `device_id` in the card config. */
export function belongs(hass, entityId, deviceId) {
  const device = hass.states[entityId]?.attributes?.device_id;
  if (!deviceId) return entityId.includes('ugreen');
  return device ? device === deviceId : entityId.includes('ugreen');
}

export function findOne(hass, deviceId, domain, suffix) {
  return Object.keys(hass?.states || {}).find(
    (id) => id.startsWith(`${domain}.`) && id.endsWith(suffix) && belongs(hass, id, deviceId),
  );
}

export function findAll(hass, deviceId, domain, suffix) {
  return Object.keys(hass?.states || {}).filter(
    (id) => id.startsWith(`${domain}.`) && id.endsWith(suffix) && belongs(hass, id, deviceId),
  );
}

/* The charger's ports, in the order it reports them.
 *
 * Taken from the power sensors rather than a table of names: a model this code
 * has never heard of still has one sensor per port, and the order they were
 * created in is the charger's own. */
export function ports(hass, deviceId) {
  const total = findOne(hass, deviceId, 'sensor', '_total_power');
  return findAll(hass, deviceId, 'sensor', '_power')
    .filter((id) => id !== total)
    .map((id) => {
      const base = id.slice(0, -'_power'.length);
      const object = base.split('.')[1];
      const name = (hass.states[id].attributes.friendly_name || object)
        .replace(/\s*power$/i, '').split(' ').pop();
      return {
        id,
        base,
        name,
        charging: `binary_sensor.${object}_charging`,
        event: `event.${object}_charging`,
      };
    });
}

/* The charger's own entity prefix, taken from the total-power sensor.
 *
 * Needed because a suffix is not enough to tell the charger's counters from a
 * port's: `sensor.<prefix>_energy` is the whole charger, `sensor.<prefix>_c1_energy`
 * is one port, and both end `_energy`. */
export function prefix(hass, deviceId) {
  const total = findOne(hass, deviceId, 'sensor', '_total_power');
  return total ? total.split('.')[1].slice(0, -'_total_power'.length) : undefined;
}

/* An entity of the charger itself, by the name that follows its prefix. */
export function chargerEntity(hass, deviceId, domain, name) {
  const base = prefix(hass, deviceId);
  const id = base && `${domain}.${base}_${name}`;
  return id && hass.states[id] ? id : undefined;
}

export function num(hass, entityId, fallback = 0) {
  const value = parseFloat(hass?.states?.[entityId]?.state);
  return Number.isFinite(value) ? value : fallback;
}

/* Words ----------------------------------------------------------------- */

/* One string, in the viewer's language where the card has one.
 *
 * Keys missing from a language fall back to English, so a partial translation
 * is a useful translation. */
export function translator(table, hass) {
  const lang = (hass?.locale?.language || hass?.language || 'en').toLowerCase().split('-')[0];
  return (key, vars) => {
    const text = (table[lang] || {})[key] ?? table.en[key] ?? key;
    return vars ? text.replace(/\{(\w+)\}/g, (_, name) => vars[name]) : text;
  };
}

/* An option as the frontend spells it.
 *
 * `formatEntityState` is what the more-info dialog uses, so a card asking it
 * gets "DC turbo" and "Пользовательский" rather than a guess made from the key
 * -- which is where "Dc turbo" came from. */
export function optionLabel(hass, entityId, option) {
  const state = hass?.states?.[entityId];
  if (state && hass.formatEntityState) {
    const said = hass.formatEntityState(state, option);
    if (said && said !== option) return said;
  }
  const key = state?.attributes?.translation_key;
  const translated = key && hass.localize?.(
    `component.ugreen_connect.entity.select.${key}.state.${option}`,
  );
  return translated || option.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
}

/* Widths a bucket is allowed to be, so the grid lands on the clock.
 *
 * A bucket 40 seconds wide starting at an arbitrary instant moves every time
 * the chart is redrawn; one 60 seconds wide starting on the minute does not. */
const BUCKET_STEPS = [
  5e3, 10e3, 15e3, 30e3, 60e3, 120e3, 300e3, 600e3, 900e3, 1800e3, 3600e3, 7200e3, 21600e3,
];

/* Readings over a window, averaged by time rather than by count.
 *
 * The recorder writes on change, so a port sitting still writes twice an hour
 * and a port being plugged in writes ten times a minute. A mean taken over
 * readings is therefore weighted by how eventful a stretch happened to be: the
 * same settled hour came out as a 2.5 W peak asked for over one hour and 0.6 W
 * asked for over twenty-four, against a true 3 W. A chart that changes its mind
 * about what already happened is not one you can read.
 *
 * Each reading holds until the next arrives, so the signal is a staircase, and
 * what a bucket is worth is the area under that staircase divided by its width.
 * That number is the same whichever range asks for it; a longer range smooths
 * it, and `lo`/`hi` keep the extremes the smoothing passed over.
 */
export function bucket(points, count = 90, { from, to } = {}) {
  const sorted = (points || [])
    .filter((point) => Number.isFinite(point?.x) && Number.isFinite(point?.y))
    .sort((a, b) => a.x - b.x);
  if (!sorted.length) return [];
  const x0 = from ?? sorted[0].x;
  const x1 = to ?? sorted[sorted.length - 1].x;
  if (!(x1 > x0)) return [];

  const wanted = (x1 - x0) / Math.max(1, count);
  const step = BUCKET_STEPS.find((width) => width >= wanted) ?? Math.ceil(wanted);
  const start = Math.floor(x0 / step) * step;
  const slots = Math.max(1, Math.ceil((x1 - start) / step));
  const cells = new Array(slots).fill(null);

  for (let index = 0; index < sorted.length; index += 1) {
    const value = sorted[index].y;
    const next = index + 1 < sorted.length ? sorted[index + 1].x : x1;
    let at = Math.max(sorted[index].x, start);
    const until = Math.min(next, x1);
    let slot = Math.floor((at - start) / step);
    while (at < until && slot < slots) {
      const edge = Math.min(until, start + (slot + 1) * step);
      const cell = cells[slot] || (cells[slot] = { area: 0, span: 0, lo: value, hi: value });
      cell.area += value * (edge - at);
      cell.span += edge - at;
      cell.lo = Math.min(cell.lo, value);
      cell.hi = Math.max(cell.hi, value);
      at = edge;
      slot += 1;
    }
  }

  // A bucket nothing was recorded in is the last value still standing, not a
  // gap. Dropping those is what drew a line across the chart from an old zero
  // to a new reading.
  const before = sorted.filter((point) => point.x <= start).pop();
  let held = before ? before.y : sorted[0].y;
  return cells.map((cell, index) => {
    if (cell && cell.span > 0) held = cell.area / cell.span;
    const middle = start + (index + 0.5) * step;
    return {
      x: Math.min(Math.max(middle, x0), x1),
      y: held,
      lo: cell ? Math.min(cell.lo, held) : held,
      hi: cell ? Math.max(cell.hi, held) : held,
    };
  });
}

export function duration(seconds) {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  if (hours && minutes) return `${hours} h ${minutes} m`;
  if (hours) return `${hours} h`;
  return `${minutes} m`;
}

/* "5 minutes ago", in the viewer's language, with the frontend's own formatter
 * where the browser has one. */
export function since(hass, at, justNow = 'just now') {
  const seconds = (Date.now() - at.valueOf()) / 1000;
  if (seconds < 60) return justNow;
  const locale = hass?.locale?.language || 'en';
  const say = (value, unit) => {
    try {
      return new Intl.RelativeTimeFormat(locale, { numeric: 'auto' }).format(-value, unit);
    } catch (err) {
      return `${value} ${unit} ago`;
    }
  };
  if (seconds < 3600) return say(Math.round(seconds / 60), 'minute');
  if (seconds < 86400) return say(Math.round(seconds / 3600), 'hour');
  return say(Math.round(seconds / 86400), 'day');
}

/* What was just asked for, until the charger says otherwise.
 *
 * A write here is not instant: the frame goes out, the charger is given a
 * moment to take it, and only then is the state read back -- four to seven
 * seconds in all. The poll in between publishes the old value, so a control
 * bound straight to the entity flips back under the finger that moved it and
 * then flips again when the read-back lands. Holding the asked-for value until
 * the entity agrees, or until it is clear it never will, is what stops that.
 */
export function pending(timeout = 15000) {
  const asked = new Map();
  return {
    set(key, value) { asked.set(key, { value, until: Date.now() + timeout }); },
    read(key, actual) {
      const entry = asked.get(key);
      if (!entry) return actual;
      if (entry.value === actual || Date.now() > entry.until) {
        asked.delete(key);
        return actual;
      }
      return entry.value;
    },
    clear(key) { asked.delete(key); },
  };
}

/* Drawing --------------------------------------------------------------- */

/* Theme variables rather than colours: a card that reads its palette from the
 * theme is a card that looks like the rest of somebody's dashboard, whatever
 * they have chosen. */
const SVG = 'http://www.w3.org/2000/svg';

export const SHARED_CSS = `
  /* The hidden attribute loses to any display a rule sets, and most of what
   * these cards hide is a flex row or a grid. Said once here rather than
   * remembered in every card that hides something. */
  [hidden] { display: none !important; }
  .u-label { font-size: .78em; text-transform: uppercase; letter-spacing: .06em;
             color: var(--secondary-text-color); }
  .u-muted { color: var(--secondary-text-color); }
  .u-sub { font-size: .9em; color: var(--secondary-text-color); }
  .u-empty { font-size: .9em; color: var(--secondary-text-color); padding: 6px 0; }
  .u-meter { height: 6px; border-radius: 3px; background: var(--divider-color); overflow: hidden; }
  .u-meter > i { display: block; height: 100%;
                 background: var(--state-icon-active-color, var(--primary-color)); }
  .u-row { display: flex; align-items: center; gap: 8px; }
  .u-chip { display: inline-flex; align-items: center; gap: 6px; padding: 5px 10px; font: inherit;
            font-size: .82em; background: var(--secondary-background-color); border: none;
            border-radius: 999px; color: var(--secondary-text-color); cursor: pointer; }
  .u-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--disabled-text-color); flex: none; }
  .u-on .u-dot { background: var(--state-icon-active-color, var(--primary-color)); }
  table.u-table { width: 100%; border-collapse: collapse; font-size: .92em; }
  table.u-table th { text-align: left; font-weight: 500; font-size: .82em; text-transform: uppercase;
                     letter-spacing: .04em; color: var(--secondary-text-color); padding: 0 6px 4px 0; }
  table.u-table td { padding: 5px 6px 5px 0; border-top: 1px solid var(--divider-color); }
  table.u-table .num { text-align: right; }
  table.u-table tr.click { cursor: pointer; }
  table.u-table tr.click:hover td { background: var(--secondary-background-color); }
  .u-chart { position: relative; width: 100%; height: 100%; }
  .u-chart svg { display: block; width: 100%; height: 100%; }
  /* The readout follows the pointer, so it must never be under it. */
  .u-tip { position: absolute; top: 2px; pointer-events: none; z-index: 2;
           background: var(--card-background-color); border: 1px solid var(--divider-color);
           border-radius: 8px; padding: 6px 8px; font-size: 12px; white-space: nowrap;
           box-shadow: 0 2px 10px rgba(0, 0, 0, .18); }
  .u-tip .when { font-size: 11px; color: var(--secondary-text-color); margin-bottom: 3px; }
  .u-tip .line { display: flex; align-items: center; gap: 6px; line-height: 1.5; }
  .u-tip .key { width: 10px; height: 2px; border-radius: 1px; flex: none; }
  .u-tip b { font-weight: 500; }
  .u-tip .name { color: var(--secondary-text-color); }
  @media (max-width: 520px) { .u-narrow-hide { display: none; } }
`;

/* The eight colours a chart hands out, from the theme's own graph palette so a
 * dashboard's charts agree with each other. */
export const SERIES = [
  'var(--graph-color-1, #4269d0)',
  'var(--graph-color-2, #efb118)',
  'var(--graph-color-3, #ff725c)',
  'var(--graph-color-4, #6cc5b0)',
  'var(--graph-color-5, #3ca951)',
  'var(--graph-color-6, #ff8ab7)',
  'var(--graph-color-7, #a463f2)',
  'var(--graph-color-8, #97bbf5)',
];

/* A card's own root, so its stylesheet cannot reach anything else.
 *
 * These cards are plain elements with plain class names -- `.head`, `.screen`,
 * `.grid` -- and their `<style>` blocks used to sit in the page, where they
 * styled each other: the dashboard's own `.screen` grid was quietly turned
 * into a flex row by the charger card's `.screen` rule. A shadow root per card
 * keeps each one's CSS to itself, and lets the markup keep the names that read
 * well inside it.
 */
export function mount(element, markup) {
  const root = element.shadowRoot || element.attachShadow({ mode: 'open' });
  root.innerHTML = markup;
  return root;
}

export function html(markup) {
  const template = document.createElement('template');
  template.innerHTML = markup.trim();
  return template.content.firstElementChild;
}

/* A chart of one or more series over time, with what the pointer is on.
 *
 * Two shapes per series rather than one: the line is the time-weighted average
 * of each bucket, and behind it a paler fill reaches up to the highest reading
 * in that bucket. On a short range the two are nearly the same shape; on a long
 * one the line is smooth and the fill still shows the spikes the smoothing
 * passed over -- so a peak does not disappear when the range is widened.
 *
 * Straight segments between bucket centres, not steps: a bucket holds an
 * average over its width, and a staircase drawn through averages is a shape
 * the readings never had.
 */
export function areaChart(series, {
  width = 600, height = 180, pad = 22, gutter = 42, unit = '', label,
} = {}) {
  const drawn = series.filter((one) => (one.points || []).length > 1);
  const box = document.createElement('div');
  box.className = 'u-chart';
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.setAttribute('preserveAspectRatio', 'none');
  svg.setAttribute('role', 'img');
  box.appendChild(svg);
  if (!drawn.length) return box;

  const xs = drawn.flatMap((one) => one.points.map((point) => point.x));
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  const raw = Math.max(...drawn.flatMap((one) => one.points.map((p) => p.hi ?? p.y)), 1);
  const step = 10 ** Math.floor(Math.log10(raw));
  const top = Math.ceil(raw / (step / 2)) * (step / 2);
  const X = (x) => (x1 === x0 ? gutter : gutter + ((x - x0) / (x1 - x0)) * (width - gutter));
  const Y = (y) => height - pad - (Math.min(y, top) / top) * (height - pad * 1.6);
  const floor = height - pad;

  for (const fraction of [0.25, 0.5, 0.75, 1]) {
    const line = document.createElementNS(SVG, 'line');
    line.setAttribute('x1', gutter);
    line.setAttribute('x2', width);
    line.setAttribute('y1', Y(top * fraction));
    line.setAttribute('y2', Y(top * fraction));
    line.setAttribute('stroke', 'var(--divider-color)');
    line.setAttribute('stroke-width', '1');
    svg.appendChild(line);
  }

  const path = (points, pick) => points
    .map((point, index) => `${index ? 'L' : 'M'}${X(point.x).toFixed(1)} ${Y(pick(point)).toFixed(1)}`)
    .join(' ');
  const under = (points, pick) => `${path(points, pick)} L${X(points[points.length - 1].x).toFixed(1)} ${floor} L${X(points[0].x).toFixed(1)} ${floor} Z`;

  for (const one of drawn) {
    const fill = one.fill ?? 0.16;
    const spiky = one.points.some((point) => (point.hi ?? point.y) - point.y > top / 50);
    if (spiky) {
      const peaks = document.createElementNS(SVG, 'path');
      peaks.setAttribute('d', under(one.points, (point) => point.hi ?? point.y));
      peaks.setAttribute('fill', one.color);
      peaks.setAttribute('fill-opacity', (fill * 0.45).toFixed(3));
      svg.appendChild(peaks);
    }
    const area = document.createElementNS(SVG, 'path');
    area.setAttribute('d', under(one.points, (point) => point.y));
    area.setAttribute('fill', one.color);
    area.setAttribute('fill-opacity', fill);
    svg.appendChild(area);
    const stroke = document.createElementNS(SVG, 'path');
    stroke.setAttribute('d', path(one.points, (point) => point.y));
    stroke.setAttribute('fill', 'none');
    stroke.setAttribute('stroke', one.color);
    stroke.setAttribute('stroke-width', one.width ?? 2);
    stroke.setAttribute('stroke-linejoin', 'round');
    stroke.setAttribute('vector-effect', 'non-scaling-stroke');
    svg.appendChild(stroke);
  }

  for (const fraction of [0.5, 1]) {
    const text = document.createElementNS(SVG, 'text');
    text.setAttribute('x', gutter - 6);
    text.setAttribute('y', Y(top * fraction) + 4);
    text.setAttribute('text-anchor', 'end');
    text.setAttribute('fill', 'var(--secondary-text-color)');
    text.setAttribute('font-size', '11');
    text.textContent = `${(top * fraction).toFixed(top < 10 ? 1 : 0)}${unit}`;
    svg.appendChild(text);
  }

  hover(box, svg, drawn, { X, Y, x0, x1, gutter, width, height, pad, unit, label });
  return box;
}

/* What the pointer is on: a hairline down the chart, a dot per series, and one
 * readout listing every series at that moment -- so a value never has to be
 * hunted for by landing on a two-pixel line. */
function hover(box, svg, drawn, { X, Y, x0, x1, gutter, width, height, unit, label }) {
  const rule = document.createElementNS(SVG, 'line');
  rule.setAttribute('stroke', 'var(--secondary-text-color)');
  rule.setAttribute('stroke-width', '1');
  rule.setAttribute('stroke-dasharray', '3 3');
  rule.setAttribute('y1', 4);
  rule.setAttribute('y2', height - 8);
  rule.style.display = 'none';
  svg.appendChild(rule);
  const dots = drawn.map((one) => {
    const dot = document.createElementNS(SVG, 'circle');
    dot.setAttribute('r', '3.5');
    dot.setAttribute('fill', one.color);
    dot.setAttribute('stroke', 'var(--card-background-color)');
    dot.setAttribute('stroke-width', '1.5');
    dot.style.display = 'none';
    svg.appendChild(dot);
    return dot;
  });

  const tip = document.createElement('div');
  tip.className = 'u-tip';
  tip.hidden = true;
  box.appendChild(tip);

  const nearest = (points, x) => points.reduce(
    (best, point) => (Math.abs(point.x - x) < Math.abs(best.x - x) ? point : best), points[0],
  );

  const show = (event) => {
    const rect = box.getBoundingClientRect();
    const fraction = (event.clientX - rect.left) / rect.width;
    const inPlot = (fraction * width - gutter) / (width - gutter);
    if (!(inPlot >= 0 && inPlot <= 1)) return hide();
    const x = x0 + inPlot * (x1 - x0);
    const at = nearest(drawn[0].points, x);
    const px = X(at.x);
    rule.setAttribute('x1', px);
    rule.setAttribute('x2', px);
    rule.style.display = '';
    tip.replaceChildren();
    if (label) {
      const when = document.createElement('div');
      when.className = 'when';
      when.textContent = label(at.x);
      tip.appendChild(when);
    }
    drawn.forEach((one, index) => {
      const point = nearest(one.points, at.x);
      const dot = dots[index];
      dot.setAttribute('cx', X(point.x));
      dot.setAttribute('cy', Y(point.y));
      dot.style.display = '';
      const row = document.createElement('div');
      row.className = 'line';
      const key = document.createElement('span');
      key.className = 'key';
      key.style.background = one.color;
      const value = document.createElement('b');
      const peak = point.hi ?? point.y;
      value.textContent = `${point.y.toFixed(1)}${unit}`;
      const name = document.createElement('span');
      name.className = 'name';
      // The peak is only worth saying when the average has hidden one.
      name.textContent = peak - point.y > 0.5
        ? `${one.name} · peak ${peak.toFixed(1)}${unit}`
        : one.name;
      row.append(key, value, name);
      tip.appendChild(row);
    });
    tip.hidden = false;
    const left = (px / width) * rect.width;
    tip.style.left = `${Math.min(rect.width - 8, Math.max(8, left))}px`;
    tip.style.transform = `translate(${left > rect.width / 2 ? '-100%' : '0'}, 0)`;
  };
  const hide = () => {
    rule.style.display = 'none';
    for (const dot of dots) dot.style.display = 'none';
    tip.hidden = true;
  };

  box.addEventListener('pointermove', show);
  box.addEventListener('pointerleave', hide);
  box.addEventListener('pointercancel', hide);
}

/* Defining an element twice throws, and a module can be loaded twice: an older
 * url left in the resource list, a dashboard adding it by hand beside the
 * integration's own. First copy in wins, the rest do nothing -- which is what a
 * second <script> for one card should do. */
export function defineCard(tag, cls, { name, description }) {
  if (customElements.get(tag)) return;
  customElements.define(tag, cls);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: tag, name, description });
}
