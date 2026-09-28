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

/* The chargers set up in Home Assistant: one entry per device that owns this
 * integration's total-power sensor, named as the device page names it.
 *
 * Read from `hass.entities`, the entity registry the frontend keeps, which is
 * where Home Assistant says which device an entity belongs to. Worked out
 * once per registry: the object is replaced when the registry changes, and a
 * card asks on every update. */
const CHARGERS = new WeakMap();

export function chargers(hass) {
  const entities = hass?.entities;
  if (!entities) return [];
  const cached = CHARGERS.get(entities);
  if (cached && cached.devices === hass.devices) return cached.list;
  const list = [];
  const seen = new Set();
  for (const [id, entry] of Object.entries(entities)) {
    if (entry?.platform !== 'ugreen_connect' || !entry.device_id || seen.has(entry.device_id)) continue;
    if (!id.startsWith('sensor.') || !bare(id).endsWith('_total_power')) continue;
    seen.add(entry.device_id);
    const device = hass.devices?.[entry.device_id] || {};
    list.push({
      id: entry.device_id,
      name: device.name_by_user || device.name || id,
      own: device.name_by_user || '',
      app: device.name || '',
      product: device.model || '',
      model: device.model_id || '',
      total: id,
    });
  }
  list.sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
  CHARGERS.set(entities, { devices: hass.devices, list });
  return list;
}

/* The charger a card shows: the one in its config, else the first there is --
 * never all of them at once. One that answers, before one that has left the
 * account and sits there unavailable. */
export function resolveDevice(hass, deviceId) {
  if (deviceId) return deviceId;
  const list = chargers(hass);
  const live = list.find((c) => !['unavailable', 'unknown'].includes(hass.states?.[c.total]?.state));
  return (live || list[0])?.id;
}

/* How a charger is called on screen. The name given in Home Assistant, else
 * the one given in the UGREEN app, else -- where the app's is still its
 * default, "UGREEN Nexode Pro X783" -- the product without the maker's name,
 * "Nexode Pro 300W". Two that would read the same are numbered. */
export function chargerNames(list) {
  const base = list.map((c) => {
    if (c.own) return c.own;
    const standard = /^UGREEN\b/i.test(c.app) && (!c.model || c.app.endsWith(c.model));
    if (c.app && !standard) return c.app;
    return (c.product || c.app || c.name).replace(/^UGREEN\s+/i, '');
  });
  return base.map((name, i) => (base.indexOf(name) === base.lastIndexOf(name)
    ? name : `${name} (${base.slice(0, i + 1).filter((n) => n === name).length})`));
}

/* What each model can deliver in all, for a card that was not told. */
const BUDGET = { X783: 300, X776: 160 };

export function budget(hass, deviceId) {
  const id = resolveDevice(hass, deviceId);
  return BUDGET[hass?.devices?.[id]?.model_id] || 0;
}

/* Whether this entity belongs to that charger.
 *
 * By the entity registry. It used to look for `device_id` on the state
 * object, where nothing puts it, so `device_id` in a card's config filtered
 * nothing and two chargers ran together on every card. The entity id is
 * still the fallback where the registry has not arrived. */
export function belongs(hass, entityId, deviceId) {
  const device = hass.entities?.[entityId]?.device_id ?? hass.states?.[entityId]?.attributes?.device_id;
  if (deviceId && device) return device === deviceId;
  return entityId.includes('ugreen');
}

/* An entity id without the number Home Assistant adds when a name is taken:
 * two chargers that nobody renamed are both "UGREEN Nexode Pro X783", and the
 * second one's entities end `_power_2`. */
export function bare(id) {
  return id.replace(/_\d+$/, '');
}

function tailOf(id) {
  return (id.match(/_\d+$/) || [''])[0];
}

export function findOne(hass, deviceId, domain, suffix) {
  const wanted = resolveDevice(hass, deviceId);
  return Object.keys(hass?.states || {}).find(
    (id) => id.startsWith(`${domain}.`) && bare(id).endsWith(suffix) && belongs(hass, id, wanted),
  );
}

export function findAll(hass, deviceId, domain, suffix) {
  const wanted = resolveDevice(hass, deviceId);
  return Object.keys(hass?.states || {}).filter(
    (id) => id.startsWith(`${domain}.`) && bare(id).endsWith(suffix) && belongs(hass, id, wanted),
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
      const tail = tailOf(id);
      const base = bare(id).slice(0, -'_power'.length);
      const object = base.split('.')[1];
      // The port's name as the integration gives it. The friendly name is
      // translated -- "C1 Leistung", "C1: мощность" -- so its last word is
      // only right in English; it is the fallback for an older release.
      const name = hass.states[id].attributes.port
        || (hass.states[id].attributes.friendly_name || object).replace(/\s*power$/i, '').split(' ').pop();
      return {
        id,
        base,
        name,
        charging: `binary_sensor.${object}_charging${tail}`,
        event: `event.${object}_charging${tail}`,
        // Another entity of this port, by what follows its name.
        of: (domain, what) => `${domain}.${object}_${what}${tail}`,
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
  return total ? bare(total).split('.')[1].slice(0, -'_total_power'.length) : undefined;
}

/* An entity of the charger itself, by the name that follows its prefix. */
export function chargerEntity(hass, deviceId, domain, name) {
  const base = prefix(hass, deviceId);
  const total = findOne(hass, deviceId, 'sensor', '_total_power');
  const id = base && `${domain}.${base}_${name}${total ? tailOf(total) : ''}`;
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
/* How this person wants a time of day written.
 *
 * Home Assistant keeps it in the user's profile -- 12 hours, 24, or whatever
 * the language does -- and a card that asked the browser instead wrote
 * "04:19 PM" for someone whose every other clock in HA says 16:19. */
export function timeOptions(hass) {
  const pick = hass?.locale?.time_format;
  const base = { hour: '2-digit', minute: '2-digit' };
  if (pick === '12') return { ...base, hour12: true };
  if (pick === '24') return { ...base, hour12: false };
  return base;
}

export function timeOf(hass, at) {
  return new Date(at).toLocaleTimeString(hass?.locale?.language || undefined, timeOptions(hass));
}

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
  /* A port keeps its colour everywhere it appears -- the dot on its tile, its
   * line on the chart, its bar -- so the colour is the port's name as much as
   * the label is. Eight hues in a fixed order, stepped separately for light
   * and dark, and checked for both: adjacent pairs at least 8 apart for the
   * common kinds of colour blindness and 19 apart for everyone. Home
   * Assistant's own graph palette failed that -- two of its eight read as
   * grey, and its green and teal were close enough to confuse outright. A
   * theme can still set --ugreen-port-color-1 .. -8 and win. */
  :host {
    --ugreen-port-1: var(--ugreen-port-color-1, #2a78d6);
    --ugreen-port-2: var(--ugreen-port-color-2, #eb6834);
    --ugreen-port-3: var(--ugreen-port-color-3, #1baf7a);
    --ugreen-port-4: var(--ugreen-port-color-4, #eda100);
    --ugreen-port-5: var(--ugreen-port-color-5, #e87ba4);
    --ugreen-port-6: var(--ugreen-port-color-6, #008300);
    --ugreen-port-7: var(--ugreen-port-color-7, #4a3aa7);
    --ugreen-port-8: var(--ugreen-port-color-8, #e34948);
  }
  :host([data-dark]) {
    --ugreen-port-1: var(--ugreen-port-color-1, #3987e5);
    --ugreen-port-2: var(--ugreen-port-color-2, #d95926);
    --ugreen-port-3: var(--ugreen-port-color-3, #199e70);
    --ugreen-port-4: var(--ugreen-port-color-4, #c98500);
    --ugreen-port-5: var(--ugreen-port-color-5, #d55181);
    --ugreen-port-6: var(--ugreen-port-color-6, #008300);
    --ugreen-port-7: var(--ugreen-port-color-7, #9085e9);
    --ugreen-port-8: var(--ugreen-port-color-8, #e66767);
  }
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
  /* A choice of one among a few, drawn as a sunken track with the chosen one
   * raised out of it -- the charging modes, a chart's range, 12 or 24 hours.
   * The raised face is the card's own colour on a light theme and a step
   * lighter than the track on a dark one, where the card is darker than the
   * track and would read as a hole. */
  .u-seg { display: inline-flex; flex: none; gap: 2px; padding: 3px; border-radius: 10px;
           background: var(--secondary-background-color); }
  .u-seg button { font: inherit; font-size: 13px; height: 30px; padding: 0 14px; border-radius: 8px;
                  border: none; background: transparent; color: var(--primary-text-color);
                  cursor: pointer; white-space: nowrap; }
  .u-seg button[aria-checked="true"] { font-weight: 500; box-shadow: 0 1px 2px rgba(0, 0, 0, .18);
                  background: var(--ha-card-background, var(--card-background-color)); }
  :host([data-dark]) .u-seg button[aria-checked="true"] { box-shadow: 0 1px 2px rgba(0, 0, 0, .5);
                  background: color-mix(in srgb, var(--primary-text-color) 12%, var(--secondary-background-color)); }
  .u-seg button:disabled { color: var(--disabled-text-color); cursor: default; }
  .u-seg button:focus-visible, .u-pill:focus-visible, .u-button:focus-visible {
                  outline: 2px solid var(--primary-color); outline-offset: 1px; }
  .u-seg.small button { font-size: 12px; height: 24px; padding: 0 10px; border-radius: 6px; }
  /* A button that is an action rather than a choice. */
  .u-button { font: inherit; font-size: 13px; font-weight: 500; height: 32px; padding: 0 14px;
              border-radius: 16px; border: 1px solid var(--divider-color); background: transparent;
              color: var(--primary-color); cursor: pointer; white-space: nowrap; }
  .u-button.primary { border-color: transparent; background: var(--primary-color);
                      color: var(--text-primary-color, #fff); }
  /* A toggle among several that can each be on -- the ports charged first. */
  .u-pill { font: inherit; font-size: 13px; height: 30px; padding: 0 12px; border-radius: 15px;
            display: inline-flex; align-items: center; gap: 7px; cursor: pointer;
            border: 1px solid var(--divider-color); background: transparent;
            color: var(--primary-text-color); }
  .u-pill[aria-pressed="true"] { font-weight: 500;
            border-color: color-mix(in srgb, var(--primary-color) 55%, transparent);
            background: color-mix(in srgb, var(--primary-color) 16%, transparent); }
  /* A choice of one as a list, where a row of segments does not fit. */
  .u-pick { position: relative; display: inline-block; color: var(--secondary-text-color); }
  .u-pick select { appearance: none; -webkit-appearance: none; margin: 0; width: 100%; height: 36px;
                   box-sizing: border-box; padding: 0 34px 0 14px; font: inherit; font-size: 14px;
                   color: var(--primary-text-color); background: var(--secondary-background-color);
                   border: 1px solid transparent; border-radius: 18px; }
  .u-pick select:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 1px; }
  .u-pick svg { position: absolute; right: 14px; top: 50%; margin-top: -3px; pointer-events: none; }
  .u-card-title { margin: 0; font-size: 15px; font-weight: 500; }
  @media (max-width: 520px) { .u-narrow-hide { display: none; } }
`;

/* A port's colour, by its place on the charger. See SHARED_CSS for the values
 * and why they are these. */
export const SERIES = [1, 2, 3, 4, 5, 6, 7, 8].map((n) => `var(--ugreen-port-${n})`);

/* Which of the two palettes a card draws with.
 *
 * Home Assistant says whether its theme is dark, and that -- not the
 * operating system's preference -- is what the card is sitting on. */
export function applyTheme(element, hass) {
  element.toggleAttribute('data-dark', Boolean(hass?.themes?.darkMode));
}

/* Fitting a photo to the charger's screen ------------------------------- */

/* The frame's size measured along a photo's own sides, with the photo turned
 * by `angle` radians. At a quarter turn that is the frame with its sides
 * swapped; at anything else it is the box the turned frame needs. */
export function frameSpan(frame, angle) {
  const c = Math.abs(Math.cos(angle));
  const s = Math.abs(Math.sin(angle));
  return { w: frame.w * c + frame.h * s, h: frame.w * s + frame.h * c };
}

/* The smallest scale at which a photo turned by `angle` still covers the
 * frame. Any smaller and the charger shows a blank corner; turned off the
 * square, a photo has to be larger than the frame for its corners to stay
 * covered too. */
export function coverScale(image, frame, angle) {
  const span = frameSpan(frame, angle);
  return Math.max(span.w / image.width, span.h / image.height);
}

/* Where the photo may sit so the frame stays inside it: the offset, turned
 * into the photo's own axes, held within what the photo has to spare there,
 * and turned back. The frame and the photo share a centre at offset zero. */
export function clampOffset(image, frame, angle, scale, offset) {
  const span = frameSpan(frame, angle);
  const lx = Math.max(0, (image.width * scale - span.w) / 2);
  const ly = Math.max(0, (image.height * scale - span.h) / 2);
  const ux = Math.cos(-angle) * offset.x - Math.sin(-angle) * offset.y;
  const uy = Math.sin(-angle) * offset.x + Math.cos(-angle) * offset.y;
  const cx = Math.min(lx, Math.max(-lx, ux));
  const cy = Math.min(ly, Math.max(-ly, uy));
  return { x: Math.cos(angle) * cx - Math.sin(angle) * cy, y: Math.sin(angle) * cx + Math.cos(angle) * cy };
}

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

/* Where the top of the axis sits.
 *
 * Only from this list, so that half of it -- the other labelled gridline -- is
 * a number a person would say out loud. A top chosen as "whatever is above the
 * highest reading" gave gridlines at 7.5 W and 3.75 W, and rounding those to
 * fit made the axis claim 8 W and 4 W for lines that were not there. */
const AXIS_TOPS = [1, 1.2, 1.4, 1.6, 1.8, 2, 3, 4, 5, 6, 8, 10];

export function axisTop(raw) {
  const decade = 10 ** Math.floor(Math.log10(raw));
  const mantissa = raw / decade;
  return (AXIS_TOPS.find((candidate) => candidate >= mantissa - 1e-9) ?? 10) * decade;
}

/* The number, not a rounding of it. Two decimals is enough for every top above
 * and the trailing zeroes go. */
export function axisLabel(value) {
  return String(Number(value.toFixed(2)));
}

/* A chart of one or more series over time, with what the pointer is on.
 *
 * One line per series: the time-weighted average of each bucket, filled to the
 * floor. A wider range smooths it rather than changing it, and the highest
 * reading a bucket covered is kept for the readout under the pointer -- drawn
 * as a second shape it read as an unnamed series of its own.
 *
 * Straight segments between bucket centres, not steps: a bucket holds an
 * average over its width, and a staircase drawn through averages is a shape
 * the readings never had.
 */
export function areaChart(series, {
  width = 600, height = 180, pad = 22, gutter = 42, unit = '', label, ticks = [],
} = {}) {
  const drawn = series.filter((one) => (one.points || []).length > 1);
  const box = document.createElement('div');
  box.className = 'u-chart';
  // Drawn at the size it is shown at. Stretched from a fixed box instead, the
  // axis labels came out tall and thin whenever the card was taller than the
  // box was drawn for.
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.setAttribute('width', String(width));
  svg.setAttribute('height', String(height));
  svg.setAttribute('role', 'img');
  box.appendChild(svg);
  if (!drawn.length) return box;

  const xs = drawn.flatMap((one) => one.points.map((point) => point.x));
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  const raw = Math.max(...drawn.flatMap((one) => one.points.map((point) => point.y)), 1);
  const top = axisTop(raw);
  const bottom = ticks.length ? pad + 8 : pad;
  const X = (x) => (x1 === x0 ? gutter : gutter + ((x - x0) / (x1 - x0)) * (width - gutter));
  const Y = (y) => height - bottom - (Math.min(y, top) / top) * (height - bottom - 12);
  const floor = height - bottom;

  for (const fraction of [0, 0.25, 0.5, 0.75, 1]) {
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

  for (const fraction of [0, 0.5, 1]) {
    const text = document.createElementNS(SVG, 'text');
    text.setAttribute('x', gutter - 8);
    text.setAttribute('y', Y(top * fraction) + 4);
    text.setAttribute('text-anchor', 'end');
    text.setAttribute('fill', 'var(--secondary-text-color)');
    text.setAttribute('font-size', '11');
    text.textContent = fraction ? `${axisLabel(top * fraction)}${unit}` : '0';
    svg.appendChild(text);
  }
  // The times along the bottom, on the clock's own marks.
  const inside = ticks.filter((tick) => tick.x >= x0 && tick.x <= x1);
  inside.forEach((tick, index) => {
    const text = document.createElementNS(SVG, 'text');
    const at = X(tick.x);
    text.setAttribute('x', at);
    text.setAttribute('y', height - 4);
    text.setAttribute('text-anchor', at < gutter + 20 ? 'start' : at > width - 20 ? 'end' : 'middle');
    text.setAttribute('fill', 'var(--secondary-text-color)');
    text.setAttribute('font-size', '11');
    text.textContent = tick.text;
    if (index || inside.length === 1 || at > gutter + 4) svg.appendChild(text);
  });

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
