/* The whole charger on one screen, laid out here rather than by the dashboard.
 *
 * Home Assistant's sections put cards in columns of equal width and let them
 * grow as they like; a charger's screen wants the opposite. So this card owns
 * the layout and hands the pieces to the cards that already draw them:
 *
 *   the charger          -- what it delivers, out of what it can, and the mode
 *   the front panel      -- one tile per port, in the order they sit on it
 *   the chart            -- the widest thing below, since it is read the most
 *   and beside it        -- what finished, the week's energy, and the screen
 *
 * Put it in a panel view (`type: panel`) and it fills the screen. Narrower than
 * the chart and its column can share, it folds to one column; the pieces are
 * the same cards anyone can use on their own.
 *
 * With several chargers added and no `device_id`, the chosen one's name heads
 * the screen, and the others are a list behind it; each viewer's choice is
 * kept in their browser. With `device_id`, the card shows that charger and no
 * name.
 *
 * Config:
 *   type: custom:ugreen-dashboard-card
 *   device_id: <the charger>        # optional; without it, a choice if several
 *   max_power: 300                  # the budget of one charger; several each
 *                                   # take their own model's
 *   hours: 3                        # the chart's range to start on
 *   period: week                    # the energy card's period
 *   fill: true                      # in a panel view, take the height under
 *                                   # the toolbar; false to size to content
 */

import {
  SHARED_CSS, applyTheme, chargerNames, chargers, defineCard, mount, translator,
} from './ugreen-ui.js';

// The pieces this lays out. Imported rather than assumed: a dashboard can load
// this module first, and an element that is not defined yet is an unknown tag
// with no setConfig on it.
import './ugreen-charger-card.js';
import './ugreen-energy-card.js';
import './ugreen-ports-card.js';
import './ugreen-power-card.js';
import './ugreen-sessions-card.js';
import './ugreen-wallpaper-card.js';

const TEXT = {
  en: { chargers: 'Chargers' },
  de: { chargers: 'Ladegeräte' },
  ru: { chargers: 'Зарядки' },
};

// Where a viewer's choice of charger is kept, in their own browser.
const CHOSEN_KEY = 'ugreen-dashboard:charger';

const STYLE = `
  ${SHARED_CSS}
  /* The charger's name as the screen's title, with the others behind it: a
   * native list over the name, so it opens the way a list does on each device. */
  .title { position: relative; display: inline-flex; align-items: center; gap: 10px; align-self: flex-start;
           color: var(--primary-text-color); cursor: pointer; }
  .title .nm { font-size: 22px; font-weight: 500; }
  .title svg { color: var(--secondary-text-color); }
  .title select { position: absolute; inset: 0; opacity: 0; cursor: pointer; font-size: 16px; }
  .title:focus-within { outline: 2px solid var(--primary-color); outline-offset: 4px; border-radius: 6px; }
  @container (max-width: 640px) {
    .title .nm { font-size: 19px; }
  }
  :host { display: block; padding: 20px; box-sizing: border-box; container-type: inline-size; }
  .screen { display: flex; flex-direction: column; gap: 16px; }
  .below { display: grid; grid-template-columns: minmax(0, 1fr) 464px; gap: 16px; align-items: stretch; }
  .side { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
  /* Height the column has spare goes to the finished sessions, which can use
   * it for more rows; the screen card stretched instead was mostly empty. */
  .side > :first-child { flex-grow: 1; }
  /* Given a panel view to itself, the screen is filled the way the design
   * draws it: the row below takes whatever height is left under the toolbar,
   * and the chart, which is read the most, takes it within the row. On a
   * screen too short for that, this changes nothing. */
  :host([fill]) { min-height: calc(100vh - var(--header-height, 56px)); display: flex; flex-direction: column; }
  :host([fill]) .screen { flex-grow: 1; }
  :host([fill]) .below { flex-grow: 1; }
  /* One column once the chart would have less room than its side column. */
  @container (max-width: 1060px) {
    :host { padding: 12px; }
    .below { grid-template-columns: minmax(0, 1fr); }
  }
`;

class UgreenDashboardCard extends HTMLElement {
  static getStubConfig() { return { device_id: '', max_power: 300 }; }

  setConfig(config) {
    this._config = config || {};
    this._built = false;
    // Read once: the card is handed a new hass on every state change anywhere.
    try { this._kept = localStorage.getItem(CHOSEN_KEY); } catch { this._kept = null; }
    if (this.shadowRoot) this.shadowRoot.innerHTML = '';
  }

  set hass(hass) {
    this._hass = hass;
    this._t = translator(TEXT, hass);
    applyTheme(this, hass);
    this.toggleAttribute('fill', this._config.fill !== false && this._inPanel());
    // Pinned by its config, or the viewer's choice among those added.
    const list = this._config.device_id ? [] : chargers(hass);
    this._several = list.length > 1;
    const chosen = this._config.device_id || this._chosen(list);
    if (this._built && chosen !== this._for) {
      this._built = false;
    }
    this._for = chosen;
    this._build();
    this._syncTabs(list, chosen);
    for (const card of this._cards || []) {
      if (typeof card.setConfig === 'function') card.hass = hass;
    }
  }

  /* The charger this viewer picked last, while it is still one of them: on
   * this page first, then as their browser kept it, then the first one. */
  _chosen(list) {
    for (const id of [this._picked, this._kept]) {
      if (id && list.some((c) => c.id === id)) return id;
    }
    return list[0]?.id;
  }

  _choose(id) {
    try { localStorage.setItem(CHOSEN_KEY, id); } catch { /* kept for this page only */ }
    this._picked = id;
    if (this._hass) this.hass = this._hass;
  }

  /* The chosen charger's name, and a list of the others behind it. */
  _syncTabs(list, chosen) {
    const row = this._root?.querySelector('.chargers');
    if (!row) return;
    row.hidden = list.length < 2;
    if (row.hidden) return;
    const names = chargerNames(list);
    const at = Math.max(0, list.findIndex((c) => c.id === chosen));
    if (!row.firstElementChild) {
      row.innerHTML = `<label class="title"><span class="nm"></span><svg width="12" height="8"
        viewBox="0 0 12 8" aria-hidden="true"><path d="M1 1.5l5 5 5-5" fill="none" stroke="currentColor"
        stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"></path></svg><select></select></label>`;
      const pick = row.querySelector('select');
      pick.setAttribute('aria-label', this._t('chargers'));
      pick.addEventListener('change', () => this._choose(pick.value));
    }
    row.querySelector('.nm').textContent = names[at];
    const pick = row.querySelector('select');
    const wanted = list.map((c, i) => `${c.id}\n${names[i]}`).join('\n');
    if (pick.dataset.list !== wanted) {
      pick.dataset.list = wanted;
      pick.replaceChildren(...list.map((charger, i) => {
        const option = document.createElement('option');
        option.value = charger.id;
        option.textContent = names[i];
        return option;
      }));
    }
    pick.value = chosen;
  }

  getCardSize() { return 20; }

  /* Whether this card is the whole of a panel view. Looked for up through the
   * shadow roots it sits in, since that is where Lovelace puts it. */
  _inPanel() {
    let node = this;
    for (let step = 0; node && step < 12; step += 1) {
      if (node.localName === 'hui-panel-view') return true;
      node = node.parentNode || node.host;
    }
    return false;
  }

  /* One child card, configured from this card's own options.
   *
   * Upgraded by hand if need be: the imports above define every tag used here,
   * but a browser that has not run them yet hands back an unknown element, and
   * an unknown element has no setConfig. */
  _card(tag, extra) {
    const card = document.createElement(tag);
    const config = {
      type: `custom:${tag}`,
      device_id: this._for,
      // The config's budget where it is about one charger. Switching between
      // several, none: each charger card takes its own from its model, as it
      // reads it, since a 160W is not out of 300.
      max_power: this._several ? undefined : this._config.max_power,
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
        <div class="chargers" hidden></div>
        <div class="below"><div class="side"></div></div>
      </div>
    `);
    const screen = this._root.querySelector('.screen');
    const below = this._root.querySelector('.below');
    const side = this._root.querySelector('.side');

    const charger = this._card('ugreen-charger-card', {});
    const panel = this._card('ugreen-ports-card', {});
    const power = this._card('ugreen-power-card', { hours: this._config.hours ?? 3 });
    const finished = this._card('ugreen-sessions-card', {});
    const energy = this._card('ugreen-energy-card', { period: this._config.period ?? 'week' });
    const wallpaper = this._card('ugreen-wallpaper-card', {});

    screen.querySelector('.chargers').after(charger, panel);
    below.prepend(power);
    side.append(finished, energy, wallpaper);
    this._cards = [charger, panel, power, finished, energy, wallpaper];
  }
}

defineCard('ugreen-dashboard-card', UgreenDashboardCard, {
  name: 'UGREEN Charger dashboard',
  description: 'The whole charger on one screen: the budget, the ports, the chart, what finished, the energy and the screen',
});
