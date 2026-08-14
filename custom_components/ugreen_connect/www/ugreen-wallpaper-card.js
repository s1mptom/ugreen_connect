/*
 * Screensaver settings for UGREEN chargers, laid out the way the app does it:
 * a switch, and under it the settings that only matter once it is on --
 * time format, clock style, and the picture.
 *
 * The screen is 560x170, so a photo almost never suits it as taken. Picking one
 * opens the same kind of editor the app has: the crop window over the image,
 * dragged, zoomed and rotated until the right part is inside it.
 *
 * Config:
 *   type: custom:ugreen-wallpaper-card
 *   device_id: <the charger>        # optional if the entities are found
 *   title: Screensaver              # optional
 */

const OUT_W = 560;
const OUT_H = 170;
const RATIO = OUT_W / OUT_H;

/* Everything the card says, in one place.
 *
 * To add a language: copy the whole `en` block, key it by the language code
 * Home Assistant uses ("de", "fr", "ru", ...), and translate the values. Keys
 * that are missing fall back to English, so a partial translation is fine, and
 * `{n}` in a value is replaced by a number. */
const TEXT = {
  en: {
    title: 'Screensaver',
    timeFormat: 'Time format',
    hours12: '12 h',
    hours24: '24 h',
    clockStyle: 'Clock style',
    style1: 'Style 1',
    style2: 'Style 2',
    wallpaper: 'Wallpaper',
    none: 'None',
    stockPicture: 'Wallpaper {n}',
    customWallpaper: 'Custom wallpaper',
    yours: 'Yours',
    ownPicture: 'Picture {n}',
    upload: 'Upload a picture',
    rotate: 'Rotate 90°',
    reset: 'Reset',
    cancel: 'Cancel',
    apply: 'Use this',
    dragHint: 'Drag to move, scroll or pinch to zoom.',
    notAnImage: 'That file is not an image',
    needDevice: 'Set device_id in the card config',
    uploading: 'Uploading…',
    sent: 'Done — the charger is fetching it now.',
    failed: 'Upload failed',
  },
};

const css = `
  .body { padding: 16px; display: flex; flex-direction: column; gap: 16px; }
  .head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
  .head h2 { margin: 0; font-size: 1.15em; font-weight: 500; }
  .settings { display: flex; flex-direction: column; gap: 14px; }
  .settings[hidden] { display: none; }
  .field { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
  .field > span { color: var(--primary-text-color); }
  .seg { display: inline-flex; border: 1px solid var(--divider-color); border-radius: 8px;
         overflow: hidden; }
  .seg button { border: 0; background: transparent; color: var(--primary-text-color);
                padding: 7px 14px; cursor: pointer; font: inherit; }
  .seg button[aria-pressed="true"] { background: var(--primary-color);
                                     color: var(--text-primary-color); }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
          gap: 10px 10px; }
  .cell { display: flex; flex-direction: column; gap: 5px; }
  .cell .lab { font-size: .8em; color: var(--secondary-text-color); text-align: center;
               white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .tile { position: relative; border: 2px solid transparent; border-radius: 8px;
          overflow: hidden; cursor: pointer; background: var(--secondary-background-color);
          aspect-ratio: ${RATIO}; padding: 0; container-type: size; }
  /* Pictures are held rotated a quarter turn anticlockwise, so the preview is
     turned back. The box takes the tile's sides swapped, which after the
     rotation lands exactly on it. The sides are read as container units rather
     than percentages: a percentage would be measured against a box the tile's
     own border has already shrunk out of ratio, leaving bare strips at both
     ends once the picture is turned. */
  .tile img { position: absolute; top: 50%; left: 50%;
              width: 100cqh; height: 100cqw; object-fit: cover;
              transform: translate(-50%, -50%) rotate(90deg); display: block; }
  .tile[aria-pressed="true"] { border-color: var(--success-color, #4caf50); }
  .tile.plain { display: flex; align-items: center; justify-content: center;
                color: var(--secondary-text-color); font-size: .85em; }
  /* The app marks the chosen picture and clock with a green tick in the corner;
     a border alone is easy to miss on a dark tile. */
  .mark { position: absolute; right: 4px; bottom: 4px; width: 18px; height: 18px;
          border-radius: 50%; background: var(--success-color, #4caf50); color: #fff;
          display: none; align-items: center; justify-content: center;
          font-size: 12px; line-height: 1; }
  [aria-pressed="true"] > .mark { display: flex; }
  .styles { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .style { display: flex; flex-direction: column; gap: 5px; }
  .style .lab { font-size: .8em; color: var(--secondary-text-color); text-align: center; }
  /* The tile is its own container so the clock scales with the tile rather
     than with the page. */
  .clock { container-type: inline-size; position: relative; width: 100%;
           border: 2px solid transparent; border-radius: 8px; padding: 0;
           cursor: pointer; background: #0b0b0b; overflow: hidden;
           aspect-ratio: ${RATIO}; }
  .clock[aria-pressed="true"] { border-color: var(--success-color, #4caf50); }
  /* Both styles hang the date under the time, flush with it; what the style
     changes is where that pair sits -- middle for one, left for the other. */
  .face { position: absolute; inset: 0; display: flex; align-items: center;
          color: #fff; line-height: 1.1; }
  .face.centre { justify-content: center; }
  .face.left { justify-content: flex-start; padding-left: 7%; }
  .face .blk { display: flex; flex-direction: column; align-items: flex-start; }
  .face b { font-size: var(--fs, 15cqw); font-weight: 700; letter-spacing: .01em; }
  .face i { font-size: var(--fd, 5cqw); font-style: normal; opacity: .85;
            letter-spacing: .1em; margin-top: 2cqw; }
  /* The picture below is the charger's own, so the clock needs its own contrast. */
  .hero .face { text-shadow: 0 1px 6px rgba(0,0,0,.65); --fs: 10cqw; --fd: 3.6cqw; }
  .hero { position: relative; width: 100%; aspect-ratio: ${RATIO}; border-radius: 10px;
          overflow: hidden; background: #0b0b0b; container-type: size; }
  .hero img { position: absolute; top: 50%; left: 50%;
              width: 100cqh; height: 100cqw; object-fit: cover;
              transform: translate(-50%, -50%) rotate(90deg); display: block; }
  .stage { position: relative; width: 100%; aspect-ratio: ${RATIO * 1.5};
           background: #202020; border-radius: 8px; overflow: hidden;
           touch-action: none; cursor: grab; }
  .stage.dragging { cursor: grabbing; }
  .stage canvas { width: 100%; height: 100%; display: block; }
  .row { display: flex; gap: 8px; flex-wrap: wrap; }
  .btn { border: 1px solid var(--divider-color); background: var(--card-background-color);
         color: var(--primary-text-color); border-radius: 8px; padding: 8px 14px;
         cursor: pointer; font: inherit; }
  .btn.primary { background: var(--primary-color); color: var(--text-primary-color);
                 border-color: transparent; }
  .btn[disabled] { opacity: .5; cursor: default; }
  .status { min-height: 1.2em; font-size: .9em; color: var(--secondary-text-color); }
  .status.error { color: var(--error-color); }
  .editor[hidden] { display: none; }
`;

class UgreenWallpaperCard extends HTMLElement {
  static getStubConfig() { return { device_id: '' }; }

  setConfig(config) {
    this._config = config;
    this._image = null;
    this._built = false;
    this.innerHTML = '';
  }

  set hass(hass) {
    this._hass = hass;
    this._build();
    this._sync();
  }

  getCardSize() { return 8; }

  /* One string, in the viewer's language where there is one. */
  _t(key, vars) {
    const lang = (this._hass?.locale?.language || this._hass?.language || 'en')
      .toLowerCase().split('-')[0];
    const text = (TEXT[lang] || {})[key] ?? TEXT.en[key] ?? key;
    return vars ? text.replace(/\{(\w+)\}/g, (_, name) => vars[name]) : text;
  }

  /* Entities ------------------------------------------------------------ */

  _find(domain, suffix) {
    const wanted = `${domain}.`;
    return Object.keys(this._hass?.states || {}).find((id) => {
      if (!id.startsWith(wanted) || !id.endsWith(suffix)) return false;
      const attrs = this._hass.states[id].attributes;
      if (!this._config.device_id) return id.includes('ugreen');
      return attrs.device_id ? attrs.device_id === this._config.device_id : id.includes('ugreen');
    });
  }

  _entities() {
    return {
      screensaver: this._find('switch', '_screensaver'),
      format: this._find('select', '_time_format'),
      style: this._find('select', '_clock_style'),
      wallpaper: this._find('select', '_wallpaper'),
    };
  }

  _state(id) { return id ? this._hass.states[id] : undefined; }

  /* Rendering ----------------------------------------------------------- */

  _build() {
    if (this._built) return;
    this._built = true;
    this.innerHTML = `
      <ha-card>
        <div class="body">
          <div class="head">
            <h2>${this._config.title || this._t('title')}</h2>
            <ha-switch class="power"></ha-switch>
          </div>
          <div class="settings">
            <div class="hero"><span class="face centre"><span class="blk"><b></b><i></i></span></span></div>
            <div class="field fmt"><span>${this._t('timeFormat')}</span>
              <span class="seg">
                <button data-fmt="12h" aria-pressed="false">${this._t('hours12')}</button>
                <button data-fmt="24h" aria-pressed="false">${this._t('hours24')}</button>
              </span>
            </div>
            <div>
              <div class="field"><span>${this._t('clockStyle')}</span></div>
              <div class="styles">
                <div class="style">
                  <button class="clock" data-sty="style_1" aria-pressed="false">
                    <span class="face centre"><span class="blk"><b>09:00</b><i>MON, JUN 09</i></span></span>
                    <span class="mark">✓</span>
                  </button>
                  <span class="lab">${this._t('style1')}</span>
                </div>
                <div class="style">
                  <button class="clock" data-sty="style_2" aria-pressed="false">
                    <span class="face left"><span class="blk"><b>09:00</b><i>MON, JUN 09</i></span></span>
                    <span class="mark">✓</span>
                  </button>
                  <span class="lab">${this._t('style2')}</span>
                </div>
              </div>
            </div>
            <div>
              <div class="field"><span>${this._t('wallpaper')}</span></div>
              <div class="grid"></div>
            </div>
            <div class="own" hidden>
              <div class="field"><span>${this._t('customWallpaper')}</span></div>
              <div class="grid mine"></div>
            </div>
            <div class="row">
              <label class="btn primary">${this._t('upload')}<input type="file" accept="image/*" hidden></label>
            </div>
            <div class="editor" hidden>
              <div class="stage"><canvas></canvas></div>
              <div class="row" style="margin-top:10px">
                <button class="btn" data-act="rot">${this._t('rotate')}</button>
                <button class="btn" data-act="fit">${this._t('reset')}</button>
                <button class="btn" data-act="cancel">${this._t('cancel')}</button>
                <button class="btn primary" data-act="apply">${this._t('apply')}</button>
              </div>
            </div>
            <div class="status"></div>
          </div>
        </div>
      </ha-card>
      <style>${css}</style>`;

    this._power = this.querySelector('.power');
    this._settings = this.querySelector('.settings');
    this._hero = this.querySelector('.hero');
    this._grid = this.querySelector('.grid');
    this._mine = this.querySelector('.grid.mine');
    this._own = this.querySelector('.own');
    this._statusEl = this.querySelector('.status');
    this._editor = this.querySelector('.editor');
    this._stage = this.querySelector('.stage');
    this._canvas = this.querySelector('canvas');

    this._power.addEventListener('change', () => {
      const id = this._entities().screensaver;
      if (id) {
        this._hass.callService('switch', this._power.checked ? 'turn_on' : 'turn_off',
          { entity_id: id });
      }
    });
    this.querySelectorAll('[data-fmt]').forEach((b) => b.addEventListener('click', () => {
      this._select(this._entities().format, b.dataset.fmt);
    }));
    this.querySelectorAll('[data-sty]').forEach((b) => b.addEventListener('click', () => {
      this._select(this._entities().style, b.dataset.sty);
    }));
    this.querySelector('input[type=file]').addEventListener('change', (e) => {
      const file = e.target.files && e.target.files[0];
      e.target.value = '';
      if (file) this._open(file);
    });
    this.querySelector('[data-act=rot]').addEventListener('click', () => {
      this._angle += Math.PI / 2; this._fit(true);
    });
    this.querySelector('[data-act=fit]').addEventListener('click', () => {
      this._angle = 0; this._fit(true);
    });
    this.querySelector('[data-act=cancel]').addEventListener('click', () => this._close());
    this.querySelector('[data-act=apply]').addEventListener('click', () => this._upload());
    this._gestures();
    window.addEventListener('resize', () => this._draw());
  }

  _sync() {
    if (!this._built || !this._hass) return;
    const ent = this._entities();
    const on = this._state(ent.screensaver)?.state === 'on';
    this._power.checked = on;
    this._settings.hidden = !on;

    const fmt = this._state(ent.format)?.state;
    this.querySelectorAll('[data-fmt]').forEach((b) => {
      b.setAttribute('aria-pressed', String(b.dataset.fmt === fmt));
    });
    const sty = this._state(ent.style)?.state;
    this.querySelectorAll('[data-sty]').forEach((b) => {
      b.setAttribute('aria-pressed', String(b.dataset.sty === sty));
    });

    const wall = this._state(ent.wallpaper);
    const list = wall?.attributes?.wallpapers || [];
    const current = wall?.state;
    const signature = JSON.stringify([list.map((w) => w.id), current]);
    if (signature !== this._gridSig) {
      this._gridSig = signature;
      // Stock pictures and the owner's own are kept apart, as the app keeps them.
      const stock = list.filter((w) => w.stock);
      const mine = list.filter((w) => !w.stock);
      this._grid.innerHTML = '';
      this._grid.appendChild(this._tile({ id: 'none' }, this._t('none'), current === 'none'));
      stock.forEach((w, i) => this._grid.appendChild(
        this._tile(w, this._t('stockPicture', { n: i + 1 }), w.id === current)));
      this._mine.innerHTML = '';
      mine.forEach((w, i) => this._mine.appendChild(
        this._tile(w, mine.length > 1 ? this._t('ownPicture', { n: i + 1 }) : this._t('yours'),
                       w.id === current)));
      this._own.hidden = mine.length === 0;
    }
    this._drawHero(list, current, sty, fmt);
  }

  /* The strip the charger will actually show: the chosen picture with the clock
     over it, in the chosen style. The app leads with the same thing. */
  _drawHero(list, current, sty, fmt) {
    const pic = list.find((w) => w.id === current && (w.preview || w.url));
    const img = this._hero.querySelector('img');
    if (pic && img?.dataset.pic !== pic.id) {
      const target = img || this._hero.insertAdjacentElement('afterbegin', new Image());
      target.dataset.pic = pic.id;
      this._show(target, pic);
    } else if (!pic && img) {
      img.remove();
    }
    const face = this._hero.querySelector('.face');
    face.className = `face ${sty === 'style_2' ? 'left' : 'centre'}`;
    const now = new Date();
    const h = now.getHours();
    const hh = fmt === '12h' ? (h % 12 || 12) : h;
    face.querySelector('b').textContent =
      `${fmt === '12h' ? hh : String(hh).padStart(2, '0')}:`
      + String(now.getMinutes()).padStart(2, '0')
      + (fmt === '12h' ? (h < 12 ? ' AM' : ' PM') : '');
    face.querySelector('i').textContent = now
      .toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: '2-digit' })
      .replace(/(\w+) (\w+) (\d+)/, '$1, $2 $3').toUpperCase();
    if (!this._tick) this._tick = setInterval(() => this._sync(), 20000);
  }

  disconnectedCallback() {
    if (this._tick) { clearInterval(this._tick); this._tick = null; }
    for (const url of Object.values(this._blobs || {})) URL.revokeObjectURL(url);
    this._blobs = {};
  }

  /* Point an <img> at a picture.

     The address the integration serves needs Home Assistant's own credentials,
     which an <img> never sends, so it is fetched here and handed over as a
     blob. The CDN link stays as a fallback -- it works for the first ten
     minutes after the card is drawn, which is better than a broken image. */
  async _show(img, w) {
    this._blobs ||= {};
    if (this._blobs[w.id]) { img.src = this._blobs[w.id]; return; }
    const token = this._hass?.auth?.data?.access_token;
    if (w.preview && token) {
      try {
        const reply = await fetch(w.preview, { headers: { authorization: `Bearer ${token}` } });
        if (reply.ok) {
          img.src = this._blobs[w.id] = URL.createObjectURL(await reply.blob());
          return;
        }
      } catch (err) { /* fall through to the CDN */ }
    }
    if (w.url) img.src = w.url;
  }

  _tile(w, label, active) {
    const cell = document.createElement('div');
    cell.className = 'cell';
    const el = document.createElement('button');
    const pic = w.preview || w.url;
    el.className = 'tile' + (pic ? '' : ' plain');
    el.setAttribute('aria-pressed', String(!!active));
    // The name goes under the tile either way, so a plain one is left empty.
    el.innerHTML = (pic ? '<img alt="">' : '') + '<span class="mark">✓</span>';
    if (pic) this._show(el.querySelector('img'), w);
    el.addEventListener('click', () => this._select(this._entities().wallpaper, w.id));
    cell.appendChild(el);
    const cap = document.createElement('span');
    cap.className = 'lab';
    cap.textContent = label;
    cell.appendChild(cap);
    return cell;
  }

  _select(entityId, option) {
    if (!entityId) return;
    this._hass.callService('select', 'select_option', { entity_id: entityId, option });
  }

  _status(text, bad) {
    this._statusEl.textContent = text || '';
    this._statusEl.classList.toggle('error', !!bad);
  }

  /* Editor -------------------------------------------------------------- */

  async _open(file) {
    const url = URL.createObjectURL(file);
    try {
      const img = new Image();
      await new Promise((ok, no) => {
        img.onload = ok;
        img.onerror = () => no(new Error(this._t('notAnImage')));
        img.src = url;
      });
      this._image = img;
      this._angle = 0;
      this._editor.hidden = false;
      this._fit(true);
      this._status(this._t('dragHint'));
    } catch (err) {
      this._status(err.message, true);
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  _close() {
    this._image = null;
    this._editor.hidden = true;
    this._status('');
  }

  _frame() {
    const w = this._canvas.width;
    const h = this._canvas.height;
    const fw = Math.min(w * 0.88, h * 0.88 * RATIO);
    return { x: (w - fw) / 2, y: (h - fw / RATIO) / 2, w: fw, h: fw / RATIO };
  }

  _size() {
    const swap = Math.abs(Math.sin(this._angle)) > 0.5;
    return swap ? { w: this._image.height, h: this._image.width }
                : { w: this._image.width, h: this._image.height };
  }

  _fit(centre) {
    if (!this._image) return;
    const rect = this._stage.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    this._canvas.width = Math.round(rect.width * dpr);
    this._canvas.height = Math.round(rect.height * dpr);
    const frame = this._frame();
    const size = this._size();
    // Never smaller than the window, or the wallpaper would have blank edges.
    this._min = Math.max(frame.w / size.w, frame.h / size.h);
    this._scale = this._min;
    if (centre) this._offset = { x: 0, y: 0 };
    this._clamp();
    this._draw();
  }

  _clamp() {
    const frame = this._frame();
    const size = this._size();
    const lx = Math.max(0, (size.w * this._scale - frame.w) / 2);
    const ly = Math.max(0, (size.h * this._scale - frame.h) / 2);
    this._offset.x = Math.min(lx, Math.max(-lx, this._offset.x));
    this._offset.y = Math.min(ly, Math.max(-ly, this._offset.y));
  }

  _draw() {
    if (!this._canvas || !this._image) return;
    const ctx = this._canvas.getContext('2d');
    const { width: w, height: h } = this._canvas;
    ctx.clearRect(0, 0, w, h);
    const frame = this._frame();
    ctx.save();
    ctx.translate(frame.x + frame.w / 2 + this._offset.x, frame.y + frame.h / 2 + this._offset.y);
    ctx.rotate(this._angle);
    ctx.scale(this._scale, this._scale);
    ctx.drawImage(this._image, -this._image.width / 2, -this._image.height / 2);
    ctx.restore();
    ctx.fillStyle = 'rgba(0,0,0,.55)';
    ctx.fillRect(0, 0, w, frame.y);
    ctx.fillRect(0, frame.y + frame.h, w, h - frame.y - frame.h);
    ctx.fillRect(0, frame.y, frame.x, frame.h);
    ctx.fillRect(frame.x + frame.w, frame.y, w - frame.x - frame.w, frame.h);
    ctx.strokeStyle = 'rgba(255,255,255,.9)';
    ctx.lineWidth = Math.max(1, window.devicePixelRatio || 1);
    ctx.strokeRect(frame.x, frame.y, frame.w, frame.h);
  }

  _gestures() {
    const stage = this._stage;
    const dpr = () => window.devicePixelRatio || 1;
    const points = new Map();
    let last = null;

    stage.addEventListener('pointerdown', (e) => {
      if (!this._image) return;
      points.set(e.pointerId, e);
      stage.setPointerCapture(e.pointerId);
      last = { x: e.clientX, y: e.clientY };
      stage.classList.add('dragging');
    });
    stage.addEventListener('pointermove', (e) => {
      if (!this._image || !points.has(e.pointerId)) return;
      points.set(e.pointerId, e);
      if (points.size === 2) {
        const [a, b] = [...points.values()];
        const dist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
        if (this._pinch) this._zoom(dist / this._pinch);
        this._pinch = dist;
        last = null;
        return;
      }
      if (!last) return;
      this._offset.x += (e.clientX - last.x) * dpr();
      this._offset.y += (e.clientY - last.y) * dpr();
      last = { x: e.clientX, y: e.clientY };
      this._clamp();
      this._draw();
    });
    const up = (e) => {
      points.delete(e.pointerId);
      this._pinch = null;
      last = null;
      stage.classList.remove('dragging');
    };
    stage.addEventListener('pointerup', up);
    stage.addEventListener('pointercancel', up);
    stage.addEventListener('wheel', (e) => {
      if (!this._image) return;
      e.preventDefault();
      this._zoom(Math.exp(-e.deltaY / 400));
    }, { passive: false });
  }

  _zoom(factor) {
    this._scale = Math.min(this._min * 8, Math.max(this._min, this._scale * factor));
    this._clamp();
    this._draw();
  }

  async _upload() {
    if (!this._image || this._busy) return;
    const deviceId = this._config.device_id
      || this._state(this._entities().wallpaper)?.attributes?.device_id;
    if (!deviceId) {
      this._status(this._t('needDevice'), true);
      return;
    }
    this._busy = true;
    const apply = this.querySelector('[data-act=apply]');
    apply.disabled = true;
    this._status(this._t('uploading'));

    const out = document.createElement('canvas');
    out.width = OUT_W;
    out.height = OUT_H;
    const ctx = out.getContext('2d');
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, OUT_W, OUT_H);
    const frame = this._frame();
    const k = OUT_W / frame.w;
    ctx.translate(OUT_W / 2 + this._offset.x * k, OUT_H / 2 + this._offset.y * k);
    ctx.rotate(this._angle);
    ctx.scale(this._scale * k, this._scale * k);
    ctx.drawImage(this._image, -this._image.width / 2, -this._image.height / 2);

    try {
      await this._hass.callService('ugreen_connect', 'set_wallpaper', {
        device_id: deviceId,
        image: out.toDataURL('image/jpeg', 0.9),
      });
      this._status(this._t('sent'));
      this._close();
    } catch (err) {
      this._status(err?.message || this._t('failed'), true);
    } finally {
      this._busy = false;
      apply.disabled = false;
    }
  }
}

customElements.define('ugreen-wallpaper-card', UgreenWallpaperCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: 'ugreen-wallpaper-card',
  name: 'UGREEN Screensaver',
  description: "Screensaver settings and wallpaper, with a crop editor",
});
