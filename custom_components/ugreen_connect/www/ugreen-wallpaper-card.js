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
          gap: 10px; }
  .tile { position: relative; border: 2px solid transparent; border-radius: 8px;
          overflow: hidden; cursor: pointer; background: var(--secondary-background-color);
          aspect-ratio: ${RATIO}; padding: 0; }
  .tile img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .tile[aria-pressed="true"] { border-color: var(--primary-color); }
  .tile .cap { position: absolute; left: 0; right: 0; bottom: 0; font-size: .72em;
               background: rgba(0,0,0,.45); color: #fff; padding: 2px 5px;
               white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .tile.plain { display: flex; align-items: center; justify-content: center;
                color: var(--secondary-text-color); font-size: .85em; }
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
            <h2>${this._config.title || 'Screensaver'}</h2>
            <ha-switch class="power"></ha-switch>
          </div>
          <div class="settings">
            <div class="field fmt"><span>Time format</span>
              <span class="seg">
                <button data-fmt="12h" aria-pressed="false">12 h</button>
                <button data-fmt="24h" aria-pressed="false">24 h</button>
              </span>
            </div>
            <div class="field sty"><span>Clock style</span>
              <span class="seg">
                <button data-sty="style_1" aria-pressed="false">Style 1</button>
                <button data-sty="style_2" aria-pressed="false">Style 2</button>
              </span>
            </div>
            <div>
              <div class="field"><span>Wallpaper</span></div>
              <div class="grid"></div>
            </div>
            <div class="row">
              <label class="btn primary">Upload a picture<input type="file" accept="image/*" hidden></label>
            </div>
            <div class="editor" hidden>
              <div class="stage"><canvas></canvas></div>
              <div class="row" style="margin-top:10px">
                <button class="btn" data-act="rot">Rotate 90°</button>
                <button class="btn" data-act="fit">Reset</button>
                <button class="btn" data-act="cancel">Cancel</button>
                <button class="btn primary" data-act="apply">Use this</button>
              </div>
            </div>
            <div class="status"></div>
          </div>
        </div>
      </ha-card>
      <style>${css}</style>`;

    this._power = this.querySelector('.power');
    this._settings = this.querySelector('.settings');
    this._grid = this.querySelector('.grid');
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
      this._grid.innerHTML = '';
      this._grid.appendChild(this._tile({ id: 'none', label: 'None' }, current === 'none'));
      list.forEach((w) => this._grid.appendChild(this._tile(w, w.id === current)));
    }
  }

  _tile(w, active) {
    const el = document.createElement('button');
    el.className = 'tile' + (w.url ? '' : ' plain');
    el.setAttribute('aria-pressed', String(!!active));
    el.title = w.name || w.id;
    el.innerHTML = w.url
      ? `<img src="${w.url}" loading="lazy" alt=""><span class="cap">${w.stock ? 'Built-in' : 'Yours'}</span>`
      : (w.label || w.id);
    el.addEventListener('click', () => this._select(this._entities().wallpaper, w.id));
    return el;
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
        img.onerror = () => no(new Error('That file is not an image'));
        img.src = url;
      });
      this._image = img;
      this._angle = 0;
      this._editor.hidden = false;
      this._fit(true);
      this._status('Drag to move, scroll or pinch to zoom.');
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
      this._status('Set device_id in the card config', true);
      return;
    }
    this._busy = true;
    const apply = this.querySelector('[data-act=apply]');
    apply.disabled = true;
    this._status('Uploading…');

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
      this._status('Done — the charger is fetching it now.');
      this._close();
    } catch (err) {
      this._status(err?.message || 'Upload failed', true);
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
