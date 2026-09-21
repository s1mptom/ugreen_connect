/* Every port of the charger in one table, and the sessions that have ended
 * under it.
 *
 * Eight ports times six readings is forty-eight entities. As tiles that is
 * forty-eight cards and a screenful of chrome around numbers; the readings
 * belong in rows, which is what this draws.
 *
 * Config:
 *   type: custom:ugreen-ports-card
 *   device_id: <the charger>        # optional if only one charger is set up
 *   title: Ports                    # optional
 *   ports: true                     # the port table itself; default true
 *   sessions: true                  # the ended-sessions table; default true
 *   max_power: 300                  # what the row bars scale to; default: the
 *                                   # largest total this card has seen
 */

import {
  mount, SERIES, SHARED_CSS, defineCard, duration, findOne, num, ports, since, translator,
} from './ugreen-ui.js';

const TEXT = {
  en: {
    title: 'Ports',
    hint: 'tap a row for history and session detail',
    port: 'Port',
    power: 'Power',
    volts: 'Volts',
    amps: 'Amps',
    sinceCol: 'Since',
    protocol: 'Protocol',
    session: 'This session',
    sessions: 'Sessions that ended',
    delivered: 'Delivered',
    peak: 'Peak',
    lasted: 'Lasted',
    ended: 'Ended',
    nothing: 'nothing plugged in',
    cable: 'cable only',
    none: 'none',
    noPorts: 'No charger entities found. Set device_id in the card config.',
    noSessions: 'Nothing has finished charging yet.',
    justNow: 'just now',
  },
  de: {
    title: 'Anschlüsse',
    hint: 'Zeile antippen für Verlauf und Ladedetails',
    port: 'Anschluss',
    power: 'Leistung',
    volts: 'Volt',
    amps: 'Ampere',
    sinceCol: 'Seit',
    protocol: 'Protokoll',
    session: 'Aktuelle Ladung',
    sessions: 'Beendete Ladungen',
    delivered: 'Geliefert',
    peak: 'Spitze',
    lasted: 'Dauer',
    ended: 'Beendet',
    nothing: 'nichts angeschlossen',
    cable: 'nur Kabel',
    none: 'keins',
    noPorts: 'Keine Entitäten gefunden. device_id in der Kartenkonfiguration setzen.',
    noSessions: 'Noch nichts fertig geladen.',
    justNow: 'gerade eben',
  },
  ru: {
    title: 'Порты',
    hint: 'строка открывает историю и детали зарядки',
    port: 'Порт',
    power: 'Мощность',
    volts: 'Вольты',
    amps: 'Амперы',
    sinceCol: 'Идёт',
    protocol: 'Протокол',
    session: 'Текущая зарядка',
    sessions: 'Завершённые зарядки',
    delivered: 'Отдано',
    peak: 'Пик',
    lasted: 'Длилась',
    ended: 'Когда',
    nothing: 'ничего не подключено',
    cable: 'только кабель',
    none: 'нет',
    noPorts: 'Сущности не найдены. Укажите device_id в настройках карточки.',
    noSessions: 'Ни одна зарядка ещё не закончилась.',
    justNow: 'только что',
  },
};

const STYLE = `
  ${SHARED_CSS}
  ha-card { height: 100%; box-sizing: border-box; }
  .body { padding: 14px 16px 10px; display: flex; flex-direction: column; gap: 8px;
          height: 100%; box-sizing: border-box; }
  .head { display: flex; align-items: baseline; gap: 10px; }
  .head h2 { margin: 0; font-size: 11px; font-weight: 400; text-transform: uppercase;
             letter-spacing: .10em; color: var(--secondary-text-color); flex-grow: 1; }
  .head .hint { font-size: 11px; color: var(--disabled-text-color); }
  /* Fixed, so eight rows of numbers line up in columns of their own width
   * rather than in whatever the widest reading of the moment asks for --
   * a table that reflows every poll is unreadable while it is charging. */
  table.u-table { table-layout: fixed; font-size: 13px; }
  table.u-table th { font-size: 11px; letter-spacing: .06em; padding: 0 10px 6px 0; }
  table.u-table td { padding: 7px 10px 7px 0; }
  table.u-table th:last-child, table.u-table td:last-child { padding-right: 0; }
  .name { display: flex; align-items: center; gap: 7px; white-space: nowrap;
          font-size: 14px; font-weight: 500; }
  .u-dot { width: 7px; height: 7px; }
  tr.idle td { color: var(--secondary-text-color); }
  tr.idle .name { font-weight: 400; }
  td.watts { font-size: 15px; }
  td.volts, td.amps { font-size: 13px; }
  td.protocol { font-size: 12px; }
  td.since { font-size: 12px; }
  /* The bar belongs beside what it measures, not beside the number it repeats:
   * against the session text it reads as how much of the charger this port is
   * taking, which is the question a row of eight ports is asked. */
  .session { display: flex; align-items: center; gap: 8px; }
  .session .u-meter { flex-grow: 1; height: 6px; }
  .session .text { font-size: 12px; width: 104px; flex: none; white-space: nowrap;
                   overflow: hidden; text-overflow: ellipsis; }
  h3 { margin: 8px 0 0; font-size: 11px; font-weight: 400; text-transform: uppercase;
       letter-spacing: .10em; color: var(--secondary-text-color); }
  table.sessions { font-size: 13px; }
  table.sessions thead { display: none; }
  table.sessions .port { font-size: 13px; font-weight: 500; }
  table.sessions .energy { color: var(--state-icon-active-color, var(--primary-color)); }
  table.sessions .peak, table.sessions .lasted, table.sessions .when { font-size: 12px; }
  table.sessions .when { text-align: right; color: var(--disabled-text-color); }
`;

class UgreenPortsCard extends HTMLElement {
  static getStubConfig() { return { device_id: '', sessions: true }; }

  setConfig(config) {
    this._config = config || {};
    this._built = false;
    this._peak = 0;
    if (this.shadowRoot) this.shadowRoot.innerHTML = '';
  }

  set hass(hass) {
    this._hass = hass;
    this._t = translator(TEXT, hass);
    this._build();
    this._sync();
  }

  getCardSize() { return this._config.sessions === false ? 6 : 10; }

  _build() {
    if (this._built) return;
    this._built = true;
    this._root = mount(this, `
      <ha-card>
        <style>${STYLE}</style>
        <div class="body">
          <div class="head">
            <h2>${
              this._config.title
              ?? (this._config.ports === false ? this._t('sessions') : this._t('title'))
            }</h2>
            <span class="hint ports-hint">${this._t('hint')}</span>
          </div>
          <table class="u-table ports">
            <colgroup>
              <col style="width: 58px"><col style="width: 74px"><col style="width: 66px">
              <col style="width: 66px"><col style="width: 96px"><col><col style="width: 96px">
            </colgroup>
            <thead>
              <tr>
                <th>${this._t('port')}</th>
                <th class="num">${this._t('power')}</th>
                <th class="num u-narrow-hide">${this._t('volts')}</th>
                <th class="num u-narrow-hide">${this._t('amps')}</th>
                <th class="u-narrow-hide">${this._t('protocol')}</th>
                <th>${this._t('session')}</th>
                <th class="num u-narrow-hide">${this._t('sinceCol')}</th>
              </tr>
            </thead>
            <tbody></tbody>
          </table>
          <h3 class="sessions-head">${this._t('sessions')}</h3>
          <table class="u-table sessions">
            <colgroup>
              <col style="width: 44px"><col style="width: 84px"><col style="width: 76px">
              <col><col style="width: 120px">
            </colgroup>
            <thead>
              <tr>
                <th>${this._t('port')}</th>
                <th class="num">${this._t('delivered')}</th>
                <th class="num u-narrow-hide">${this._t('peak')}</th>
                <th class="u-narrow-hide">${this._t('lasted')}</th>
                <th>${this._t('ended')}</th>
              </tr>
            </thead>
            <tbody></tbody>
          </table>
          <div class="u-empty" hidden></div>
        </div>
      </ha-card>
    `);
    this._els = {
      hint: this._root.querySelector('.ports-hint'),
      portsTable: this._root.querySelector('table.ports'),
      ports: this._root.querySelector('table.ports tbody'),
      sessionsHead: this._root.querySelector('.sessions-head'),
      sessions: this._root.querySelector('table.sessions'),
      sessionRows: this._root.querySelector('table.sessions tbody'),
      empty: this._root.querySelector('.u-empty'),
    };
  }

  _sync() {
    if (!this._hass) return;
    const found = ports(this._hass, this._config.device_id);
    const showPorts = this._config.ports !== false && found.length > 0;
    const showSessions = this._config.sessions !== false && found.length > 0;

    this._els.empty.hidden = found.length > 0;
    this._els.empty.textContent = found.length ? '' : this._t('noPorts');
    this._els.portsTable.hidden = !showPorts;
    this._els.hint.hidden = !showPorts;
    this._els.sessionsHead.hidden = !showSessions || !showPorts;
    this._els.sessions.hidden = !showSessions;
    if (!found.length) {
      this._els.ports.replaceChildren();
      this._els.sessionRows.replaceChildren();
      return;
    }

    const totalId = findOne(this._hass, this._config.device_id, 'sensor', '_total_power');
    const total = totalId
      ? num(this._hass, totalId)
      : found.reduce((sum, port) => sum + num(this._hass, port.id), 0);
    this._peak = Math.max(this._peak, total, 1);
    const scale = Number(this._config.max_power) || this._peak;

    if (showPorts) {
      this._els.ports.replaceChildren(
        ...found.map((port, index) => this._portRow(port, scale, SERIES[index % SERIES.length])),
      );
    }
    if (showSessions) this._sessionRows(found);
  }

  _portRow(port, scale, colour) {
    const watts = num(this._hass, port.id);
    const volts = num(this._hass, `${port.base}_voltage`);
    const amps = num(this._hass, `${port.base}_current`);
    const protocol = this._hass.states[`${port.base}_protocol`]?.state;
    const drawing = this._hass.states[port.charging]?.state === 'on';

    const row = document.createElement('tr');
    row.className = drawing ? 'click u-on' : 'click idle';
    row.innerHTML = `
      <td><span class="name"><span class="u-dot" style="${
        drawing ? `background: ${colour}` : ''}"></span>${port.name}</span></td>
      <td class="num watts" style="${drawing ? `color: ${colour}` : ''}">${watts.toFixed(1)} W</td>
      <td class="num volts u-narrow-hide u-muted">${volts.toFixed(1)} V</td>
      <td class="num amps u-narrow-hide u-muted">${amps.toFixed(2)} A</td>
      <td class="protocol u-narrow-hide">${
        protocol && protocol !== 'none' ? protocol : `<span class="u-muted">${this._t('none')}</span>`
      }</td>
      <td><span class="session">
        <span class="u-meter"><i style="width: ${
          Math.min(100, (watts / scale) * 100).toFixed(0)}%; background: ${colour}"></i></span>
        <span class="text u-muted">${this._sessionText(port, volts)}</span>
      </span></td>
      <td class="num since u-narrow-hide u-muted">${this._sinceText(port)}</td>
    `;
    row.addEventListener('click', () => this._moreInfo(port.id));
    return row;
  }

  /* How long the bout on this port has been running, or how long ago the last
   * one ended. */
  _sinceText(port) {
    const energy = this._hass.states[`${port.base}_session_energy`];
    const started = energy?.attributes?.started;
    const ended = energy?.attributes?.ended;
    const stamp = energy?.attributes?.charging ? started : ended;
    if (!stamp) return '—';
    const at = new Date(stamp);
    return Number.isNaN(at.valueOf()) ? '—' : since(this._hass, at, this._t('justNow'));
  }

  /* What this port has taken in, or why it has taken nothing. */
  _sessionText(port, volts) {
    const energy = this._hass.states[`${port.base}_session_energy`];
    const wh = parseFloat(energy?.state);
    if (!Number.isFinite(wh) || wh <= 0) {
      return volts > 0.5 ? this._t('cable') : this._t('nothing');
    }
    const charge = num(this._hass, `${port.base}_session_charge`);
    const seconds = Number(energy.attributes.duration) || 0;
    const parts = [`${wh.toFixed(1)} Wh`];
    if (charge > 0) parts.push(`${Math.round(charge)} mAh`);
    if (seconds) parts.push(duration(seconds));
    return parts.join(' · ');
  }

  _sessionRows(found) {
    const rows = found
      .map((port) => {
        const event = this._hass.states[port.event];
        if (!event || event.attributes.event_type !== 'ended') return null;
        const at = new Date(event.state);
        return Number.isNaN(at.valueOf()) ? null : { port, event, at };
      })
      .filter(Boolean)
      .sort((a, b) => b.at - a.at);

    if (!rows.length) {
      const empty = document.createElement('tr');
      empty.innerHTML = `<td colspan="5" class="u-empty">${this._t('noSessions')}</td>`;
      this._els.sessionRows.replaceChildren(empty);
      return;
    }

    this._els.sessionRows.replaceChildren(...rows.map(({ port, event, at }) => {
      const row = document.createElement('tr');
      row.className = 'click';
      const wh = Number(event.attributes.energy_wh) || 0;
      const peak = Number(event.attributes.peak_power) || 0;
      const protocol = event.attributes.protocol;
      row.innerHTML = `
        <td class="port">${port.name}</td>
        <td class="num energy">${wh.toFixed(1)} Wh</td>
        <td class="num peak u-narrow-hide u-muted">${peak.toFixed(1)} W</td>
        <td class="lasted u-narrow-hide u-muted">${duration(Number(event.attributes.duration) || 0)}${
          protocol && protocol !== 'none' ? ` · ${protocol}` : ''}</td>
        <td class="when">${since(this._hass, at, this._t('justNow'))}</td>
      `;
      row.addEventListener('click', () => this._moreInfo(`${port.base}_session_energy`));
      return row;
    }));
  }

  _moreInfo(entityId) {
    if (!entityId) return;
    this.dispatchEvent(new CustomEvent('hass-more-info', {
      detail: { entityId }, bubbles: true, composed: true,
    }));
  }
}

defineCard('ugreen-ports-card', UgreenPortsCard, {
  name: 'UGREEN Ports',
  description: 'Every port of a UGREEN charger in one table, with the sessions that ended',
});
