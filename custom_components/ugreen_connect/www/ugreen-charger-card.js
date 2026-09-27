/* The charger as a whole: what it is delivering out of what it can, and the
 * mode that decides how that is shared.
 *
 * The strip across the top of a charger's dashboard. The total is read from
 * the doorway; the bar under it is the same budget cut into the ports taking
 * it, each in its own colour, so "who is drawing what" and "how much is left"
 * are one glance. The mode sits under the budget because the mode is what
 * divides it, and a mode's own settings sit beside it -- under `priority`,
 * the ports it charges first, which can be changed from here.
 *
 * Config:
 *   type: custom:ugreen-charger-card
 *   device_id: <the charger>        # optional if only one charger is set up
 *   max_power: 300                  # the charger's budget; without it the bar
 *                                   # scales to the largest total seen
 */

import {
  SERIES, SHARED_CSS, applyTheme, defineCard, findAll, findOne, mount, num, optionLabel,
  pending, ports, translator,
} from './ugreen-ui.js';

const TEXT = {
  en: {
    of: 'of {max} W',
    live: '{n} of {all} ports charging',
    nothing: 'Nothing is charging',
    free: '{w} W free',
    connected: 'Connected',
    offline: 'Cloud offline',
    firmware: 'Firmware {version}',
    update: '{version} ready, install it in the UGREEN app',
    mode: 'Charging mode',
    limits: 'Limits, set in the UGREEN app',
    first: 'Charged first',
    firstHint: 'Any of the three, or all of them. The rest share what is left.',
    lastFirst: 'One port always goes first',
    adaptive_power: 'Shares power by what each device asks for.',
    thermal_safe: 'Lowers output as the charger warms up.',
    dc_turbo: 'Gives the DC port its full output.',
    priority: 'The ports chosen to go first get full power before the rest.',
    custom: 'Set in the UGREEN app.',
    noDevice: 'No charger entities found. Set device_id in the card config.',
  },
  de: {
    of: 'von {max} W',
    live: '{n} von {all} Anschlüssen laden',
    nothing: 'Nichts lädt',
    free: '{w} W frei',
    connected: 'Verbunden',
    offline: 'Cloud offline',
    firmware: 'Firmware {version}',
    update: '{version} bereit, in der UGREEN-App installieren',
    mode: 'Lademodus',
    limits: 'Grenzen, in der UGREEN-App gesetzt',
    first: 'Zuerst geladen',
    firstHint: 'Einer der drei oder alle. Die übrigen teilen sich den Rest.',
    lastFirst: 'Ein Anschluss wird immer zuerst geladen',
    adaptive_power: 'Verteilt die Leistung nach dem Bedarf jedes Geräts.',
    thermal_safe: 'Senkt die Leistung, wenn das Ladegerät warm wird.',
    dc_turbo: 'Gibt dem DC-Anschluss die volle Leistung.',
    priority: 'Die gewählten Anschlüsse bekommen vor den übrigen volle Leistung.',
    custom: 'In der UGREEN-App gesetzt.',
    noDevice: 'Keine Entitäten gefunden. device_id in der Kartenkonfiguration setzen.',
  },
  ru: {
    of: 'из {max} Вт',
    live: 'Заряжают {n} из {all} портов',
    nothing: 'Ничего не заряжается',
    free: 'свободно {w} Вт',
    connected: 'На связи',
    offline: 'Облако недоступно',
    firmware: 'Прошивка {version}',
    update: 'Готова {version}, установите в приложении UGREEN',
    mode: 'Режим зарядки',
    limits: 'Лимиты, заданные в приложении UGREEN',
    first: 'Заряжаются первыми',
    firstHint: 'Любой из трёх или все сразу. Остальные делят то, что осталось.',
    lastFirst: 'Хотя бы один порт всегда заряжается первым',
    adaptive_power: 'Делит мощность по запросу каждого устройства.',
    thermal_safe: 'Снижает мощность, когда зарядка нагревается.',
    dc_turbo: 'Отдаёт DC-порту полную мощность.',
    priority: 'Выбранные порты получают полную мощность раньше остальных.',
    custom: 'Задаётся в приложении UGREEN.',
    noDevice: 'Сущности не найдены. Укажите device_id в настройках карточки.',
  },
};

const STYLE = `
  ${SHARED_CSS}
  ha-card { padding: 14px 18px; display: flex; flex-direction: column; gap: 12px; box-sizing: border-box; }
  .top { display: flex; align-items: center; gap: 22px; flex-wrap: wrap; }
  .total { display: flex; align-items: baseline; gap: 8px; flex: none; min-width: 200px; cursor: pointer;
           background: none; border: none; padding: 0; font: inherit; color: inherit; }
  .total .w { font-size: 30px; font-weight: 500; line-height: 1; }
  .total .unit { font-size: 15px; color: var(--secondary-text-color); }
  .total .of { font-size: 13px; color: var(--secondary-text-color); margin-left: 4px; }
  .budget { flex: 1 1 260px; display: flex; flex-direction: column; gap: 5px; min-width: 200px; }
  .bar { display: flex; gap: 2px; height: 12px; border-radius: 6px; overflow: hidden;
         background: color-mix(in srgb, var(--primary-text-color) 9%, transparent); }
  .bar i { display: block; height: 100%; min-width: 3px; }
  .caption { display: flex; justify-content: space-between; gap: 12px; font-size: 12px;
             color: var(--secondary-text-color); }
  .status { display: flex; align-items: center; gap: 18px; flex: none; font-size: 13px;
            color: var(--secondary-text-color); }
  .status button { font: inherit; color: inherit; background: none; border: none; padding: 0;
                   display: inline-flex; align-items: center; gap: 6px; cursor: pointer; }
  .status .offline { color: var(--warning-color, #ff9800); }
  .status .update { color: var(--primary-color); font-weight: 500; }
  .status svg { flex: none; }
  .modes { display: flex; align-items: center; gap: 18px; flex-wrap: wrap; min-height: 36px; }
  .about { font-size: 12px; color: var(--secondary-text-color); }
  .limits { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 13px; }
  .limits .label { color: var(--secondary-text-color); }
  .limit { display: inline-flex; align-items: baseline; gap: 6px; height: 28px; line-height: 28px;
           padding: 0 10px; border-radius: 8px; background: var(--secondary-background-color); }
  .limit .n { font-size: 12px; color: var(--secondary-text-color); }
  .limit b { font-weight: 500; }
  .first { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-size: 13px; }
  .first .label { color: var(--secondary-text-color); }
  .first .u-pill i { width: 8px; height: 8px; border-radius: 50%; flex: none; }
  .first .u-pill[aria-disabled="true"] { cursor: default; }
  .first .hint { font-size: 12px; color: var(--secondary-text-color); }
  .empty { color: var(--secondary-text-color); }
  :host { display: block; container-type: inline-size; }
  .modepick { display: none; position: relative; flex: 1 1 auto; color: var(--secondary-text-color); }
  .modepick select { appearance: none; -webkit-appearance: none; margin: 0; width: 100%; height: 36px;
                     box-sizing: border-box; padding: 0 34px 0 14px; font: inherit; font-size: 14px;
                     color: var(--primary-text-color); background: var(--secondary-background-color);
                     border: 1px solid transparent; border-radius: 18px; }
  .modepick select:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 1px; }
  .modepick svg { position: absolute; right: 14px; top: 50%; margin-top: -3px; pointer-events: none; }
  /* Five modes do not fit across a phone, and a row of them scrolled sideways
     hid the one in use. Narrow, the modes are a list. */
  @container (max-width: 640px) {
    .status { width: 100%; justify-content: space-between; }
    .modes .u-seg { display: none; }
    .modepick { display: block; }
    .first .hint { display: none; }
  }
`;

const CLOUD = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
  stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <path d="M17.5 19H8a5 5 0 1 1 1.2-9.86A6 6 0 0 1 20.8 11 4 4 0 0 1 17.5 19z"></path></svg>`;

class UgreenChargerCard extends HTMLElement {
  static getStubConfig() { return { device_id: '', max_power: 300 }; }

  setConfig(config) {
    this._config = config || {};
    this._built = false;
    this._peak = 0;
    this._asked = pending();
    this._flying = new Map();
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

  _find(domain, suffix) {
    return findOne(this._hass, this._config.device_id, domain, suffix);
  }

  _build() {
    if (this._built) return;
    this._built = true;
    this._root = mount(this, `
      <ha-card>
        <style>${STYLE}</style>
        <div class="top">
          <button type="button" class="total"><span class="w">—</span><span class="unit">W</span><span class="of"></span></button>
          <div class="budget">
            <div class="bar" role="img"></div>
            <div class="caption"><span class="live"></span><span class="free"></span></div>
          </div>
          <div class="status"></div>
        </div>
        <div class="modes">
          <div class="u-seg" role="radiogroup" aria-label="${this._t('mode')}"></div>
          <label class="modepick"><select aria-label="${this._t('mode')}"></select><svg width="10" height="6"
            viewBox="0 0 10 6" aria-hidden="true"><path d="M1 1l4 4 4-4" fill="none" stroke="currentColor"
            stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"></path></svg></label>
          <div class="params"></div>
        </div>
        <div class="empty" hidden>${this._t('noDevice')}</div>
      </ha-card>
    `);
    const $ = (sel) => this._root.querySelector(sel);
    this._els = {
      top: $('.top'), modesRow: $('.modes'), empty: $('.empty'),
      total: $('.total'), watts: $('.total .w'), of: $('.total .of'),
      bar: $('.bar'), live: $('.live'), free: $('.free'),
      status: $('.status'), modes: $('.u-seg'), params: $('.params'),
      pick: $('.modepick select'),
    };
    this._els.pick.addEventListener('change', () => {
      const id = this._find('select', '_charging_mode');
      const option = this._els.pick.value;
      if (!id || option === this._hass.states[id]?.state) return;
      this._asked.set('mode', option);
      this._syncModes(id);
      this._hass.callService('select', 'select_option', { entity_id: id, option });
    });
    this._els.total.addEventListener('click', () => this._moreInfo(this._find('sensor', '_total_power')));
  }

  _sync() {
    if (!this._hass) return;
    const total = this._find('sensor', '_total_power');
    const mode = this._find('select', '_charging_mode');
    const found = Boolean(total || mode);
    this._els.top.hidden = !found;
    this._els.modesRow.hidden = !found;
    this._els.empty.hidden = found;
    if (!found) return;
    this._syncBudget(total);
    this._syncStatus();
    this._syncModes(mode);
  }

  /* The total, and the same total cut into the ports that make it up. */
  _syncBudget(totalId) {
    const all = ports(this._hass, this._config.device_id);
    const watts = totalId ? num(this._hass, totalId) : all.reduce((s, p) => s + num(this._hass, p.id), 0);
    this._peak = Math.max(this._peak, watts, 1);
    const max = Number(this._config.max_power) || 0;
    const scale = max || this._peak;
    this._els.watts.textContent = watts.toFixed(1);
    this._els.of.textContent = max ? this._t('of', { max }) : '';

    // Charging is what the integration's charging sensor says, the same rule
    // the port tiles use: a phone topping up at half a watt is not "charging"
    // there, and counted here it made the strip say one port was while every
    // tile beneath it said none.
    const drawing = all
      .map((port, index) => ({ port, index, w: num(this._hass, port.id) }))
      .filter((p) => this._hass.states[p.port.charging]?.state === 'on');
    this._els.bar.replaceChildren(...drawing.map(({ port, index, w }) => {
      const seg = document.createElement('i');
      seg.style.width = `${Math.min(100, (w / scale) * 100)}%`;
      seg.style.background = SERIES[index % SERIES.length];
      seg.title = `${port.name}: ${w.toFixed(1)} W`;
      return seg;
    }));
    this._els.bar.setAttribute('aria-label', drawing.map(({ port, w }) => `${port.name} ${w.toFixed(1)} W`).join(', ')
      || this._t('nothing'));
    this._els.live.textContent = drawing.length
      ? this._t('live', { n: drawing.length, all: all.length }) : this._t('nothing');
    this._els.free.textContent = max ? this._t('free', { w: Math.max(0, max - watts).toFixed(1) }) : '';
  }

  _syncStatus() {
    const items = [];
    const cloud = this._find('sensor', '_cloud_status');
    if (cloud) {
      const online = this._hass.states[cloud]?.state === 'online';
      items.push({
        html: `${CLOUD}<span>${online ? this._t('connected') : this._t('offline')}</span>`,
        cls: online ? '' : 'offline', entity: cloud,
      });
    }
    const update = this._find('update', '_firmware');
    const state = update && this._hass.states[update];
    if (state) {
      items.push({ html: this._t('firmware', { version: state.attributes.installed_version || '' }), entity: update });
      // The integration cannot install firmware -- the cloud tells the charger
      // to, and that path has not been seen yet -- so an update is announced
      // with where to install it rather than drawn as a button that would not.
      if (state.state === 'on' && state.attributes.latest_version) {
        items.push({ html: this._t('update', { version: state.attributes.latest_version }), cls: 'update', entity: update });
      }
    }
    this._els.status.replaceChildren(...items.map((item) => {
      const el = document.createElement('button');
      el.type = 'button';
      el.className = item.cls || '';
      el.innerHTML = item.html;
      el.addEventListener('click', () => this._moreInfo(item.entity));
      return el;
    }));
  }

  _syncModes(modeId) {
    const state = modeId ? this._hass.states[modeId] : undefined;
    if (!state) { this._els.modes.replaceChildren(); this._els.params.replaceChildren(); return; }
    const options = state.attributes.options || [];
    const current = this._asked.read('mode', state.state);
    // A mode the select does not offer is one the charger is in and cannot be
    // put into from here -- custom, whose parameters only the app composes --
    // so it is shown, chosen and not clickable, rather than as "unknown".
    const names = options.includes(current) ? [...options, 'custom'] : [...options, current];
    const unique = [...new Set(names)];
    this._els.modes.replaceChildren(...unique.map((option) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.setAttribute('role', 'radio');
      button.setAttribute('aria-checked', String(option === current));
      button.textContent = optionLabel(this._hass, modeId, option);
      if (!options.includes(option)) {
        button.disabled = option !== current;
        button.title = this._t('custom');
      } else {
        button.addEventListener('click', () => {
          if (option === current) return;
          this._asked.set('mode', option);
          this._syncModes(modeId);
          this._hass.callService('select', 'select_option', { entity_id: modeId, option });
        });
      }
      return button;
    }));
    // The same choice as a list, for a card too narrow for the row.
    this._els.pick.replaceChildren(...unique.map((option) => {
      const el = document.createElement('option');
      el.value = option;
      el.textContent = optionLabel(this._hass, modeId, option);
      el.disabled = !options.includes(option) && option !== current;
      return el;
    }));
    this._els.pick.value = current;
    this._syncParams(current);
  }

  /* What the current mode is doing with the budget: the ports `priority`
   * charges first, which can be changed here, and the custom limits, which
   * only the app sets. For the rest, a line on what the mode is for. */
  _syncParams(current) {
    if (current === 'priority' && this._syncFirst()) return;
    if (current === 'custom') {
      const limits = findAll(this._hass, this._config.device_id, 'sensor', '_custom_mode_limit')
        .filter((id) => !['unavailable', 'unknown'].includes(this._hass.states[id]?.state))
        .sort();
      if (limits.length) {
        const row = document.createElement('div');
        row.className = 'limits';
        row.innerHTML = `<span class="label">${this._t('limits')}</span>`;
        for (const id of limits) {
          const s = this._hass.states[id];
          const name = (s.attributes.friendly_name || id)
            .replace(/\s*custom[- ]mode limit$/i, '').split(' ').pop();
          const cell = document.createElement('span');
          cell.className = 'limit';
          cell.innerHTML = `<span class="n"></span><b></b>`;
          cell.querySelector('.n').textContent = name;
          cell.querySelector('b').textContent = `${Math.round(num(this._hass, id))} ${s.attributes.unit_of_measurement || 'W'}`;
          row.appendChild(cell);
        }
        this._els.params.replaceChildren(row);
        return;
      }
    }
    const about = document.createElement('span');
    about.className = 'about';
    about.textContent = TEXT.en[current] ? this._t(current) : '';
    this._els.params.replaceChildren(about);
  }

  /* C1, C2 and C3, each a toggle, since the app lets any of them go first and
   * all three together. One switch per port on the integration's side, so the
   * pills are those switches; while the mode is anything else they are
   * unavailable, and this draws nothing and says what the mode does instead.
   *
   * The last one on stays on: the charger has never been sent an empty choice,
   * the switch refuses it, and a pill that looked like it would turn off and
   * then did not is worse than one that says why it does not.
   *
   * A press shows at once and holds until its call is done, not until the
   * switch first agrees: pressed twice quickly, the second press's value can
   * be the one the switch still has from before the first, and agreeing with
   * that let the first press's result show through in between. */
  _syncFirst() {
    const switches = findAll(this._hass, this._config.device_id, 'switch', '_charged_first')
      .filter((id) => ['on', 'off'].includes(this._hass.states[id]?.state))
      .map((id) => ({ id, name: id.split('.')[1].slice(0, -'_charged_first'.length).split('_').pop().toUpperCase() }))
      .sort((a, b) => a.name.localeCompare(b.name));
    if (!switches.length) return false;
    const all = ports(this._hass, this._config.device_id);
    const on = (s) => (this._flying.get(s.id)?.value
      ?? this._asked.read(`first:${s.id}`, this._hass.states[s.id].state)) === 'on';
    const count = switches.filter(on).length;

    const row = document.createElement('div');
    row.className = 'first';
    row.innerHTML = `<span class="label">${this._t('first')}</span>`;
    for (const s of switches) {
      const pressed = on(s);
      const last = pressed && count === 1;
      const index = all.findIndex((p) => p.name === s.name);
      const pill = document.createElement('button');
      pill.type = 'button';
      pill.className = 'u-pill';
      pill.setAttribute('aria-pressed', String(pressed));
      pill.innerHTML = `<i style="background: ${index >= 0 ? SERIES[index % SERIES.length] : 'var(--secondary-text-color)'}"></i><span></span>`;
      pill.querySelector('span').textContent = s.name;
      if (last) {
        pill.setAttribute('aria-disabled', 'true');
        pill.title = this._t('lastFirst');
      }
      pill.addEventListener('click', () => {
        if (last) return;
        const value = pressed ? 'off' : 'on';
        const flight = this._flying.get(s.id) || { calls: 0 };
        flight.value = value;
        flight.calls += 1;
        this._flying.set(s.id, flight);
        this._syncParams('priority');
        // Home Assistant says why a call failed on its own, in a toast.
        this._hass.callService('switch', pressed ? 'turn_off' : 'turn_on', { entity_id: s.id })
          .then(() => { if (flight.calls === 1) this._asked.set(`first:${s.id}`, flight.value); })
          .catch(() => {})
          .finally(() => {
            flight.calls -= 1;
            if (!flight.calls) this._flying.delete(s.id);
            this._sync();
          });
      });
      row.appendChild(pill);
    }
    const hint = document.createElement('span');
    hint.className = 'hint';
    hint.textContent = this._t('firstHint');
    row.appendChild(hint);
    this._els.params.replaceChildren(row);
    return true;
  }

  _moreInfo(entityId) {
    if (!entityId) return;
    this.dispatchEvent(new CustomEvent('hass-more-info', { detail: { entityId }, bubbles: true, composed: true }));
  }
}

defineCard('ugreen-charger-card', UgreenChargerCard, {
  name: 'UGREEN Charger',
  description: 'Total power out of the budget, cut into the ports drawing it, and the charging mode',
});
