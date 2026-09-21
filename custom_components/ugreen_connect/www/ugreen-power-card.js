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
  mount, SERIES, SHARED_CSS, areaChart, bucket, defineCard, findOne, num, ports, translator,
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
  .body { padding: 12px 16px 14px; display: flex; flex-direction: column; gap: 8px; }
  .head { display: flex; align-items: center; gap: 10px; }
  .head h2 { margin: 0; font-size: 1.05em; font-weight: 500; flex-grow: 1; }
  .ranges { display: flex; gap: 4px; }
  .ranges button { font: inherit; font-size: .8em; padding: 4px 9px; cursor: pointer;
                   color: var(--secondary-text-color); background: none;
                   border: 1px solid var(--divider-color); border-radius: 999px; }
  .ranges button[aria-pressed="true"] { color: var(--text-primary-color, #fff);
                   background: var(--state-icon-active-color, var(--primary-color));
                   border-color: var(--state-icon-active-color, var(--primary-color)); }
  .chart { min-height: 150px; }
  .legend { display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: .84em; }
  .legend button { font: inherit; font-size: 1em; display: inline-flex; align-items: center; gap: 6px;
                   background: none; border: none; padding: 0; cursor: pointer;
                   color: var(--secondary-text-color); }
  .legend .swatch { width: 9px; height: 9px; border-radius: 2px; flex: none; }
  .legend b { font-weight: 500; color: var(--primary-text-color); }
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
            <h2>${this._config.title || this._t('title')}</h2>
            <div class="ranges"></div>
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
  }

  _buildRanges() {
    const ranges = this._config.ranges ?? [1, 3, 24];
    this._els.ranges.replaceChildren(...ranges.map((hours) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = hours === 24 ? this._t('day') : this._t('hour', { n: hours });
      button.setAttribute('aria-pressed', String(hours === this._hours));
      button.addEventListener('click', () => {
        this._hours = hours;
        this._asked = 0;
        for (const other of this._els.ranges.children) {
          other.setAttribute('aria-pressed', String(other === button));
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

  _draw(found, total) {
    if (!this._history) return;
    const series = [];
    if (total) {
      series.push({
        name: this._t('total'),
        color: 'var(--state-icon-active-color, var(--primary-color))',
        points: this._points(total),
        fill: 0.18,
        entity: total,
      });
    }
    if (this._config.ports !== false) {
      found.forEach((port, index) => {
        const points = this._points(port.id);
        if (points.some((point) => point.y > 0)) {
          series.push({
            name: port.name,
            color: SERIES[index % SERIES.length],
            points,
            fill: 0.10,
            width: 1.5,
            entity: port.id,
          });
        }
      });
    }

    const drawn = series.filter((one) => one.points.length > 1);
    this._els.empty.hidden = drawn.length > 0;
    this._els.empty.textContent = drawn.length ? '' : this._t('noData');
    this._els.chart.replaceChildren(
      areaChart(drawn, { width: 620, height: 170, unit: ' W' }),
    );
    this._els.legend.replaceChildren(...drawn.map((one) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.innerHTML = `<span class="swatch" style="background: ${one.color}"></span>${
        one.name} <b>${num(this._hass, one.entity).toFixed(1)} W</b>`;
      button.addEventListener('click', () => this.dispatchEvent(new CustomEvent('hass-more-info', {
        detail: { entityId: one.entity }, bubbles: true, composed: true,
      })));
      return button;
    }));
  }
}

defineCard('ugreen-power-card', UgreenPowerCard, {
  name: 'UGREEN Power',
  description: 'The charger\'s power over time, per port, from the recorder',
});
