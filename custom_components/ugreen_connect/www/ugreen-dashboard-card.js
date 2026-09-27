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
 * Config:
 *   type: custom:ugreen-dashboard-card
 *   device_id: <the charger>        # optional if only one charger is set up
 *   max_power: 300                  # the charger's budget
 *   hours: 3                        # the chart's range to start on
 *   period: week                    # the energy card's period
 */

import { SHARED_CSS, applyTheme, defineCard, mount } from './ugreen-ui.js';

// The pieces this lays out. Imported rather than assumed: a dashboard can load
// this module first, and an element that is not defined yet is an unknown tag
// with no setConfig on it.
import './ugreen-charger-card.js';
import './ugreen-energy-card.js';
import './ugreen-ports-card.js';
import './ugreen-power-card.js';
import './ugreen-sessions-card.js';
import './ugreen-wallpaper-card.js';

const STYLE = `
  ${SHARED_CSS}
  :host { display: block; padding: 20px; box-sizing: border-box; container-type: inline-size; }
  .screen { display: flex; flex-direction: column; gap: 16px; }
  .below { display: grid; grid-template-columns: minmax(0, 1fr) 464px; gap: 16px; align-items: stretch; }
  .side { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
  .side > :last-child { flex-grow: 1; }
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
    if (this.shadowRoot) this.shadowRoot.innerHTML = '';
  }

  set hass(hass) {
    this._hass = hass;
    applyTheme(this, hass);
    this._build();
    for (const card of this._cards || []) {
      if (typeof card.setConfig === 'function') card.hass = hass;
    }
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

    screen.prepend(charger, panel);
    below.prepend(power);
    side.append(finished, energy, wallpaper);
    this._cards = [charger, panel, power, finished, energy, wallpaper];
  }
}

defineCard('ugreen-dashboard-card', UgreenDashboardCard, {
  name: 'UGREEN Charger dashboard',
  description: 'The whole charger on one screen: the budget, the ports, the chart, what finished, the energy and the screen',
});
