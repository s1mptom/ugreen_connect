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

/* Readings averaged into buckets across the range.
 *
 * A charger polled every five seconds gives three hours as two thousand
 * points, and drawn raw that is a hairball rather than a shape. One point per
 * bucket, averaged, keeps the shape and drops the noise. */
export function bucket(points, count = 90, { from, to } = {}) {
  if (!points.length) return points;
  const x0 = from ?? points[0].x;
  const x1 = to ?? points[points.length - 1].x;
  const width = (x1 - x0) / count || 1;
  const cells = new Array(count).fill(null);
  for (const point of points) {
    if (point.x < x0) continue;
    const slot = Math.min(count - 1, Math.floor((point.x - x0) / width));
    const cell = cells[slot] || (cells[slot] = { sum: 0, n: 0 });
    cell.sum += point.y;
    cell.n += 1;
  }
  // A reading holds until the next one arrives: a bucket nothing was recorded
  // in is the last value still standing, not a gap. Dropping those is what drew
  // a line straight across the chart from an old zero to a new reading.
  const before = points.filter((point) => point.x <= x0).pop();
  let held = before ? before.y : (points[0]?.y ?? 0);
  const out = [];
  for (let index = 0; index < count; index += 1) {
    const cell = cells[index];
    if (cell) held = cell.sum / cell.n;
    out.push({ x: x0 + index * width, y: held });
  }
  out.push({ x: x1, y: held });
  return out;
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

/* An area chart of one or more series, as an SVG that scales with its box.
 *
 * `series`: [{name, color, points: [{x: ms, y: value}]}]. Points are drawn in
 * the order given and assumed to share a time range; a series with fewer than
 * two points is drawn as nothing rather than as a spike. */
export function areaChart(series, { width = 600, height = 180, pad = 22, gutter = 42, unit = '' } = {}) {
  const drawn = series.filter((s) => (s.points || []).length > 1);
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.setAttribute('preserveAspectRatio', 'none');
  svg.setAttribute('role', 'img');
  svg.style.width = '100%';
  svg.style.height = `${height}px`;
  if (!drawn.length) return svg;

  const xs = drawn.flatMap((s) => s.points.map((p) => p.x));
  const ys = drawn.flatMap((s) => s.points.map((p) => p.y));
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  const raw = Math.max(...ys, 1);
  const step = 10 ** Math.floor(Math.log10(raw));
  const top = Math.ceil(raw / (step / 2)) * (step / 2);
  const X = (x) => (x1 === x0 ? gutter : gutter + ((x - x0) / (x1 - x0)) * (width - gutter));
  const Y = (y) => height - pad - (y / top) * (height - pad * 1.6);

  for (const fraction of [0.25, 0.5, 0.75, 1]) {
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.setAttribute('x1', gutter);
    line.setAttribute('x2', width);
    line.setAttribute('y1', Y(top * fraction));
    line.setAttribute('y2', Y(top * fraction));
    line.setAttribute('stroke', 'var(--divider-color)');
    line.setAttribute('stroke-width', '1');
    svg.appendChild(line);
  }

  for (const s of drawn) {
    // Stepped, because that is what the readings mean: each one holds until the
    // next arrives, so the line goes along and then jumps, never diagonally
    // between two samples an hour apart.
    const line = s.points.map((p, i) => (i
      ? `H${X(p.x).toFixed(1)} V${Y(p.y).toFixed(1)}`
      : `M${X(p.x).toFixed(1)} ${Y(p.y).toFixed(1)}`)).join(' ');
    const area = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    area.setAttribute('d', `${line} L${X(s.points.at(-1).x).toFixed(1)} ${height - pad} L${X(s.points[0].x).toFixed(1)} ${height - pad} Z`);
    area.setAttribute('fill', s.color);
    area.setAttribute('fill-opacity', s.fill ?? 0.16);
    svg.appendChild(area);
    const stroke = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    stroke.setAttribute('d', line);
    stroke.setAttribute('fill', 'none');
    stroke.setAttribute('stroke', s.color);
    stroke.setAttribute('stroke-width', s.width ?? 2);
    stroke.setAttribute('vector-effect', 'non-scaling-stroke');
    svg.appendChild(stroke);
  }

  for (const fraction of [0.5, 1]) {
    const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    label.setAttribute('x', gutter - 6);
    label.setAttribute('y', Y(top * fraction) + 4);
    label.setAttribute('text-anchor', 'end');
    label.setAttribute('fill', 'var(--secondary-text-color)');
    label.setAttribute('font-size', '11');
    label.textContent = `${(top * fraction).toFixed(top < 10 ? 1 : 0)}${unit}`;
    svg.appendChild(label);
  }
  return svg;
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
