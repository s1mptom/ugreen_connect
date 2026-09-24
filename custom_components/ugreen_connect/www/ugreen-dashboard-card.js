/* The whole charger on one screen, laid out here rather than by the dashboard.
 *
 * Home Assistant's sections put cards in columns of equal width and let them
 * grow as they like; a charger's screen wants the opposite -- a strip across
 * the top, a tall table beside a chart, a short row along the bottom, all of it
 * sized to what it holds. So this card owns the layout and hands the pieces to
 * the cards that already draw them.
 *
 * Put it in a panel view (`type: panel`) and it fills the screen; anywhere else
 * it behaves like a very wide card. Below about 1100px it folds to one column,
 * and the pieces are the same cards anyone can use on their own.
 *
 * Config:
 *   type: custom:ugreen-dashboard-card
 *   device_id: <the charger>        # optional if only one charger is set up
 *   max_power: 300                  # what the bars scale to
 *   hours: 3                        # the chart's range to start on
 *   period: week                    # the energy card's period
 */

import {
  SHARED_CSS, defineCard, mount, num, translator,
} from './ugreen-ui.js';

// The pieces this lays out. Imported rather than assumed: a dashboard can load
// this module first, and an element that is not defined yet is an unknown tag
// with no setConfig on it.
import './ugreen-charger-card.js';
import './ugreen-energy-card.js';
import './ugreen-ports-card.js';
import './ugreen-power-card.js';
import './ugreen-wallpaper-card.js';

const TEXT = {
  en: {
    limits: 'Custom mode limits',
    limitsWhy: 'Shown only while the charger runs Custom; the sensors read nothing under a preset.',
    noDevice: 'No charger entities found. Set device_id in the card config.',
  },
  de: {
    limits: 'Grenzen der Port-Anpassung',
    limitsWhy: 'Nur sichtbar, solange die Port-Anpassung läuft; unter einer Voreinstellung lesen die Sensoren nichts.',
    noDevice: 'Keine Entitäten gefunden. device_id in der Kartenkonfiguration setzen.',
  },
  ru: {
    limits: 'Лимиты пользовательского режима',
    limitsWhy: 'Видны, только пока зарядка в этом режиме: под предустановленным сенсоры ничего не читают.',
    noDevice: 'Сущности не найдены. Укажите device_id в настройках карточки.',
  },
};

const STYLE = `
  ${SHARED_CSS}
  :host { display: block; padding: 20px; box-sizing: border-box; }
  /* Three rows, each with its own columns, rather than one grid for the lot:
   * the rows share nothing but their gutter, and a single grid would have had
   * the ports table and the screen card lining up on a column edge neither of
   * them wants. Widths are the design's, in pixels, because the tables and the
   * chart were drawn against them; everything else takes the space left. */
  .screen { display: flex; flex-direction: column; gap: 14px; min-height: 100%; }
  .row { display: grid; gap: 14px; align-items: stretch; }
  .row1 { grid-template-columns: 300px minmax(0, 1fr); }
  .row2 { grid-template-columns: minmax(0, 720px) minmax(0, 1fr); flex-grow: 1; }
  .row3 { grid-template-columns: 340px minmax(0, 1fr); }
  .row3.with-limits { grid-template-columns: 340px minmax(0, 1fr) 300px; }
  .col { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
  .col .power { flex-grow: 1; }
  .limits ha-card { padding: 14px 16px; height: 100%; box-sizing: border-box; }
  .limits h3 { margin: 0 0 4px; font-size: 11px; font-weight: 400; text-transform: uppercase;
               letter-spacing: .10em; color: var(--state-icon-active-color, var(--primary-color)); }
  .limits .why { font-size: 12px; color: var(--secondary-text-color); margin-bottom: 8px; }
  .limits .grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; }
  .limits .cell { background: var(--secondary-background-color); border-radius: 8px; padding: 6px 8px; }
  .limits .cell .name { font-size: 11px; color: var(--secondary-text-color); }
  .limits .cell .value { font-size: 14px; }
  @media (max-width: 1100px) {
    :host { padding: 12px; }
    .row1, .row2, .row3, .row3.with-limits { grid-template-columns: minmax(0, 1fr); }
  }
`;

class UgreenDashboardCard extends HTMLElement {
  static getStubConfig() { return { device_id: '' }; }

  setConfig(config) {
    this._config = config || {};
    this._built = false;
    if (this.shadowRoot) this.shadowRoot.innerHTML = '';
  }

  set hass(hass) {
    this._hass = hass;
    this._t = translator(TEXT, hass);
    this._build();
    for (const card of this._cards || []) {
      if (typeof card.setConfig === 'function') card.hass = hass;
    }
    this._syncLimits();
  }

  getCardSize() { return 20; }

  /* One child card, configured from this card's own options.
   *
   * Upgraded by hand if need be: the imports above define every tag used here,
   * but a browser that has not run them yet hands back an unknown element, and
   * an unknown element has no setConfig. */
  _card(tag, extra) {
    const card = document.createElement(tag);
    const config = {
      type: `custom:${tag}`,
      device_id: this._config.device_id,
      max_power: this._config.max_power,
      ...extra,
    };
    const apply = () => {
      customElements.upgrade(card);
      card.setConfig(config);
      if (this._hass) card.hass = this._hass;
    };
    if (customElements.get(tag)) apply();
    else customElements.whenDefined(tag).then(apply);
    return card;
  }

  _build() {
    if (this._built) return;
    this._built = true;
    this._root = mount(this, `
      <style>${STYLE}</style>
      <div class="screen">
        <div class="row row1"></div>
        <div class="row row2"></div>
        <div class="row row3"></div>
      </div>
    `);
    const row1 = this._root.querySelector('.row1');
    const row2 = this._root.querySelector('.row2');
    this._row3 = this._root.querySelector('.row3');

    const summary = this._card('ugreen-charger-card', { part: 'summary' });
    const controls = this._card('ugreen-charger-card', { part: 'controls' });
    row1.append(summary, controls);

    const ports = this._card('ugreen-ports-card', { sessions: false });
    ports.classList.add('ports');
    const power = this._card('ugreen-power-card', { hours: this._config.hours ?? 3 });
    power.classList.add('power');
    const sessions = this._card('ugreen-ports-card', { ports: false });
    sessions.classList.add('sessions');
    const column = document.createElement('div');
    column.className = 'col';
    column.append(power, sessions);
    row2.append(ports, column);

    const wallpaper = this._card('ugreen-wallpaper-card', {});
    const energy = this._card('ugreen-energy-card', { period: this._config.period ?? 'week' });

    const limits = document.createElement('div');
    limits.className = 'limits';
    limits.hidden = true;
    limits.innerHTML = `
      <ha-card>
        <h3>${this._t('limits')}</h3>
        <div class="why">${this._t('limitsWhy')}</div>
        <div class="grid"></div>
      </ha-card>
    `;
    this._row3.append(wallpaper, energy, limits);

    this._cards = [summary, controls, ports, power, sessions, wallpaper, energy];
    this._limits = limits;
  }

  /* The six group limits, while the charger is in the mode that has them.
   *
   * Their sensors are unavailable under a preset, which is what decides
   * whether this shows at all -- no need to read the mode itself. */
  _syncLimits() {
    if (!this._limits) return;
    const groups = Object.keys(this._hass.states)
      .filter((id) => id.startsWith('sensor.') && id.endsWith('_custom_mode_limit')
        && (!this._config.device_id
          || this._hass.states[id].attributes.device_id === this._config.device_id
          || id.includes('ugreen')))
      .sort();
    const live = groups.filter((id) => !['unavailable', 'unknown'].includes(this._hass.states[id].state));
    this._limits.hidden = live.length === 0;
    this._row3.classList.toggle('with-limits', live.length > 0);
    if (!live.length) return;
    this._limits.querySelector('.grid').replaceChildren(...live.map((id) => {
      const state = this._hass.states[id];
      const name = (state.attributes.friendly_name || id)
        .replace(/\s*custom[- ]mode limit$/i, '').split(' ').pop();
      const cell = document.createElement('div');
      cell.className = 'cell';
      cell.innerHTML = `<div class="name">${name}</div><div class="value">${
        num(this._hass, id)} ${state.attributes.unit_of_measurement || 'W'}</div>`;
      return cell;
    }));
  }
}

defineCard('ugreen-dashboard-card', UgreenDashboardCard, {
  name: 'UGREEN Charger dashboard',
  description: 'The whole charger on one screen: the strip, the ports, the chart, the sessions, the screen and the energy',
});
