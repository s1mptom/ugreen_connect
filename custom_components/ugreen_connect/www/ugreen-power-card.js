/* What the charger has been delivering, drawn per port.
 *
 * The built-in history graph draws one line per entity and a legend of entity
 * names; a charger wants its total filled in behind the ports that make it up,
 * and port names short enough to sit in a row. The history comes from the
 * recorder over the same websocket the rest of the frontend uses.
 *
 * Config:
 *   type: custom:ugreen-power-card
 *   device_id: <the charger>        # optional if only one charger is set up
 *   title: Power                    # optional
 *   hours: 3                        # the range shown first; default 3
 *   ranges: [1, 3, 24]              # the buttons; [] hides them
 *   ports: true                     # per-port series; default true
 */

import {
  mount, SERIES, SHARED_CSS, applyTheme, areaChart, bucket, defineCard, findOne, num, ports,
  timeOf, timeOptions, translator,
} from './ugreen-ui.js';

const TEXT = {
  en: {
    title: 'Power',
    total: 'Total',
    hour: '{n} h',
    day: '24 h',
    noData: 'No history yet — the recorder has nothing for this range.',
    noDevice: 'No charger entities found. Set device_id in the card config.',
  },
  de: {
    title: 'Leistung',
    total: 'Gesamt',
    hour: '{n} h',
    day: '24 h',
    noData: 'Noch kein Verlauf — der Recorder hat für diesen Zeitraum nichts.',
    noDevice: 'Keine Entitäten gefunden. device_id in der Kartenkonfiguration setzen.',
  },
  ru: {
    title: 'Мощность',
    total: 'Всего',
    hour: '{n} ч',
    day: '24 ч',
    noData: 'Истории пока нет — за этот период рекордер ничего не отдал.',
    noDevice: 'Сущности не найдены. Укажите device_id в настройках карточки.',
  },
};

const STYLE = `
  ${SHARED_CSS}
  ha-card { height: 100%; box-sizing: border-box; }
  .body { padding: 16px 18px 14px; display: flex; flex-direction: column; gap: 10px; height: 100%;
          box-sizing: border-box; }
  .head { display: flex; align-items: center; gap: 10px; }
  .head h2 { flex-grow: 1; }
  /* The chart takes the room the card has, and is drawn to it; its box is
   * placed over that room rather than inside it, so drawing it cannot make the
   * room bigger and set off another draw. */
  .chart { position: relative; flex: 1 1 0; min-height: 210px; }
  .chart .u-chart { position: absolute; inset: 0; }
  .legend { display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: 12px; }
  .legend button { font: inherit; font-size: 12px; display: inline-flex; align-items: center; gap: 7px;
                   background: none; border: none; padding: 0; cursor: pointer; color: var(--primary-text-color); }
  .legend .key { width: 14px; height: 2px; border-radius: 1px; flex: none; }
  .legend span { color: var(--secondary-text-color); }
  .legend b { font-weight: 500; }
`;

class UgreenPowerCard extends HTMLElement {
  static getStubConfig() { return { device_id: '', hours: 3 }; }

  setConfig(config) {
    this._config = config || {};
    this._built = false;
    this._hours = Number(config?.hours) || 3;
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

  getCardSize() { return 6; }

  _build() {
    if (this._built) return;
    this._built = true;
    this._root = mount(this, `
      <ha-card>
        <style>${STYLE}</style>
        <div class="body">
          <div class="head">
            <h2 class="u-card-title">${this._config.title || this._t('title')}</h2>
            <div class="ranges u-seg small" role="radiogroup"></div>
          </div>
          <div class="chart"></div>
          <div class="legend"></div>
          <div class="u-empty" hidden></div>
        </div>
      </ha-card>
    `);
    this._els = {
      ranges: this._root.querySelector('.ranges'),
      chart: this._root.querySelector('.chart'),
      legend: this._root.querySelector('.legend'),
      empty: this._root.querySelector('.u-empty'),
    };
    this._buildRanges();

    // A poll lands every five seconds and redraws the chart, which throws away
    // the hairline and the readout under the pointer -- the value someone is
    // in the middle of reading. So the chart holds still while it is being
    // read, and catches up when the pointer leaves.
    this._els.chart.addEventListener('pointerenter', () => { this._held = true; });
    this._els.chart.addEventListener('pointerleave', () => {
      this._held = false;
      this._sync();
    });

    // Redrawn when its room changes -- a window resized, a sidebar opened --
    // since the chart is drawn to the size it has rather than stretched.
    this._size = [0, 0];
    new ResizeObserver(() => {
      const size = [this._els.chart.clientWidth, this._els.chart.clientHeight];
      if (Math.abs(size[0] - this._size[0]) < 2 && Math.abs(size[1] - this._size[1]) < 2) return;
      this._size = size;
      if (this._last) this._draw(...this._last);
    }).observe(this._els.chart);
  }

  _buildRanges() {
    const ranges = this._config.ranges ?? [1, 3, 24];
    this._els.ranges.replaceChildren(...ranges.map((hours) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = hours === 24 ? this._t('day') : this._t('hour', { n: hours });
      button.setAttribute('role', 'radio');
      button.setAttribute('aria-checked', String(hours === this._hours));
      button.addEventListener('click', () => {
        this._hours = hours;
        this._asked = 0;
        for (const other of this._els.ranges.children) {
          other.setAttribute('aria-checked', String(other === button));
        }
        this._sync();
      });
      return button;
    }));
  }

  _sync() {
    if (!this._hass) return;
    const found = ports(this._hass, this._config.device_id);
    const total = findOne(this._hass, this._config.device_id, 'sensor', '_total_power');
    if (!found.length && !total) {
      this._els.empty.hidden = false;
      this._els.empty.textContent = this._t('noDevice');
      return;
    }
    this._fetch(found, total);
    this._draw(found, total);
  }

  /* The recorder, at most twice a minute.
   *
   * Every state change of every port would otherwise redraw the chart, and the
   * poll interval is five seconds. */
  _fetch(found, total) {
    const now = Date.now();
    if (now - this._asked < 30000) return;
    this._asked = now;
    this._from = now - this._hours * 3600 * 1000;
    const ids = [total, ...(this._config.ports === false ? [] : found.map((p) => p.id))]
      .filter(Boolean);
    if (!ids.length) return;
    this._hass.callWS({
      type: 'history/history_during_period',
      start_time: new Date(now - this._hours * 3600 * 1000).toISOString(),
      end_time: new Date(now).toISOString(),
      entity_ids: ids,
      minimal_response: true,
      no_attributes: true,
      significant_changes_only: false,
    }).then((result) => {
      this._history = result || {};
      this._draw(found, total);
    }).catch(() => {
      this._history = {};
      this._draw(found, total);
    });
  }

  _points(entityId) {
    const rows = this._history?.[entityId] || [];
    const points = rows
      .map((row) => ({ x: (row.lu ?? row.last_updated ?? 0) * 1000, y: parseFloat(row.s ?? row.state) }))
      .filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y) && point.x > 0);
    // The window, not the readings, decides the axis: a port that reported once
    // an hour ago still holds that value across the rest of the chart.
    return bucket(points, 90, { from: this._from ?? points[0]?.x, to: Date.now() });
  }

  /* The clock's own marks across the window: quarter hours over one hour,
   * hours over three, six-hour marks over a day. */
  _ticks(from, to) {
    const step = this._hours <= 1 ? 15 * 60000 : this._hours <= 3 ? 3600000 : 6 * 3600000;
    const offset = -new Date(from).getTimezoneOffset() * 60000;
    const out = [];
    for (let at = Math.ceil((from + offset) / step) * step - offset; at <= to; at += step) {
      out.push({ x: at, text: timeOf(this._hass, at) });
    }
    return out;
  }

  _draw(found, total) {
    this._last = [found, total];
    if (!this._history) return;
    if (this._held && this._els.chart.firstChild) return;
    const series = [];
    if (total) {
      // The total in the text's own colour, not the theme's accent: the accent
      // is a blue, and so is the first port.
      series.push({
        name: this._t('total'),
        color: 'var(--secondary-text-color)',
        points: this._points(total),
        fill: 0.08,
        entity: total,
      });
    }
    if (this._config.ports !== false) {
      found.forEach((port, index) => {
        const points = this._points(port.id);
        // A port whose average rounds to nothing can still have had a peak.
        if (points.some((point) => (point.hi ?? point.y) > 0)) {
          series.push({
            name: port.name,
            color: SERIES[index % SERIES.length],
            points,
            fill: 0.06,
            width: 2,
            entity: port.id,
          });
        }
      });
    }

    const drawn = series.filter((one) => one.points.length > 1);
    this._els.empty.hidden = drawn.length > 0;
    this._els.empty.textContent = drawn.length ? '' : this._t('noData');
    this._els.chart.replaceChildren(
      areaChart(drawn, {
        width: Math.max(240, Math.round(this._els.chart.clientWidth) || 620),
        height: Math.max(180, Math.round(this._els.chart.clientHeight) || 210),
        gutter: 46,
        unit: ' W',
        ticks: this._ticks(this._from ?? Date.now() - this._hours * 3600000, Date.now()),
        // Over a day the hour is what tells two readings apart; over an hour it
        // is the minute. Both are what the viewer's own locale calls them.
        label: (at) => new Date(at).toLocaleTimeString(this._hass?.locale?.language || undefined, {
          ...timeOptions(this._hass),
          ...(this._hours >= 24 ? { weekday: 'short' } : {}),
        }),
      }),
    );
    const keys = drawn.map((one) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.innerHTML = `<i class="key" style="background: ${one.color}"></i><span></span><b>${
        num(this._hass, one.entity).toFixed(1)} W</b>`;
      button.querySelector('span').textContent = one.name;
      button.addEventListener('click', () => this.dispatchEvent(new CustomEvent('hass-more-info', {
        detail: { entityId: one.entity }, bubbles: true, composed: true,
      })));
      return button;
    });
    this._els.legend.replaceChildren(...keys);
  }
}

defineCard('ugreen-power-card', UgreenPowerCard, {
  name: 'UGREEN Power',
  description: 'The charger\'s power over time, per port, from the recorder',
});
