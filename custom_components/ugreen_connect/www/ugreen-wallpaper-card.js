/*
 * Wallpaper picker for UGREEN chargers.
 *
 * The screen is 560x170 -- unusually wide -- so a picture almost never fits it
 * as taken. The card shows the crop window over the image and lets you drag,
 * zoom and rotate until the right part is inside it, then hands the cropped
 * 560x170 JPEG to the ugreen_connect.set_wallpaper service.
 *
 * Config:
 *   type: custom:ugreen-wallpaper-card
 *   device_id: <the charger, from Developer tools -> Devices>
 *   title: Wallpaper            # optional
 */

const OUT_W = 560;
const OUT_H = 170;
const FRAME_RATIO = OUT_W / OUT_H;

class UgreenWallpaperCard extends HTMLElement {
  static getConfigElement() { return null; }
  static getStubConfig() { return { device_id: '' }; }

  setConfig(config) {
    if (!config.device_id) throw new Error('device_id is required');
    this._config = config;
    this._reset();
    this._render();
  }

  set hass(hass) { this._hass = hass; }
  getCardSize() { return 6; }

  _reset() {
    this._image = null;
    this._scale = 1;
    this._minScale = 1;
    this._angle = 0;          // radians
    this._offset = { x: 0, y: 0 };
    this._drag = null;
    this._pinch = null;
    this._busy = false;
  }

  _render() {
    if (this._built) return;
    this._built = true;
    this.innerHTML = `
      <ha-card>
        <div class="wrap">
          <div class="stage"><canvas></canvas><div class="hint">Pick a picture to begin</div></div>
          <div class="row">
            <label class="btn primary">Pick picture<input type="file" accept="image/*" hidden></label>
            <button class="btn" data-act="rot">Rotate 90°</button>
            <button class="btn" data-act="reset">Reset</button>
            <button class="btn primary" data-act="apply" disabled>Set wallpaper</button>
          </div>
          <div class="status"></div>
        </div>
      </ha-card>
      <style>
        .wrap { padding: 12px; }
        .stage { position: relative; width: 100%; aspect-ratio: ${FRAME_RATIO * 1.6};
                 background: var(--secondary-background-color); border-radius: 8px;
                 overflow: hidden; touch-action: none; cursor: grab; }
        .stage.dragging { cursor: grabbing; }
        canvas { width: 100%; height: 100%; display: block; }
        .hint { position: absolute; inset: 0; display: flex; align-items: center;
                justify-content: center; color: var(--secondary-text-color);
                pointer-events: none; font-size: .9em; }
        .row { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }
        .btn { border: 1px solid var(--divider-color); background: var(--card-background-color);
               color: var(--primary-text-color); border-radius: 6px; padding: 8px 12px;
               cursor: pointer; font: inherit; }
        .btn.primary { background: var(--primary-color); color: var(--text-primary-color);
                       border-color: transparent; }
        .btn[disabled] { opacity: .5; cursor: default; }
        .status { margin-top: 8px; min-height: 1.2em; color: var(--secondary-text-color);
                  font-size: .9em; }
        .status.error { color: var(--error-color); }
      </style>`;

    this._canvas = this.querySelector('canvas');
    this._stage = this.querySelector('.stage');
    this._statusEl = this.querySelector('.status');
    this._hint = this.querySelector('.hint');

    this.querySelector('input[type=file]').addEventListener('change', (e) => {
      const file = e.target.files && e.target.files[0];
      if (file) this._load(file);
    });
    this.querySelector('[data-act=rot]').addEventListener('click', () => {
      this._angle += Math.PI / 2;
      this._fit(true);
    });
    this.querySelector('[data-act=reset]').addEventListener('click', () => {
      this._angle = 0;
      this._fit(true);
    });
    this.querySelector('[data-act=apply]').addEventListener('click', () => this._apply());

    this._bindGestures();
    window.addEventListener('resize', () => this._draw());
  }

  _status(text, isError) {
    this._statusEl.textContent = text || '';
    this._statusEl.classList.toggle('error', !!isError);
  }

  async _load(file) {
    this._status('Loading…');
    const url = URL.createObjectURL(file);
    try {
      const img = new Image();
      await new Promise((ok, fail) => {
        img.onload = ok;
        img.onerror = () => fail(new Error('That file is not an image'));
        img.src = url;
      });
      this._image = img;
      this._angle = 0;
      this._fit(true);
      this._hint.style.display = 'none';
      this.querySelector('[data-act=apply]').disabled = false;
      this._status('Drag to move, scroll or pinch to zoom.');
    } catch (err) {
      this._status(err.message, true);
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  /* Geometry ------------------------------------------------------------ */

  _frame() {
    // The crop window, centred in the stage with the screen's aspect ratio.
    const w = this._canvas.width;
    const h = this._canvas.height;
    const fw = Math.min(w * 0.86, h * 0.86 * FRAME_RATIO);
    return { x: (w - fw) / 2, y: (h - fw / FRAME_RATIO) / 2, w: fw, h: fw / FRAME_RATIO };
  }

  _rotatedSize() {
    const swap = Math.abs(Math.sin(this._angle)) > 0.5;
    return swap
      ? { w: this._image.height, h: this._image.width }
      : { w: this._image.width, h: this._image.height };
  }

  _fit(centre) {
    if (!this._image) return;
    this._sizeCanvas();
    const frame = this._frame();
    const size = this._rotatedSize();
    // Never let the picture be smaller than the window, or the wallpaper would
    // have empty edges.
    this._minScale = Math.max(frame.w / size.w, frame.h / size.h);
    this._scale = this._minScale;
    if (centre) this._offset = { x: 0, y: 0 };
    this._clamp();
    this._draw();
  }

  _clamp() {
    const frame = this._frame();
    const size = this._rotatedSize();
    const limitX = Math.max(0, (size.w * this._scale - frame.w) / 2);
    const limitY = Math.max(0, (size.h * this._scale - frame.h) / 2);
    this._offset.x = Math.min(limitX, Math.max(-limitX, this._offset.x));
    this._offset.y = Math.min(limitY, Math.max(-limitY, this._offset.y));
  }

  _sizeCanvas() {
    const rect = this._stage.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    this._canvas.width = Math.round(rect.width * dpr);
    this._canvas.height = Math.round(rect.height * dpr);
  }

  _draw() {
    if (!this._canvas) return;
    const ctx = this._canvas.getContext('2d');
    const w = this._canvas.width;
    const h = this._canvas.height;
    ctx.clearRect(0, 0, w, h);
    if (!this._image) return;

    const frame = this._frame();
    ctx.save();
    ctx.translate(frame.x + frame.w / 2 + this._offset.x, frame.y + frame.h / 2 + this._offset.y);
    ctx.rotate(this._angle);
    ctx.scale(this._scale, this._scale);
    ctx.drawImage(this._image, -this._image.width / 2, -this._image.height / 2);
    ctx.restore();

    // Dim everything the charger will not show, and outline what it will.
    ctx.fillStyle = 'rgba(0,0,0,.55)';
    ctx.fillRect(0, 0, w, frame.y);
    ctx.fillRect(0, frame.y + frame.h, w, h - frame.y - frame.h);
    ctx.fillRect(0, frame.y, frame.x, frame.h);
    ctx.fillRect(frame.x + frame.w, frame.y, w - frame.x - frame.w, frame.h);
    ctx.strokeStyle = 'rgba(255,255,255,.9)';
    ctx.lineWidth = Math.max(1, (window.devicePixelRatio || 1));
    ctx.strokeRect(frame.x, frame.y, frame.w, frame.h);
  }

  /* Gestures ------------------------------------------------------------ */

  _bindGestures() {
    const stage = this._stage;
    const dpr = () => window.devicePixelRatio || 1;

    stage.addEventListener('pointerdown', (e) => {
      if (!this._image) return;
      stage.setPointerCapture(e.pointerId);
      this._drag = { x: e.clientX, y: e.clientY };
      stage.classList.add('dragging');
    });
    stage.addEventListener('pointermove', (e) => {
      if (!this._drag || !this._image) return;
      this._offset.x += (e.clientX - this._drag.x) * dpr();
      this._offset.y += (e.clientY - this._drag.y) * dpr();
      this._drag = { x: e.clientX, y: e.clientY };
      this._clamp();
      this._draw();
    });
    const end = (e) => {
      this._drag = null;
      stage.classList.remove('dragging');
      if (e.pointerId !== undefined && stage.hasPointerCapture?.(e.pointerId)) {
        stage.releasePointerCapture(e.pointerId);
      }
    };
    stage.addEventListener('pointerup', end);
    stage.addEventListener('pointercancel', end);

    stage.addEventListener('wheel', (e) => {
      if (!this._image) return;
      e.preventDefault();
      this._zoom(Math.exp(-e.deltaY / 400));
    }, { passive: false });

    // Pinch: track two pointers and zoom by how their distance changes.
    const points = new Map();
    stage.addEventListener('pointerdown', (e) => points.set(e.pointerId, e));
    stage.addEventListener('pointermove', (e) => {
      if (!points.has(e.pointerId)) return;
      points.set(e.pointerId, e);
      if (points.size !== 2) return;
      const [a, b] = [...points.values()];
      const dist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
      if (this._pinch) this._zoom(dist / this._pinch);
      this._pinch = dist;
      this._drag = null;
    });
    const drop = (e) => { points.delete(e.pointerId); this._pinch = null; };
    stage.addEventListener('pointerup', drop);
    stage.addEventListener('pointercancel', drop);
  }

  _zoom(factor) {
    this._scale = Math.min(this._minScale * 8, Math.max(this._minScale, this._scale * factor));
    this._clamp();
    this._draw();
  }

  /* Output -------------------------------------------------------------- */

  _crop() {
    const out = document.createElement('canvas');
    out.width = OUT_W;
    out.height = OUT_H;
    const ctx = out.getContext('2d');
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, OUT_W, OUT_H);

    // Same transform as the preview, rescaled from the on-screen window to the
    // real 560x170 output.
    const frame = this._frame();
    const k = OUT_W / frame.w;
    ctx.translate(OUT_W / 2 + this._offset.x * k, OUT_H / 2 + this._offset.y * k);
    ctx.rotate(this._angle);
    ctx.scale(this._scale * k, this._scale * k);
    ctx.drawImage(this._image, -this._image.width / 2, -this._image.height / 2);
    return out.toDataURL('image/jpeg', 0.9);
  }

  async _apply() {
    if (!this._image || this._busy) return;
    this._busy = true;
    const apply = this.querySelector('[data-act=apply]');
    apply.disabled = true;
    this._status('Uploading…');
    try {
      await this._hass.callService('ugreen_connect', 'set_wallpaper', {
        device_id: this._config.device_id,
        image: this._crop(),
      });
      this._status('Done — the charger is fetching it now.');
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
  name: 'UGREEN Wallpaper',
  description: "Crop a picture to the charger's screen and send it",
});
