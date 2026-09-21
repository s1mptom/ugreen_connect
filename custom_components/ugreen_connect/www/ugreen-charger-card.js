/* The charger at a glance: what it is delivering, which mode it is in, and the
 * two screen settings anyone actually touches.
 *
 * The tile card can show any one of these, and four tiles in a row is four
 * boxes of chrome around four numbers. This is the strip across the top of a
 * charger's dashboard: the total big enough to read from the doorway, the
 * modes as one row of buttons rather than a dropdown, and the state of the
 * cloud and the firmware where they can be ignored until they matter.
 *
 * Config:
 *   type: custom:ugreen-charger-card
 *   device_id: <the charger>        # optional if only one charger is set up
 *   max_power: 300                  # what the bar scales to; default: the
 *                                   # largest total this card has seen
 *   screen: true                    # brightness and screen-off; default true
 *   today: true                     # today's energy, from statistics
 */

import {
  mount, SHARED_CSS, chargerEntity, defineCard, findOne, num, optionLabel, pending, translator,
} from './ugreen-ui.js';

const TEXT = {
  en: {
    drawingNow: 'Drawing now',
    of: 'of {max} W',
    today: 'Today',
    allTime: 'All time',
    ports: 'Ports drawing',
    mode: 'Charging mode',
    screen: 'Screen',
    screenOff: 'Off after',
    cloudOnline: 'Cloud online',
    cloudOffline: 'Cloud offline',
    firmware: 'Firmware {version}',
    updateWaiting: 'Firmware {version} waiting',
    modeRefused: 'Custom is set in the UgreenConnect app',
    noDevice: 'No charger entities found. Set device_id in the card config.',
  },
  de: {
    drawingNow: 'Aktuell',
    of: 'von {max} W',
    today: 'Heute',
    allTime: 'Gesamt',
    ports: 'Anschlüsse laden',
    mode: 'Lademodus',
    screen: 'Bildschirm',
    screenOff: 'Aus nach',
    cloudOnline: 'Cloud online',
    cloudOffline: 'Cloud offline',
    firmware: 'Firmware {version}',
    updateWaiting: 'Firmware {version} verfügbar',
    modeRefused: 'Port-Anpassung wird in der UgreenConnect-App gesetzt',
    noDevice: 'Keine Entitäten gefunden. device_id in der Kartenkonfiguration setzen.',
  },
  ru: {
    drawingNow: 'Сейчас отдаёт',
    of: 'из {max} Вт',
    today: 'Сегодня',
    allTime: 'За всё время',
    ports: 'Портов заряжает',
    mode: 'Режим зарядки',
    screen: 'Экран',
    screenOff: 'Гаснет через',
    cloudOnline: 'Облако на связи',
    cloudOffline: 'Облако недоступно',
    firmware: 'Прошивка {version}',
    updateWaiting: 'Готова прошивка {version}',
    modeRefused: 'Пользовательский режим задаётся в приложении UgreenConnect',
    noDevice: 'Сущности не найдены. Укажите device_id в настройках карточки.',
  },
};

const STYLE = `
  ${SHARED_CSS}
  ha-card { overflow: hidden; height: 100%; box-sizing: border-box; }
  .body { padding: 12px 16px; display: flex; gap: 20px; align-items: flex-start; flex-wrap: wrap;
          height: 100%; box-sizing: border-box; }
  .left { min-width: 190px; max-width: 260px; display: flex; flex-direction: column; gap: 5px; }
  .label { font-size: .78em; text-transform: uppercase; letter-spacing: .06em;
           color: var(--secondary-text-color); }
  .total { display: flex; align-items: baseline; gap: 6px; }
  .total .w { font-size: 2.3em; line-height: 1; font-weight: 400; }
  .total .unit { color: var(--secondary-text-color); }
  .total .of { margin-left: auto; font-size: .82em; color: var(--secondary-text-color); }
  .meter { height: 7px; border-radius: 4px; background: var(--divider-color); overflow: hidden; }
  .meter > i { display: block; height: 7px; background: var(--state-icon-active-color, var(--primary-color)); }
  .figures { display: flex; gap: 14px; padding-top: 2px; }
  .figures .n { font-size: 1.05em; }
  .right { flex: 1 1 320px; display: flex; flex-direction: column; gap: 8px; min-width: 260px; }
  .modes { display: flex; gap: 6px; flex-wrap: wrap; }
  .modes button { flex: 1 1 88px; padding: 7px 8px; font: inherit; font-size: .86em;
                  color: var(--primary-text-color); background: var(--secondary-background-color);
                  border: 1px solid var(--divider-color); border-radius: 10px; cursor: pointer; }
  .modes button:hover { border-color: var(--state-icon-active-color, var(--primary-color)); }
  .modes button[aria-pressed="true"] { background: var(--state-icon-active-color, var(--primary-color));
                  border-color: var(--state-icon-active-color, var(--primary-color));
                  color: var(--text-primary-color, #fff); font-weight: 500; }
  .modes button[disabled] { cursor: default; opacity: .75; border-style: dashed; }
  .modes button[disabled]:hover { border-color: var(--divider-color); }
  .chips { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  .chip { display: inline-flex; align-items: center; gap: 6px; padding: 5px 10px; font-size: .82em;
          background: var(--secondary-background-color); border-radius: 999px;
          color: var(--secondary-text-color); }
  .chip .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--disabled-text-color); }
  .chip.ok .dot { background: var(--success-color, #4caf50); }
  .chip.warn { color: var(--primary-text-color); }
  .chip.warn .dot { background: var(--warning-color, #ff9800); }
  .screen { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .screen ha-slider, .screen input[type="range"] { flex: 1 1 120px; min-width: 110px; accent-color: var(--state-icon-active-color, var(--primary-color)); }
  .screen .val { font-size: .85em; color: var(--secondary-text-color); min-width: 40px; }
  .screen select { font: inherit; font-size: .85em; padding: 5px 8px; border-radius: 8px;
                   color: var(--primary-text-color); background: var(--secondary-background-color);
                   border: 1px solid var(--divider-color); }
  .note { font-size: .8em; color: var(--secondary-text-color); min-height: 1.1em; }
  .empty { padding: 14px 16px; color: var(--secondary-text-color); }
`;

class UgreenChargerCard extends HTMLElement {
  static getStubConfig() { return { device_id: '' }; }

  setConfig(config) {
    this._config = config || {};
    this._built = false;
    this._peak = 0;
    this._today = null;
    this._askedToday = 0;
    // What has been asked for and not yet confirmed; see `pending`.
    this._asked = pending();
    if (this.shadowRoot) this.shadowRoot.innerHTML = '';
  }

  set hass(hass) {
    this._hass = hass;
    this._t = translator(TEXT, hass);
    this._build();
    this._sync();
  }

  getCardSize() { return 4; }

  /* Entities ------------------------------------------------------------ */

  _find(domain, suffix) {
    return findOne(this._hass, this._config.device_id, domain, suffix);
  }

  _entities() {
    return {
      total: this._find('sensor', '_total_power'),
      energy: chargerEntity(this._hass, this._config.device_id, 'sensor', 'energy'),
      cloud: this._find('sensor', '_cloud_status'),
      firmware: this._find('update', '_firmware'),
      mode: this._find('select', '_charging_mode'),
      brightness: this._find('number', '_screen_brightness'),
      screenOff: this._find('select', '_screen_off_time'),
    };
  }

  /* Rendering ----------------------------------------------------------- */

  _build() {
    if (this._built) return;
    this._built = true;
    this._root = mount(this, `
      <ha-card>
        <style>${STYLE}</style>
        <div class="body" hidden>
          <div class="left">
            <div class="label">${this._t('drawingNow')}</div>
            <div class="total"><span class="w">—</span><span class="unit">W</span><span class="of"></span></div>
            <div class="meter"><i></i></div>
            <div class="figures">
              <div class="today" hidden><div class="label">${this._t('today')}</div><div class="n today-n"></div></div>
              <div><div class="label">${this._t('allTime')}</div><div class="n all-n"></div></div>
              <div><div class="label">${this._t('ports')}</div><div class="n ports-n"></div></div>
            </div>
          </div>
          <div class="right">
            <div class="label">${this._t('mode')}</div>
            <div class="modes"></div>
            <div class="chips"></div>
            <div class="screen" hidden>
              <span class="label">${this._t('screen')}</span>
              <input class="bright" type="range" min="0" max="100" step="1" aria-label="${this._t('screen')}">
              <span class="val"></span>
              <span class="label">${this._t('screenOff')}</span>
              <select class="off" aria-label="${this._t('screenOff')}"></select>
            </div>
            <div class="note"></div>
          </div>
        </div>
        <div class="empty">${this._t('noDevice')}</div>
      </ha-card>
    `);
    this._els = {
      body: this._root.querySelector('.body'),
      empty: this._root.querySelector('.empty'),
      watts: this._root.querySelector('.total .w'),
      of: this._root.querySelector('.total .of'),
      meter: this._root.querySelector('.meter > i'),
      today: this._root.querySelector('.today'),
      todayValue: this._root.querySelector('.today-n'),
      all: this._root.querySelector('.all-n'),
      ports: this._root.querySelector('.ports-n'),
      modes: this._root.querySelector('.modes'),
      chips: this._root.querySelector('.chips'),
      screen: this._root.querySelector('.screen'),
      bright: this._root.querySelector('.bright'),
      brightValue: this._root.querySelector('.val'),
      off: this._root.querySelector('.off'),
      note: this._root.querySelector('.note'),
    };

    this._els.bright.addEventListener('change', (event) => {
      const id = this._entities().brightness;
      if (!id) return;
      this._asked.set('brightness', String(Number(event.target.value)));
      this._hass.callService('number', 'set_value', { entity_id: id, value: Number(event.target.value) });
    });
    this._els.bright.addEventListener('input', (event) => {
      this._els.brightValue.textContent = `${event.target.value}%`;
    });
    this._els.off.addEventListener('change', (event) => {
      const id = this._entities().screenOff;
      if (!id) return;
      this._asked.set('screenOff', event.target.value);
      this._hass.callService('select', 'select_option', { entity_id: id, option: event.target.value });
    });
    this._els.watts.addEventListener('click', () => this._moreInfo(this._entities().total));
  }

  _sync() {
    if (!this._hass) return;
    const ent = this._entities();
    const found = Boolean(ent.total || ent.mode);
    this._els.body.hidden = !found;
    this._els.empty.hidden = found;
    if (!found) return;

    const watts = num(this._hass, ent.total);
    this._peak = Math.max(this._peak, watts, 1);
    const scale = Number(this._config.max_power) || this._peak;
    this._els.watts.textContent = watts.toFixed(1);
    this._els.of.textContent = this._config.max_power
      ? this._t('of', { max: this._config.max_power }) : '';
    this._els.meter.style.width = `${Math.min(100, (watts / scale) * 100)}%`;

    const charging = Object.keys(this._hass.states).filter((id) =>
      id.startsWith('binary_sensor.') && id.endsWith('_charging')
      && (!this._config.device_id
        || this._hass.states[id].attributes.device_id === this._config.device_id
        || id.includes('ugreen')));
    const live = charging.filter((id) => this._hass.states[id].state === 'on').length;
    this._els.ports.textContent = charging.length ? `${live} / ${charging.length}` : '—';

    const energy = ent.energy ? num(this._hass, ent.energy, NaN) : NaN;
    this._els.all.textContent = Number.isFinite(energy) ? `${energy.toFixed(2)} kWh` : '—';
    this._syncToday(ent.energy);

    this._syncModes(ent.mode);
    this._syncChips(ent);
    this._syncScreen(ent);
  }

  /* Today's energy is a statistic rather than a state: the counter only ever
   * rises, so what went in since midnight is the difference across the day.
   * Asked for at most once a minute, and simply left out when the recorder has
   * nothing to say. */
  _syncToday(energyId) {
    const wanted = this._config.today !== false && energyId;
    this._els.today.hidden = !wanted || this._today === null;
    if (!wanted) return;
    if (this._today !== null) {
      this._els.todayValue.textContent = `${this._today.toFixed(2)} kWh`;
    }
    const now = Date.now();
    if (now - this._askedToday < 60000) return;
    this._askedToday = now;
    const midnight = new Date();
    midnight.setHours(0, 0, 0, 0);
    this._hass.callWS({
      type: 'recorder/statistics_during_period',
      start_time: midnight.toISOString(),
      statistic_ids: [energyId],
      period: 'day',
      types: ['change'],
    }).then((result) => {
      const rows = result?.[energyId] || [];
      const change = rows.reduce((sum, row) => sum + (Number(row.change) || 0), 0);
      this._today = change;
      this._els.today.hidden = false;
      this._els.todayValue.textContent = `${change.toFixed(2)} kWh`;
    }).catch(() => {
      this._today = null;
      this._els.today.hidden = true;
    });
  }

  _syncModes(modeId) {
    const state = modeId ? this._hass.states[modeId] : undefined;
    if (!state) { this._els.modes.replaceChildren(); return; }
    const options = state.attributes.options || [];
    const current = this._asked.read('mode', state.state);
    const names = options.includes(current) ? options : [...options, current];

    this._els.modes.replaceChildren(...names.map((option) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = optionLabel(this._hass, modeId, option);
      button.setAttribute('aria-pressed', String(option === current));
      // A mode the select does not offer is one the charger is in and cannot be
      // put into from here -- `custom`, whose parameters only the app composes.
      // It is shown, because a charger sitting in it is not "unknown", and it
      // is not clickable, because the click would be refused.
      if (!options.includes(option)) {
        button.disabled = true;
        button.title = this._t('modeRefused');
      } else {
        button.addEventListener('click', () => {
          this._asked.set('mode', option);
          this._syncModes(modeId);
          this._hass.callService('select', 'select_option', { entity_id: modeId, option });
        });
      }
      return button;
    }));
    this._els.note.textContent = options.includes(current) ? '' : this._t('modeRefused');
  }

  _syncChips(ent) {
    const chips = [];
    const cloud = ent.cloud ? this._hass.states[ent.cloud]?.state : undefined;
    if (cloud) {
      chips.push({
        text: cloud === 'online' ? this._t('cloudOnline') : this._t('cloudOffline'),
        cls: cloud === 'online' ? 'ok' : 'warn',
        entity: ent.cloud,
      });
    }
    const update = ent.firmware ? this._hass.states[ent.firmware] : undefined;
    if (update) {
      const waiting = update.state === 'on';
      chips.push({
        text: waiting
          ? this._t('updateWaiting', { version: update.attributes.latest_version || '' })
          : this._t('firmware', { version: update.attributes.installed_version || '' }),
        cls: waiting ? 'warn' : 'ok',
        entity: ent.firmware,
      });
    }
    this._els.chips.replaceChildren(...chips.map((chip) => {
      const el = document.createElement('button');
      el.type = 'button';
      el.className = `chip ${chip.cls}`;
      el.innerHTML = `<span class="dot"></span>${chip.text}`;
      el.addEventListener('click', () => this._moreInfo(chip.entity));
      return el;
    }));
  }

  _syncScreen(ent) {
    const wanted = this._config.screen !== false && (ent.brightness || ent.screenOff);
    this._els.screen.hidden = !wanted;
    if (!wanted) return;

    const brightness = ent.brightness ? this._hass.states[ent.brightness] : undefined;
    if (brightness && this._root.activeElement !== this._els.bright) {
      const shown = this._asked.read('brightness', brightness.state);
      this._els.bright.value = shown;
      this._els.brightValue.textContent = `${Math.round(Number(shown))}%`;
    }
    this._els.bright.hidden = !brightness;

    const off = ent.screenOff ? this._hass.states[ent.screenOff] : undefined;
    this._els.off.hidden = !off;
    if (!off) return;
    const options = off.attributes.options || [];
    const same = this._els.off.options.length === options.length
      && [...this._els.off.options].every((option, i) => option.value === options[i]);
    if (!same) {
      this._els.off.replaceChildren(...options.map((option) => {
        const el = document.createElement('option');
        el.value = option;
        el.textContent = optionLabel(this._hass, ent.screenOff, option);
        return el;
      }));
    }
    this._els.off.value = this._asked.read('screenOff', off.state);
  }

  _moreInfo(entityId) {
    if (!entityId) return;
    this.dispatchEvent(new CustomEvent('hass-more-info', {
      detail: { entityId }, bubbles: true, composed: true,
    }));
  }
}

defineCard('ugreen-charger-card', UgreenChargerCard, {
  name: 'UGREEN Charger',
  description: 'Total power, charging mode and the screen settings in one strip',
});
