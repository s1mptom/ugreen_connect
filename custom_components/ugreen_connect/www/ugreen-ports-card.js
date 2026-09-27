/* The charger's front panel: one tile per port, in the order they sit on it.
 *
 * Eight ports times six readings is forty-eight entities, and what anyone asks
 * of them is which ports are charging and how fast. So a port that is charging
 * is a tile with its watts in large type, what it is running at, a line of the
 * last hour, and what this charge has put in so far; a port that is not is the
 * same tile, quiet, saying so. Under the priority mode, the ports it charges
 * first say so beside their name.
 *
 * Only what is happening now is here. The session sensors keep the last
 * session's figures after it ends, and shown on an idle port those figures
 * repeat what the finished-sessions card already says, under a heading that
 * claims they are current.
 *
 * Config:
 *   type: custom:ugreen-ports-card
 *   device_id: <the charger>        # optional if only one charger is set up
 *   columns: 8                      # tiles per row at full width; fewer on a
 *                                   # narrow card whatever this says
 */

import {
  SERIES, SHARED_CSS, applyTheme, bucket, defineCard, duration, mount, num, ports, translator,
} from './ugreen-ui.js';

const TEXT = {
  en: {
    energy: 'Energy', charge: 'Charge', time: 'Charging for', first: 'First', firstTitle: 'Charged first',
    idle: 'Not charging', cable: 'Cable only', onPort: '{v} V on the port',
    noPorts: 'No charger entities found. Set device_id in the card config.',
  },
  de: {
    energy: 'Energie', charge: 'Ladung', time: 'Lädt seit', first: 'Zuerst', firstTitle: 'Zuerst geladen',
    idle: 'Lädt nicht', cable: 'Nur Kabel', onPort: '{v} V am Anschluss',
    noPorts: 'Keine Entitäten gefunden. device_id in der Kartenkonfiguration setzen.',
  },
  ru: {
    energy: 'Энергия', charge: 'Заряд', time: 'Заряжает', first: 'Первый', firstTitle: 'Заряжается первым',
    idle: 'Не заряжает', cable: 'Только кабель', onPort: 'на порту {v} В',
    noPorts: 'Сущности не найдены. Укажите device_id в настройках карточки.',
  },
};

// How much of the past a tile's line covers, and how finely.
const SPARK_MINUTES = 60;
const SPARK_POINTS = 30;

const STYLE = `
  ${SHARED_CSS}
  :host { display: block; container-type: inline-size; }
  .tiles { display: grid; grid-template-columns: repeat(var(--cols, 8), minmax(0, 1fr)); gap: 10px; }
  .tile { font: inherit; text-align: left; box-sizing: border-box; min-height: 232px;
          padding: 14px 14px 12px; border-radius: var(--ha-card-border-radius, 12px);
          border: 1px solid var(--divider-color); color: var(--primary-text-color);
          background: color-mix(in srgb, var(--ha-card-background, var(--card-background-color)) 55%, var(--primary-background-color));
          display: flex; flex-direction: column; cursor: pointer; }
  .tile:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
  .tile.on { background: var(--ha-card-background, var(--card-background-color)); border-color: var(--port); }
  .head { display: flex; align-items: center; gap: 7px; height: 20px; }
  .dot { width: 9px; height: 9px; border-radius: 50%; flex: none;
         background: color-mix(in srgb, var(--secondary-text-color) 45%, transparent); }
  .on .dot { background: var(--port); }
  .name { font-size: 15px; font-weight: 500; color: var(--secondary-text-color); }
  .on .name { color: var(--primary-text-color); }
  .grow { flex-grow: 1; }
  .proto { font-size: 12px; color: var(--secondary-text-color); }
  .first { font-size: 11px; line-height: 16px; padding: 0 6px; border-radius: 9px; color: var(--primary-color);
           border: 1px solid color-mix(in srgb, var(--primary-color) 55%, transparent); }
  .watts { display: flex; align-items: baseline; gap: 5px; margin-top: 12px; }
  .watts b { font-size: 30px; font-weight: 500; line-height: 1; }
  .watts span { font-size: 14px; color: var(--secondary-text-color); }
  .electric { display: flex; gap: 14px; margin-top: 6px; font-size: 12px; color: var(--secondary-text-color); }
  svg.spark { display: block; width: 100%; height: 30px; margin-top: 10px; overflow: visible; }
  .figures { display: flex; flex-direction: column; gap: 4px; border-top: 1px solid var(--divider-color);
             padding-top: 9px; font-size: 12px; }
  .figures div { display: flex; justify-content: space-between; gap: 8px; }
  .figures span { color: var(--secondary-text-color); }
  .note { font-size: 13px; color: var(--secondary-text-color); }
  .detail { font-size: 12px; color: var(--secondary-text-color); margin-top: 3px; min-height: 16px; }
  .empty { color: var(--secondary-text-color); padding: 14px 0; }
  /* Narrower than eight tiles can hold: four, then two. At two a pair of idle
   * tiles folds down to its note, since there is nothing for the height to
   * hold, while a charging tile beside an idle one keeps the row's height. */
  @container (max-width: 1100px) { .tiles { --cols: 4; } }
  @container (max-width: 560px) {
    .tiles { --cols: 2; }
    .tile { min-height: 0; }
    .tile .watts b { font-size: 26px; }
    svg.spark { display: none; }
  }
`;

class UgreenPortsCard extends HTMLElement {
  static getStubConfig() { return { device_id: '' }; }

  setConfig(config) {
    this._config = config || {};
    this._built = false;
    this._history = null;
    this._asked = 0;
    if (this.shadowRoot) this.shadowRoot.innerHTML = '';
  }

  set hass(hass) {
    this._hass = hass;
    this._t = translator(TEXT, hass);
    applyTheme(this, hass);
    this._build();
    this._sync();
  }

  getCardSize() { return 5; }

  _build() {
    if (this._built) return;
    this._built = true;
    this._root = mount(this, `
      <style>${STYLE}</style>
      <div class="tiles" role="list"></div>
      <div class="empty" hidden>${this._t('noPorts')}</div>
    `);
    this._els = { tiles: this._root.querySelector('.tiles'), empty: this._root.querySelector('.empty') };
    const cols = Number(this._config.columns);
    if (cols > 0) this._els.tiles.style.setProperty('--cols', String(cols));
  }

  _sync() {
    if (!this._hass) return;
    const found = ports(this._hass, this._config.device_id);
    this._els.empty.hidden = found.length > 0;
    this._fetch(found);
    this._els.tiles.replaceChildren(...found.map((port, index) => this._tile(port, SERIES[index % SERIES.length])));
  }

  /* The last hour of every port, once a minute: the line is a shape, not a
   * reading, and it does not need the poll's five seconds. */
  _fetch(found) {
    const now = Date.now();
    if (!found.length || now - this._asked < 60000) return;
    this._asked = now;
    this._from = now - SPARK_MINUTES * 60000;
    this._hass.callWS({
      type: 'history/history_during_period',
      start_time: new Date(this._from).toISOString(),
      end_time: new Date(now).toISOString(),
      entity_ids: found.map((p) => p.id),
      minimal_response: true,
      no_attributes: true,
      significant_changes_only: false,
    }).then((result) => { this._history = result || {}; this._sync(); })
      .catch(() => { this._history = {}; });
  }

  _spark(port) {
    const rows = this._history?.[port.id] || [];
    const points = rows
      .map((row) => ({ x: (row.lu ?? row.last_updated ?? 0) * 1000, y: parseFloat(row.s ?? row.state) }))
      .filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y) && p.x > 0);
    const cells = bucket(points, SPARK_POINTS, { from: this._from, to: Date.now() });
    if (cells.length < 2) return '';
    // Scaled to the port itself: the line is the shape of this charge, and the
    // number above it already says how big.
    const top = Math.max(...cells.map((c) => c.y), 0.1);
    const x0 = cells[0].x;
    const span = (cells[cells.length - 1].x - x0) || 1;
    const X = (x) => ((x - x0) / span) * 100;
    const Y = (y) => 29 - (y / top) * 26;
    const line = cells.map((c, i) => `${i ? 'L' : 'M'}${X(c.x).toFixed(2)} ${Y(c.y).toFixed(2)}`).join(' ');
    return `<svg class="spark" viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true">
      <path d="${line} L100 30 L0 30 Z" fill="var(--port)" fill-opacity="0.16"></path>
      <path d="${line}" fill="none" stroke="var(--port)" stroke-width="1.5" stroke-linejoin="round"
        vector-effect="non-scaling-stroke"></path></svg>`;
  }

  _tile(port, colour) {
    const s = this._hass.states;
    const watts = num(this._hass, port.id);
    const volts = num(this._hass, `${port.base}_voltage`);
    const amps = num(this._hass, `${port.base}_current`);
    const protocol = s[`${port.base}_protocol`]?.state;
    const drawing = s[port.charging]?.state === 'on';
    const energy = s[`${port.base}_session_energy`];
    const inSession = drawing && energy?.attributes?.charging !== false;
    // On only while `priority` runs: the switch is unavailable under any other
    // mode, and unavailable is not on.
    const first = s[`switch.${port.base.split('.')[1]}_charged_first`]?.state === 'on';

    const tile = document.createElement('button');
    tile.type = 'button';
    tile.setAttribute('role', 'listitem');
    tile.className = drawing ? 'tile on' : 'tile';
    tile.style.setProperty('--port', colour);
    const proto = protocol && protocol !== 'none' ? protocol : '';

    let body;
    if (drawing) {
      const wh = parseFloat(energy?.state);
      const mah = num(this._hass, `${port.base}_session_charge`, NaN);
      const seconds = Number(energy?.attributes?.duration) || 0;
      body = `
        <div class="watts"><b>${watts.toFixed(1)}</b><span>W</span></div>
        <div class="electric"><span>${volts.toFixed(1)} V</span><span>${amps.toFixed(2)} A</span></div>
        ${this._spark(port)}
        <div class="grow"></div>
        ${inSession ? `<div class="figures">
          <div><span>${this._t('energy')}</span>${Number.isFinite(wh) ? `${wh.toFixed(1)} Wh` : '—'}</div>
          <div><span>${this._t('charge')}</span>${Number.isFinite(mah) ? `${Math.round(mah)} mAh` : '—'}</div>
          <div><span>${this._t('time')}</span>${seconds ? duration(seconds) : '—'}</div>
        </div>` : ''}`;
    } else {
      const cable = volts > 0.5;
      body = `
        <div class="grow"></div>
        <div class="note">${cable ? this._t('cable') : this._t('idle')}</div>
        <div class="detail">${cable ? this._t('onPort', { v: volts.toFixed(1) }) : ''}</div>`;
    }
    tile.innerHTML = `
      <div class="head"><span class="dot"></span><span class="name"></span><span class="grow"></span>
        ${first ? `<span class="first" title="${this._t('firstTitle')}">${this._t('first')}</span>` : ''}
        <span class="proto"></span></div>
      ${body}`;
    tile.querySelector('.name').textContent = port.name;
    tile.querySelector('.proto').textContent = proto;
    tile.setAttribute('aria-label', drawing
      ? `${port.name}, ${watts.toFixed(1)} W${proto ? `, ${proto}` : ''}`
      : `${port.name}, ${volts > 0.5 ? this._t('cable') : this._t('idle')}`);
    tile.addEventListener('click', () => this.dispatchEvent(new CustomEvent('hass-more-info', {
      detail: { entityId: port.id }, bubbles: true, composed: true,
    })));
    return tile;
  }
}

defineCard('ugreen-ports-card', UgreenPortsCard, {
  name: 'UGREEN Ports',
  description: "The charger's front panel: one tile per port, with what it is charging now",
});
