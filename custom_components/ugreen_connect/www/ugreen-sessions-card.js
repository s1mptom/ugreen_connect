/* The charges that have finished, newest first.
 *
 * The other half of the ports card: that one says what is charging now, this
 * one what already has, so between them a session is on screen once -- on its
 * port while it runs, here once it is over.
 *
 * One row per port, since the charger keeps each port's last finished session
 * and not a history of them.
 *
 * Config:
 *   type: custom:ugreen-sessions-card
 *   device_id: <the charger>        # optional if only one charger is set up
 *   title: Finished recently        # optional
 *   limit: 4                        # rows at most; default 4
 */

import {
  SERIES, SHARED_CSS, applyTheme, defineCard, duration, mount, num, ports, timeOf, translator,
} from './ugreen-ui.js';

const TEXT = {
  en: {
    title: 'Finished recently', nothing: 'Nothing has finished charging yet.',
    today: 'Today {time}', yesterday: 'Yesterday',
    noPorts: 'No charger entities found. Set device_id in the card config.',
  },
  de: {
    title: 'Zuletzt beendet', nothing: 'Noch nichts fertig geladen.',
    today: 'Heute {time}', yesterday: 'Gestern',
    noPorts: 'Keine Entitäten gefunden. device_id in der Kartenkonfiguration setzen.',
  },
  ru: {
    title: 'Недавно закончились', nothing: 'Ни одна зарядка ещё не закончилась.',
    today: 'Сегодня {time}', yesterday: 'Вчера',
    noPorts: 'Сущности не найдены. Укажите device_id в настройках карточки.',
  },
};

const STYLE = `
  ${SHARED_CSS}
  ha-card { padding: 14px 18px 8px; box-sizing: border-box; height: 100%; }
  h2 { margin: 0 0 6px; }
  /* Numbers in columns of their own width, so on a wide card they stay by
   * their port instead of spreading out to the edges. */
  .row { display: grid; grid-template-columns: 38px 84px 92px 72px minmax(0, 1fr);
         align-items: center; height: 30px; border-top: 1px solid var(--divider-color); font-size: 13px;
         cursor: pointer; background: none; border-left: none; border-right: none; border-bottom: none;
         font-family: inherit; color: inherit; width: 100%; padding: 0; text-align: left; }
  .row:hover { background: var(--secondary-background-color); }
  .row:focus-visible { outline: 2px solid var(--primary-color); outline-offset: -2px; }
  .port { display: flex; align-items: center; gap: 6px; font-weight: 500; }
  .port i { width: 7px; height: 7px; border-radius: 50%; flex: none; }
  .num { text-align: right; white-space: nowrap; }
  .energy { font-weight: 500; }
  .muted { color: var(--secondary-text-color); }
  .empty { font-size: 13px; color: var(--secondary-text-color); padding: 6px 0 10px; }
  /* Narrow, the charge goes -- and its column with it, or the rest slide one
   * column left and run into each other. */
  @container (max-width: 400px) {
    .charge { display: none; }
    .row { grid-template-columns: 34px 80px 64px minmax(0, 1fr); }
  }
  :host { display: block; container-type: inline-size; }
`;

class UgreenSessionsCard extends HTMLElement {
  static getStubConfig() { return { device_id: '' }; }

  setConfig(config) {
    this._config = config || {};
    this._built = false;
    if (this.shadowRoot) this.shadowRoot.innerHTML = '';
  }

  set hass(hass) {
    this._hass = hass;
    this._t = translator(TEXT, hass);
    applyTheme(this, hass);
    this._build();
    this._sync();
  }

  getCardSize() { return 3; }

  _build() {
    if (this._built) return;
    this._built = true;
    this._root = mount(this, `
      <ha-card>
        <style>${STYLE}</style>
        <h2 class="u-card-title"></h2>
        <div class="rows"></div>
      </ha-card>
    `);
    this._root.querySelector('h2').textContent = this._config.title || this._t('title');
    this._rows = this._root.querySelector('.rows');
  }

  /* When it ended, the way a person says it: the time today, the day before,
   * the date further back. */
  _when(at) {
    const lang = this._hass?.locale?.language || undefined;
    const now = new Date();
    const midnight = new Date(now); midnight.setHours(0, 0, 0, 0);
    const time = timeOf(this._hass, at);
    if (at >= midnight) return this._t('today', { time });
    if (at >= midnight - 86400000) return this._t('yesterday');
    return at.toLocaleDateString(lang, { day: 'numeric', month: 'short' });
  }

  _sync() {
    if (!this._hass) return;
    const found = ports(this._hass, this._config.device_id);
    const limit = Number(this._config.limit) || 4;
    const rows = found
      .map((port, index) => {
        const event = this._hass.states[port.event];
        if (!event || event.attributes.event_type !== 'ended') return null;
        const at = new Date(event.state);
        return Number.isNaN(at.valueOf()) ? null : { port, index, event, at };
      })
      .filter(Boolean)
      .sort((a, b) => b.at - a.at)
      .slice(0, limit);

    if (!rows.length) {
      const empty = document.createElement('div');
      empty.className = 'empty';
      empty.textContent = found.length ? this._t('nothing') : this._t('noPorts');
      this._rows.replaceChildren(empty);
      return;
    }

    this._rows.replaceChildren(...rows.map(({ port, index, event, at }) => {
      const wh = Number(event.attributes.energy_wh) || 0;
      // The milliamp-hours live on the session sensor, which holds the latest
      // session only: right for this row while no newer one has started over
      // it, and a dash once one has, rather than the wrong session's figure.
      const energy = this._hass.states[`${port.base}_session_energy`];
      const same = energy?.attributes?.charging === false
        && Math.abs((parseFloat(energy.state) || 0) - wh) < 0.01;
      const mah = same ? num(this._hass, `${port.base}_session_charge`, NaN) : NaN;
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'row';
      row.innerHTML = `
        <span class="port"><i style="background: ${SERIES[index % SERIES.length]}"></i><span class="n"></span></span>
        <span class="num energy">${wh.toFixed(1)} Wh</span>
        <span class="num muted charge">${Number.isFinite(mah) ? `${Math.round(mah)} mAh` : '—'}</span>
        <span class="num muted">${duration(Number(event.attributes.duration) || 0)}</span>
        <span class="num muted"></span>`;
      row.querySelector('.n').textContent = port.name;
      row.lastElementChild.textContent = this._when(at);
      row.addEventListener('click', () => this.dispatchEvent(new CustomEvent('hass-more-info', {
        detail: { entityId: `${port.base}_session_energy` }, bubbles: true, composed: true,
      })));
      return row;
    }));
  }
}

defineCard('ugreen-sessions-card', UgreenSessionsCard, {
  name: 'UGREEN Finished sessions',
  description: 'The charges that have finished, newest first, one row per port',
});
