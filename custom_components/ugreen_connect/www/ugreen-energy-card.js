/* Where the charger's energy went, per port.
 *
 * The statistic card can show one counter; a charger has one per port and the
 * question is always which of them the kilowatt-hours went to. That is a bar
 * chart, and a short one: eight bars, sorted or in the charger's own order.
 *
 * Config:
 *   type: custom:ugreen-energy-card
 *   device_id: <the charger>        # optional if only one charger is set up
 *   title: Energy per port          # optional
 *   period: week                    # day | week | month | all; default week
 *   sort: true                      # biggest first; default false, charger order
 */

import {
  mount, SERIES, SHARED_CSS, defineCard, ports, translator,
} from './ugreen-ui.js';

const TEXT = {
  en: {
    title: 'Energy per port',
    day: 'today',
    week: 'this week',
    month: 'this month',
    all: 'all time',
    feeds: 'feeds the Energy dashboard',
    nothing: 'Nothing recorded for this period yet.',
    noDevice: 'No charger entities found. Set device_id in the card config.',
  },
  de: {
    title: 'Energie je Anschluss',
    day: 'heute',
    week: 'diese Woche',
    month: 'diesen Monat',
    all: 'gesamt',
    feeds: 'speist das Energie-Dashboard',
    nothing: 'Für diesen Zeitraum ist noch nichts aufgezeichnet.',
    noDevice: 'Keine Entitäten gefunden. device_id in der Kartenkonfiguration setzen.',
  },
  ru: {
    title: 'Энергия по портам',
    day: 'сегодня',
    week: 'за неделю',
    month: 'за месяц',
    all: 'за всё время',
    feeds: 'питает панель «Энергия»',
    nothing: 'За этот период пока ничего не записано.',
    noDevice: 'Сущности не найдены. Укажите device_id в настройках карточки.',
  },
};

const STYLE = `
  ${SHARED_CSS}
  ha-card { height: 100%; box-sizing: border-box; }
  .body { padding: 14px 16px; display: flex; flex-direction: column; gap: 8px; height: 100%;
          box-sizing: border-box; }
  .head { display: flex; align-items: baseline; gap: 10px; }
  .head h2 { margin: 0; font-size: 11px; font-weight: 400; text-transform: uppercase;
             letter-spacing: .10em; color: var(--secondary-text-color); flex-grow: 1; }
  .head .period { font-size: 11px; }
  .head .feeds { font-size: 11px; color: var(--disabled-text-color); }
  .bars { display: flex; align-items: flex-end; gap: 10px; height: 84px; flex: none; }
  .bar { flex: 1 1 0; display: flex; flex-direction: column; align-items: center; gap: 5px;
         justify-content: flex-end; height: 100%; cursor: pointer;
         background: none; border: none; padding: 0; font: inherit; }
  .bar .fill { width: 100%; border-radius: 4px 4px 0 0; min-height: 4px; }
  .bar:hover .fill, .bar:focus-visible .fill { filter: brightness(1.12); }
  .bar .name { font-size: 11px; color: var(--secondary-text-color); }
  /* Only the ports that took a charge are labelled: eight numbers, six of them
   * 0.000, is a row of noise around the two that matter. The rest are a hover
   * or a keyboard focus away. */
  .bar .value { font-size: 11px; color: var(--primary-text-color); min-height: 1.25em; }
  .body { position: relative; }
`;

class UgreenEnergyCard extends HTMLElement {
  static getStubConfig() { return { device_id: '', period: 'week' }; }

  setConfig(config) {
    this._config = config || {};
    this._built = false;
    this._totals = null;
    this._asked = 0;
    if (this.shadowRoot) this.shadowRoot.innerHTML = '';
  }

  set hass(hass) {
    this._hass = hass;
    this._t = translator(TEXT, hass);
    this._build();
    this._sync();
  }

  getCardSize() { return 4; }

  _period() {
    const wanted = this._config.period || 'week';
    return ['day', 'week', 'month', 'all'].includes(wanted) ? wanted : 'week';
  }

  _build() {
    if (this._built) return;
    this._built = true;
    this._root = mount(this, `
      <ha-card>
        <style>${STYLE}</style>
        <div class="body">
          <div class="head">
            <h2>${this._config.title || this._t('title')}, ${this._t(this._period())}</h2>
            <span class="feeds">${this._t('feeds')}</span>
          </div>
          <div class="bars"></div>
          <div class="u-empty" hidden></div>
          <div class="u-tip" hidden></div>
        </div>
      </ha-card>
    `);
    this._els = {
      body: this._root.querySelector('.body'),
      tip: this._root.querySelector('.u-tip'),
      bars: this._root.querySelector('.bars'),
      feeds: this._root.querySelector('.feeds'),
      empty: this._root.querySelector('.u-empty'),
    };
  }

  _sync() {
    if (!this._hass) return;
    const found = ports(this._hass, this._config.device_id);
    if (!found.length) {
      this._els.empty.hidden = false;
      this._els.empty.textContent = this._t('noDevice');
      this._els.bars.replaceChildren();
      return;
    }
    this._fetch(found);
    this._draw(found);
  }

  /* Lifetime totals are on the entities; anything shorter is a statistic, so
   * "this week" is the counter's change across the week. Asked for once a
   * minute -- the counters move slowly, and a poll is every five seconds. */
  _fetch(found) {
    if (this._period() === 'all') return;
    const now = Date.now();
    if (now - this._asked < 60000) return;
    this._asked = now;
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    if (this._period() === 'week') {
      const weekday = (start.getDay() + 6) % 7;  // Monday first
      start.setDate(start.getDate() - weekday);
    }
    if (this._period() === 'month') start.setDate(1);
    const ids = found.map((port) => `${port.base}_energy`);
    this._hass.callWS({
      type: 'recorder/statistics_during_period',
      start_time: start.toISOString(),
      statistic_ids: ids,
      period: 'day',
      types: ['change'],
    }).then((result) => {
      this._totals = Object.fromEntries(ids.map((id) => [
        id,
        (result?.[id] || []).reduce((sum, row) => sum + (Number(row.change) || 0), 0),
      ]));
      this._draw(found);
    }).catch(() => {
      this._totals = null;
      this._draw(found);
    });
  }

  _value(port) {
    const id = `${port.base}_energy`;
    if (this._period() === 'all' || !this._totals) {
      return parseFloat(this._hass.states[id]?.state) || 0;
    }
    return this._totals[id] ?? 0;
  }

  /* Every port's reading on hover or focus, including the ones whose bar is a
   * stub -- "nothing this week" is an answer too. */
  _tip(event, name, reading, colour, live) {
    const tip = this._els.tip;
    tip.replaceChildren();
    const row = document.createElement('div');
    row.className = 'line';
    const key = document.createElement('span');
    key.className = 'key';
    key.style.background = live ? colour : 'var(--divider-color)';
    const value = document.createElement('b');
    value.textContent = `${reading} kWh`;
    const label = document.createElement('span');
    label.className = 'name';
    label.textContent = name;
    row.append(key, value, label);
    tip.appendChild(row);
    tip.hidden = false;
    const box = this._els.body.getBoundingClientRect();
    const mark = (event.currentTarget || event.target).getBoundingClientRect();
    const middle = mark.left - box.left + mark.width / 2;
    tip.style.left = `${Math.min(box.width - tip.offsetWidth - 4,
      Math.max(4, middle - tip.offsetWidth / 2))}px`;
    // Above the bar, but never above the bars themselves: pushed any higher it
    // would sit on the heading, and a readout covering the title of the thing
    // it is reading is worse than one a few pixels lower.
    const ceiling = this._els.bars.getBoundingClientRect().top - box.top;
    tip.style.top = `${Math.max(ceiling, mark.top - box.top - tip.offsetHeight - 6)}px`;
  }

  _hideTip() {
    this._els.tip.hidden = true;
  }

  _draw(found) {
    const rows = found.map((port, index) => ({
      port,
      colour: SERIES[index % SERIES.length],
      value: this._value(port),
    }));
    if (this._config.sort) rows.sort((a, b) => b.value - a.value);
    const top = Math.max(...rows.map((row) => row.value), 0);

    this._els.empty.hidden = top > 0;
    this._els.empty.textContent = top > 0 ? '' : this._t('nothing');
    this._els.feeds.hidden = top <= 0;
    this._els.bars.replaceChildren(...rows.map(({ port, colour, value }) => {
      const bar = document.createElement('button');
      bar.type = 'button';
      bar.className = value > 0 ? 'bar' : 'bar zero';
      const height = top > 0 ? Math.max(4, (value / top) * 100) : 4;
      const reading = value >= 0.995 ? value.toFixed(2) : value.toFixed(3);
      bar.innerHTML = `
        <span class="value">${value > 0 ? `${reading} kWh` : ''}</span>
        <span class="fill" style="height: ${height.toFixed(0)}%; background: ${
          value > 0 ? colour : 'var(--divider-color)'}"></span>
        <span class="name">${port.name}</span>
      `;
      const readout = (event) => this._tip(event, port.name, reading, colour, value > 0);
      bar.addEventListener('pointerenter', readout);
      bar.addEventListener('focus', readout);
      bar.addEventListener('pointerleave', () => this._hideTip());
      bar.addEventListener('blur', () => this._hideTip());
      bar.addEventListener('click', () => this.dispatchEvent(new CustomEvent('hass-more-info', {
        detail: { entityId: `${port.base}_energy` }, bubbles: true, composed: true,
      })));
      return bar;
    }));
  }
}

defineCard('ugreen-energy-card', UgreenEnergyCard, {
  name: 'UGREEN Energy',
  description: 'Kilowatt-hours per port, for a day, a week, a month or all time',
});
