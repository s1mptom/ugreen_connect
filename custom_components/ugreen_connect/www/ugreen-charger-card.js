/* The charger as a whole: what it is delivering out of what it can, and the
 * mode that decides how that is shared.
 *
 * The strip across the top of a charger's dashboard. The total is read from
 * the doorway; the bar under it is the same budget cut into the ports taking
 * it, each in its own colour, so "who is drawing what" and "how much is left"
 * are one glance. The mode sits under the budget because the mode is what
 * divides it, and a mode's own settings sit beside it: the ports `priority`
 * charges first and the DC port's voltage and Always On under `dc_turbo`,
 * both of which can be changed from here.
 *
 * Config:
 *   type: custom:ugreen-charger-card
 *   device_id: <the charger>        # optional if only one charger is set up
 *   max_power: 300                  # the charger's budget; without it the bar
 *                                   # scales to the largest total seen
 */

import {
  SERIES, SHARED_CSS, applyTheme, bare, budget, defineCard, findAll, findOne, mount, num, optionLabel,
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
    updateTo: 'Update to {version}',
    pause: 'Ports may stop charging while it restarts, about two minutes.',
    goNow: 'Update now',
    notNow: 'Not now',
    updating: 'Updating to {version}',
    resumes: 'Charging resumes once it restarts',
    failed: 'Update failed: {error}',
    mode: 'Charging mode',
    limits: 'Limits, set in the UGREEN app',
    first: 'Charged first',
    firstHint: 'Any of the three, or all of them. The rest share what is left.',
    lastFirst: 'One port always goes first',
    dcVoltage: 'DC port voltage',
    alwaysOn: 'Always on, even with nothing plugged in',
    turboSome: 'Only C1–C3 charge beside it.',
    turboNone: 'The USB ports are off at 20 V.',
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
    updateTo: 'Auf {version} aktualisieren',
    pause: 'Die Anschlüsse können beim Neustart etwa zwei Minuten lang aussetzen.',
    goNow: 'Jetzt aktualisieren',
    notNow: 'Nicht jetzt',
    updating: 'Aktualisierung auf {version}',
    resumes: 'Das Laden geht nach dem Neustart weiter',
    failed: 'Aktualisierung fehlgeschlagen: {error}',
    mode: 'Lademodus',
    limits: 'Grenzen, in der UGREEN-App gesetzt',
    first: 'Zuerst geladen',
    firstHint: 'Einer der drei oder alle. Die übrigen teilen sich den Rest.',
    lastFirst: 'Ein Anschluss wird immer zuerst geladen',
    dcVoltage: 'Spannung am DC-Anschluss',
    alwaysOn: 'Immer an, auch wenn nichts angeschlossen ist',
    turboSome: 'Daneben laden nur C1–C3.',
    turboNone: 'Bei 20 V sind die USB-Anschlüsse aus.',
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
    updateTo: 'Обновить до {version}',
    pause: 'Пока зарядка перезапускается, порты могут отключиться — примерно на две минуты.',
    goNow: 'Обновить сейчас',
    notNow: 'Не сейчас',
    updating: 'Обновление до {version}',
    resumes: 'Зарядка продолжится после перезапуска',
    failed: 'Обновление не удалось: {error}',
    mode: 'Режим зарядки',
    limits: 'Лимиты, заданные в приложении UGREEN',
    first: 'Заряжаются первыми',
    firstHint: 'Любой из трёх или все сразу. Остальные делят то, что осталось.',
    lastFirst: 'Хотя бы один порт всегда заряжается первым',
    dcVoltage: 'Напряжение DC-порта',
    alwaysOn: 'Всегда включён, даже если ничего не подключено',
    turboSome: 'Рядом заряжают только C1–C3.',
    turboNone: 'При 20 В USB-порты выключены.',
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
  .status { flex-wrap: wrap; row-gap: 8px; }
  /* The row's buttons are bare text; this one is a real button again. */
  .status .u-button { height: 28px; padding: 0 12px; border: 1px solid var(--divider-color);
                      border-radius: 14px; color: var(--primary-color); font-weight: 500; }
  .status .failed { color: var(--error-color, #db4437); }
  .fwrow { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; padding: 10px 14px;
           border-radius: 10px; font-size: 13px;
           background: color-mix(in srgb, var(--primary-color) 9%, transparent); }
  .fwrow .what { display: flex; flex-direction: column; gap: 2px; flex: 1 1 260px; min-width: 0; }
  .fwrow .what b { font-weight: 500; }
  .fwrow .what span { color: var(--secondary-text-color); font-size: 12px; }
  .fwrow .acts { display: flex; gap: 8px; flex: none; }
  .bar.installing { background: color-mix(in srgb, var(--primary-color) 14%, transparent); }
  .bar.installing i { background: var(--primary-color); transition: width .6s ease; }
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
  .turbo { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-size: 13px; }
  .turbo .label { color: var(--secondary-text-color); }
  .turbo .u-seg button { height: 26px; padding: 0 12px; border-radius: 7px; }
  .turbo .sw { display: flex; align-items: center; gap: 8px; margin-left: 8px; cursor: pointer;
               color: var(--secondary-text-color); }
  .turbo .hint { font-size: 12px; color: var(--secondary-text-color); }
  .turbo .hint.off { color: var(--warning-color, #ff9800); }
  .empty { color: var(--secondary-text-color); }
  :host { display: block; container-type: inline-size; }
  .modepick { display: none; flex: 1 1 auto; }
  /* Five modes do not fit across a phone, and a row of them scrolled sideways
     hid the one in use. Narrow, the modes are a list. */
  @container (max-width: 640px) {
    .status { width: 100%; justify-content: space-between; }
    .modes > .u-seg { display: none; }
    .modepick { display: block; }
    .first .hint { display: none; }
    .turbo .sw { margin-left: 0; }
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
    // The firmware install: whether its confirmation is open, whether it has
    // been sent and not yet answered, and how it failed if it did.
    this._fw = { ask: false, sent: false, error: '' };
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
        <div class="fwrow" hidden></div>
        <div class="modes">
          <div class="u-seg" role="radiogroup" aria-label="${this._t('mode')}"></div>
          <label class="u-pick modepick"><select aria-label="${this._t('mode')}"></select><svg width="10" height="6"
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
      status: $('.status'), modes: $('.u-seg'), params: $('.params'), fwrow: $('.fwrow'),
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
    // The config's figure, else the model's own: a 160W is not out of 300.
    const max = Number(this._config.max_power) || budget(this._hass, this._config.device_id);
    const scale = max || this._peak;
    // A total that is not a number is not zero: nothing was measured.
    const measured = !totalId || Number.isFinite(parseFloat(this._hass.states[totalId]?.state));
    this._els.watts.textContent = measured ? watts.toFixed(1) : '—';
    this._els.of.textContent = max ? this._t('of', { max }) : '';
    const fw = this._firmware();
    this._els.bar.classList.toggle('installing', Boolean(fw?.installing));
    if (fw?.installing) {
      // Nothing is measured while the charger installs -- an empty bar over
      // "nothing is charging" would be a claim -- so the strip shows the
      // install instead.
      this._els.watts.textContent = '—';
      const seg = document.createElement('i');
      seg.style.width = `${fw.pct || 0}%`;
      this._els.bar.replaceChildren(seg);
      this._els.bar.setAttribute('aria-label', this._fwProgress(fw));
      this._els.live.textContent = this._fwProgress(fw);
      this._els.free.textContent = this._t('resumes');
      return;
    }

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
    const fw = this._firmware();
    if (fw) items.push({ html: this._t('firmware', { version: fw.installed || '' }), entity: fw.id });
    const made = items.map((item) => {
      const el = document.createElement('button');
      el.type = 'button';
      el.className = item.cls || '';
      el.innerHTML = item.html;
      el.addEventListener('click', () => this._moreInfo(item.entity));
      return el;
    });
    this._els.status.replaceChildren(...made, ...(fw ? this._firmwareStatus(fw) : []));
    this._syncFirmwareRow(fw);
  }

  /* Firmware ------------------------------------------------------------- */

  _firmware() {
    const id = this._find('update', '_firmware');
    const state = id && this._hass.states[id];
    if (!state) return null;
    const a = state.attributes;
    return {
      id,
      installed: a.installed_version,
      latest: a.latest_version,
      offered: state.state === 'on' && Boolean(a.latest_version),
      // Home Assistant's own flag, once the integration has said so; until
      // then, that the request is on its way.
      installing: Boolean(a.in_progress) || this._fw.sent,
      pct: Number.isFinite(a.update_percentage) ? a.update_percentage : null,
      // INSTALL is bit 1. Without it -- the 160W -- the app is where it goes.
      installable: (Number(a.supported_features) & 1) === 1,
      notes: a.release_summary || '',
    };
  }

  _fwProgress(fw) {
    const doing = this._t('updating', { version: fw.latest });
    return fw.pct ? `${doing} · ${Math.round(fw.pct)}%` : `${doing}…`;
  }

  _install(fw) {
    this._fw = { ask: false, sent: true, error: '' };
    this._sync();
    // The call returns when the charger has finished, or says why it could
    // not; the progress in between arrives as the entity's state.
    Promise.resolve(this._hass.callService('update', 'install', { entity_id: fw.id }))
      .catch((err) => {
        // A service error arrives as {code, message}; a dropped connection as
        // {error: {code: 3, message}} -- and a phone that loses its socket
        // for the two minutes has lost nothing else: the charger goes on, and
        // its progress comes back with the connection.
        const detail = err?.error || err;
        if (detail?.code === 3) return;
        this._fw.error = detail?.message || String(err);
      })
      .finally(() => { this._fw.sent = false; this._sync(); });
  }

  _button(text, cls, onClick) {
    const el = document.createElement('button');
    el.type = 'button';
    el.className = cls;
    el.textContent = text;
    el.addEventListener('click', onClick);
    return el;
  }

  /* What the status row says about firmware beyond its version: an offer,
     or how an install failed. The question and the progress have rows of
     their own. */
  _firmwareStatus(fw) {
    const out = [];
    if (this._fw.error) {
      out.push(this._button(this._t('failed', { error: this._fw.error }), 'failed', () => this._moreInfo(fw.id)));
    }
    if (fw.installing || !fw.offered || this._fw.ask) return out;
    if (!fw.installable) {
      // The 160W: nobody has watched an install on one, so the app does it.
      out.push(this._button(this._t('update', { version: fw.latest }), 'update', () => this._moreInfo(fw.id)));
      return out;
    }
    out.push(this._button(this._t('updateTo', { version: fw.latest }), 'u-button', () => {
      this._fw.ask = true;
      this._fw.error = '';
      this._sync();
    }));
    return out;
  }

  /* The question, in a row of its own under the header: what the version
     is, what changed, and that the ports may stop for a while. */
  _syncFirmwareRow(fw) {
    const row = this._els.fwrow;
    // An offer that has gone -- installed from the app, say -- takes its
    // question and any failure with it.
    if (!fw?.offered && !fw?.installing) this._fw = { ...this._fw, ask: false, error: '' };
    const open = Boolean(fw && fw.offered && fw.installable && this._fw.ask && !fw.installing);
    row.hidden = !open;
    if (!open) { row.replaceChildren(); return; }
    const what = document.createElement('div');
    what.className = 'what';
    const title = document.createElement('b');
    title.textContent = [this._t('updateTo', { version: fw.latest }), fw.notes].filter(Boolean).join(' — ');
    const why = document.createElement('span');
    why.textContent = this._t('pause');
    what.append(title, why);
    const acts = document.createElement('div');
    acts.className = 'acts';
    acts.append(
      this._button(this._t('notNow'), 'u-button', () => { this._fw.ask = false; this._sync(); }),
      this._button(this._t('goNow'), 'u-button primary', () => this._install(fw)),
    );
    row.replaceChildren(what, acts);
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
   * charges first and the DC port's settings under `dc_turbo`, which can be
   * changed here, and the custom limits, which only the app sets. For the
   * rest, a line on what the mode is for. */
  _syncParams(current) {
    if (current === 'priority' && this._syncFirst()) return;
    if (current === 'dc_turbo' && this._syncTurbo()) return;
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
          const name = s.attributes.port || (s.attributes.friendly_name || id)
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
   * A press shows at once and holds until its call is done (see `_send`). */
  _syncFirst() {
    const switches = findAll(this._hass, this._config.device_id, 'switch', '_charged_first')
      .filter((id) => ['on', 'off'].includes(this._hass.states[id]?.state))
      .map((id) => ({ id, name: bare(id).split('.')[1].slice(0, -'_charged_first'.length).split('_').pop().toUpperCase() }))
      .sort((a, b) => a.name.localeCompare(b.name));
    if (!switches.length) return false;
    const all = ports(this._hass, this._config.device_id);
    const on = (s) => this._shown(s.id) === 'on';
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
        this._send(s.id, pressed ? 'off' : 'on', 'switch', pressed ? 'turn_off' : 'turn_on');
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

  /* The DC port under `dc_turbo`: its voltage, one of the three the app offers,
   * and whether it stays live with nothing plugged in. Each is its own entity,
   * unavailable under any other mode, when this draws nothing.
   *
   * And what the voltage costs the USB ports, said beside it, because it is
   * not said anywhere else: at 12 or 15 V only C1 to C3 stay on, at 20 V none
   * do. That is UGREEN's rule as the Notebookcheck review of this charger
   * gives it, and what an X783 did here -- C5 went dark the moment DC turbo
   * came on at 12 V. Choosing 20 V from a dashboard stops a laptop charging
   * on C1, and the person choosing should know before, not after. */
  _syncTurbo() {
    const volts = this._find('select', '_dc_port_voltage');
    const always = this._find('switch', '_dc_always_on');
    const vs = volts && this._hass.states[volts];
    const on = always && ['on', 'off'].includes(this._hass.states[always]?.state);
    const live = vs && vs.state !== 'unavailable';
    if (!live && !on) return false;

    const row = document.createElement('div');
    row.className = 'turbo';
    if (live) {
      const label = document.createElement('span');
      label.className = 'label';
      label.textContent = this._t('dcVoltage');
      const seg = document.createElement('div');
      seg.className = 'u-seg';
      seg.setAttribute('role', 'radiogroup');
      seg.setAttribute('aria-label', this._t('dcVoltage'));
      const current = this._shown(volts);
      for (const option of vs.attributes.options || []) {
        const button = document.createElement('button');
        button.type = 'button';
        button.setAttribute('role', 'radio');
        button.setAttribute('aria-checked', String(option === current));
        button.textContent = optionLabel(this._hass, volts, option);
        button.addEventListener('click', () => {
          if (option !== this._shown(volts)) this._send(volts, option, 'select', 'select_option', { option });
        });
        seg.appendChild(button);
      }
      row.append(label, seg);
      const chosen = Number(this._shown(volts));
      if ([12, 15, 20].includes(chosen)) {
        const hint = document.createElement('span');
        hint.className = chosen === 20 ? 'hint off' : 'hint';
        hint.textContent = this._t(chosen === 20 ? 'turboNone' : 'turboSome');
        row.appendChild(hint);
      }
    }
    if (on) {
      const sw = document.createElement('label');
      sw.className = 'sw';
      const toggle = document.createElement('ha-switch');
      toggle.checked = this._shown(always) === 'on';
      toggle.setAttribute('aria-label', this._t('alwaysOn'));
      toggle.addEventListener('change', () => {
        this._send(always, toggle.checked ? 'on' : 'off', 'switch', toggle.checked ? 'turn_on' : 'turn_off');
      });
      sw.append(toggle, document.createTextNode(this._t('alwaysOn')));
      row.appendChild(sw);
    }
    this._els.params.replaceChildren(row);
    return true;
  }

  /* What a control shows: the value last asked of it while its call runs,
   * then the entity's own. */
  _shown(entityId) {
    return this._flying.get(entityId)?.value ?? this._asked.read(entityId, this._hass.states[entityId]?.state);
  }

  /* Ask for a value, and show it at once.
   *
   * Held until the call is done rather than until the entity first agrees:
   * pressed twice quickly, the second press's value can be the one the entity
   * still has from before the first, and agreeing with that let the first
   * press's result show through in between. The call is done once the
   * integration has read the charger back, and the last one's value is then
   * held a little longer, until the new state reaches this card. */
  _send(entityId, value, domain, service, data = {}) {
    const flight = this._flying.get(entityId) || { calls: 0 };
    flight.value = value;
    flight.calls += 1;
    this._flying.set(entityId, flight);
    this._sync();
    // Home Assistant says why a call failed on its own, in a toast.
    this._hass.callService(domain, service, { entity_id: entityId, ...data })
      .then(() => { if (flight.calls === 1) this._asked.set(entityId, flight.value); })
      .catch(() => {})
      .finally(() => {
        flight.calls -= 1;
        if (!flight.calls) this._flying.delete(entityId);
        this._sync();
      });
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
