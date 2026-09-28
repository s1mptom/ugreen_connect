/*
 * The charger's screen: what it shows, and the few settings anyone touches
 * often -- the screensaver, the brightness, how soon it goes dark -- with the
 * rest one tap away in an editor.
 *
 * The card itself is small: the strip as the charger will show it, and those
 * three settings. "Edit screen" opens the editor over the whole page: the
 * strip at the charger's own size, the clock's style and format, the pictures
 * the charger has and the owner's own, and an upload.
 *
 * The screen is 560x170, so a photo almost never suits it as taken. Uploading
 * one opens a fitting step: the screen's frame over the photo, dragged, zoomed
 * and turned -- to any angle, not in quarter turns -- until the right part is
 * inside it, with the result shown as the charger will show it.
 *
 * Config:
 *   type: custom:ugreen-wallpaper-card
 *   device_id: <the charger>        # optional if the entities are found
 *   title: Screen                   # optional
 */

const OUT_W = 560;
const OUT_H = 170;
const RATIO = OUT_W / OUT_H;
// How far past the smallest cover the zoom goes.
const ZOOM_RANGE = 8;

import {
  applyTheme, clampOffset, coverScale, findOne, frameSpan, mount, optionLabel, pending, resolveDevice,
} from './ugreen-ui.js';

/* Everything the card says, in one place.
 *
 * To add a language: copy the whole `en` block, key it by the language code
 * Home Assistant uses ("de", "fr", "ru", ...), and translate the values. Keys
 * that are missing fall back to English, so a partial translation is fine, and
 * `{n}` in a value is replaced by a number. */
const TEXT = {
  en: {
    title: 'Screen',
    editor: 'Charger screen',
    applies: 'Changes reach the charger as you make them. Each takes a few seconds.',
    edit: 'Edit screen',
    close: 'Close',
    done: 'Done',
    atSize: 'What the charger shows, at its own size',
    screensaver: 'Screensaver',
    screensaverAbout: 'The clock over the picture while nothing is changing',
    clock: 'Clock',
    timeFormat: 'Time format',
    hours12: '12 h',
    hours24: '24 h',
    clockStyle: 'Clock style',
    centred: 'Centred',
    onLeft: 'On the left',
    display: 'Display',
    brightness: 'Brightness',
    turnsOff: 'Turns off',
    turnsOffAfter: 'Turns off after',
    picture: 'Picture',
    fromCharger: 'From the charger',
    yours: 'Yours',
    none: 'None',
    stockPicture: 'Picture {n}',
    ownPicture: 'Your picture {n}',
    upload: 'Upload a photo',
    sending: 'Sending',
    sendingPicture: 'Sending the picture. The charger shows it once it has it.',
    fit: 'Fit your photo',
    back: 'Back to the screen settings',
    size: '{w} by {h}',
    frame: "The charger's screen",
    gestures: 'Drag to move. Scroll or pinch to zoom. Turn with two fingers.',
    zoom: 'Zoom',
    angle: 'Angle',
    reset: 'Reset',
    onCharger: 'On the charger',
    sentAs: 'Sent as a 560 by 170 picture, with the clock in the style you chose.',
    staysHere: 'Nothing leaves this browser until you press Use this photo.',
    cancel: 'Cancel',
    use: 'Use this photo',
    notAnImage: 'That file is not an image',
    needDevice: 'Set device_id in the card config',
    uploading: 'Uploading…',
    sent: 'Done. The charger is fetching it now.',
    failed: 'Upload failed',
  },
  de: {
    title: 'Bildschirm',
    editor: 'Bildschirm des Ladegeräts',
    applies: 'Änderungen gehen sofort an das Ladegerät. Jede dauert ein paar Sekunden.',
    edit: 'Bildschirm bearbeiten',
    close: 'Schließen',
    done: 'Fertig',
    atSize: 'Was das Ladegerät zeigt, in seiner Größe',
    screensaver: 'Bildschirmschoner',
    screensaverAbout: 'Die Uhr über dem Bild, solange sich nichts ändert',
    clock: 'Uhr',
    timeFormat: 'Zeitformat',
    hours12: '12 h',
    hours24: '24 h',
    clockStyle: 'Uhrenstil',
    centred: 'Mittig',
    onLeft: 'Links',
    display: 'Anzeige',
    brightness: 'Helligkeit',
    turnsOff: 'Aus nach',
    turnsOffAfter: 'Schaltet ab nach',
    picture: 'Bild',
    fromCharger: 'Vom Ladegerät',
    yours: 'Eigene',
    none: 'Keines',
    stockPicture: 'Bild {n}',
    ownPicture: 'Eigenes Bild {n}',
    upload: 'Foto hochladen',
    sending: 'Wird gesendet',
    sendingPicture: 'Das Bild wird gesendet. Das Ladegerät zeigt es, sobald es da ist.',
    fit: 'Foto einpassen',
    back: 'Zurück zu den Bildschirmeinstellungen',
    size: '{w} mal {h}',
    frame: 'Bildschirm des Ladegeräts',
    gestures: 'Ziehen zum Verschieben. Scrollen oder aufziehen zum Zoomen. Mit zwei Fingern drehen.',
    zoom: 'Zoom',
    angle: 'Winkel',
    reset: 'Zurücksetzen',
    onCharger: 'Auf dem Ladegerät',
    sentAs: 'Gesendet als Bild mit 560 mal 170, mit der Uhr im gewählten Stil.',
    staysHere: 'Nichts verlässt diesen Browser, bevor Sie Foto verwenden drücken.',
    cancel: 'Abbrechen',
    use: 'Foto verwenden',
    notAnImage: 'Diese Datei ist kein Bild',
    needDevice: 'device_id in der Kartenkonfiguration setzen',
    uploading: 'Wird hochgeladen…',
    sent: 'Fertig. Das Ladegerät lädt es gerade herunter.',
    failed: 'Upload fehlgeschlagen',
  },
  ru: {
    title: 'Экран',
    editor: 'Экран зарядки',
    applies: 'Изменения уходят на зарядку сразу. Каждое занимает несколько секунд.',
    edit: 'Настроить экран',
    close: 'Закрыть',
    done: 'Готово',
    atSize: 'Что покажет зарядка, в её размере',
    screensaver: 'Заставка',
    screensaverAbout: 'Часы поверх картинки, пока ничего не меняется',
    clock: 'Часы',
    timeFormat: 'Формат времени',
    hours12: '12 ч',
    hours24: '24 ч',
    clockStyle: 'Вид часов',
    centred: 'По центру',
    onLeft: 'Слева',
    display: 'Дисплей',
    brightness: 'Яркость',
    turnsOff: 'Гаснет',
    turnsOffAfter: 'Гаснет через',
    picture: 'Картинка',
    fromCharger: 'С зарядки',
    yours: 'Свои',
    none: 'Нет',
    stockPicture: 'Картинка {n}',
    ownPicture: 'Своя картинка {n}',
    upload: 'Загрузить фото',
    sending: 'Отправляется',
    sendingPicture: 'Картинка отправляется. Зарядка покажет её, как только получит.',
    fit: 'Подгоните фото',
    back: 'Назад к настройкам экрана',
    size: '{w} на {h}',
    frame: 'Экран зарядки',
    gestures: 'Тяните, чтобы сдвинуть. Колесо или щипок меняют масштаб. Двумя пальцами поворот.',
    zoom: 'Масштаб',
    angle: 'Угол',
    reset: 'Сбросить',
    onCharger: 'На зарядке',
    sentAs: 'Уйдёт картинкой 560 на 170, часы в выбранном виде.',
    staysHere: 'Ничего не покидает браузер, пока вы не нажмёте «Использовать».',
    cancel: 'Отмена',
    use: 'Использовать',
    notAnImage: 'Это не картинка',
    needDevice: 'Укажите device_id в настройках карточки',
    uploading: 'Загружается…',
    sent: 'Готово. Зарядка забирает картинку.',
    failed: 'Не удалось загрузить',
  },
};

const HERO = `<span class="face centre"><span class="blk"><b></b><i></i></span></span>`;
const CHEVRON = `<svg width="10" height="6" viewBox="0 0 10 6" aria-hidden="true"><path d="M1 1l4 4 4-4"
  fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"></path></svg>`;
const LENS_OUT = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
  stroke-linecap="round" aria-hidden="true" class="lens"><circle cx="11" cy="11" r="7"></circle><path d="M8 11h6M20 20l-4-4"></path></svg>`;
const LENS_IN = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
  stroke-linecap="round" aria-hidden="true" class="lens"><circle cx="11" cy="11" r="7"></circle><path d="M8 11h6M11 8v6M20 20l-4-4"></path></svg>`;
const SPINNER = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"
  stroke-linecap="round" aria-hidden="true"><path d="M12 3a9 9 0 1 0 9 9"></path></svg>`;

const css = `
  :host { display: block; container-type: inline-size; }
  [hidden] { display: none !important; }
  ha-card { height: 100%; box-sizing: border-box; }

  /* The card ------------------------------------------------------------ */
  .compact { padding: 14px 18px; display: flex; gap: 16px; height: 100%; box-sizing: border-box; }
  .compact .side { width: 176px; flex: none; display: flex; flex-direction: column; gap: 8px; }
  .compact .main { flex-grow: 1; min-width: 0; display: flex; flex-direction: column; gap: 10px;
                   font-size: 13px; }
  .headrow { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .title { margin: 0; font-size: 15px; font-weight: 500; }
  .line { display: flex; align-items: center; gap: 8px; }
  .line > span:first-child { color: var(--secondary-text-color); width: 70px; flex: none; }
  .line input[type="range"] { flex: 1 1 auto; min-width: 60px; max-width: 140px; margin: 0;
                              accent-color: var(--primary-color); }
  .line .val { width: 34px; text-align: right; flex: none; }
  .sw { display: flex; align-items: center; gap: 8px; color: var(--secondary-text-color); }

  /* The strip as the charger shows it. Pictures are held rotated a quarter
     turn anticlockwise, so the picture is turned back. The box takes the
     strip's sides swapped, which after the rotation lands exactly on it, read
     as container units so the strip's own border cannot throw them out. */
  .hero { position: relative; width: 100%; aspect-ratio: ${RATIO}; border-radius: 8px; overflow: hidden;
          background: #0b0b0b; container-type: size; box-shadow: 0 0 0 3px #0b0b0b; border: none;
          padding: 0; display: block; cursor: pointer; }
  .hero img, .tile img { position: absolute; top: 50%; left: 50%; width: 100cqh; height: 100cqw;
                         object-fit: cover; transform: translate(-50%, -50%) rotate(90deg); display: block; }
  .face { position: absolute; inset: 0; display: flex; align-items: center; color: #fff; line-height: 1.1;
          text-shadow: 0 1px 6px rgba(0, 0, 0, .65); }
  .face.centre { justify-content: center; }
  .face.left { justify-content: flex-start; padding-left: 7%; }
  .face .blk { display: flex; flex-direction: column; align-items: flex-start; }
  /* A third of the strip, as the charger's own clock is. */
  .face b { font-size: 33cqh; font-weight: 500; letter-spacing: .01em; }
  .face i { font-size: 9cqh; font-style: normal; opacity: .9; letter-spacing: .12em; margin-top: 4cqh; }

  /* Controls shared by the card and the editor ------------------------- */
  .button { font: inherit; font-size: 13px; font-weight: 500; height: 32px; padding: 0 14px;
            border-radius: 16px; border: 1px solid var(--divider-color); background: transparent;
            color: var(--primary-color); cursor: pointer; white-space: nowrap;
            display: inline-flex; align-items: center; justify-content: center; gap: 8px; }
  .button.primary { border-color: transparent; background: var(--primary-color);
                    color: var(--text-primary-color, #fff); }
  .button.quiet { color: var(--primary-text-color); }
  .button:disabled { opacity: .5; cursor: default; }
  .pick { position: relative; display: inline-flex; flex: none; color: var(--secondary-text-color); }
  .pick svg { position: absolute; right: 12px; top: 50%; margin-top: -3px; pointer-events: none; }
  .pick select { appearance: none; -webkit-appearance: none; margin: 0; height: 30px; box-sizing: border-box;
                 padding: 0 30px 0 12px; font: inherit; font-size: 13px; color: var(--primary-text-color);
                 background: var(--secondary-background-color); border: 1px solid transparent;
                 border-radius: 15px; cursor: pointer; max-width: 170px; }
  .pick select option { color: var(--primary-text-color); background: var(--card-background-color); }
  .seg { display: inline-flex; gap: 2px; padding: 3px; border-radius: 9px; background: var(--secondary-background-color); }
  .seg button { font: inherit; font-size: 13px; height: 28px; padding: 0 12px; border-radius: 7px;
                border: none; background: transparent; color: var(--primary-text-color); cursor: pointer; }
  .seg button[aria-checked="true"] { font-weight: 500; background: var(--card-background-color);
                                     box-shadow: 0 1px 2px rgba(0, 0, 0, .18); }
  :host([data-dark]) .seg button[aria-checked="true"] { box-shadow: 0 1px 2px rgba(0, 0, 0, .5);
    background: color-mix(in srgb, var(--primary-text-color) 12%, var(--secondary-background-color)); }
  button:focus-visible, select:focus-visible, input:focus-visible, .upload:focus-within {
    outline: 2px solid var(--primary-color); outline-offset: 1px; }

  /* The editor ---------------------------------------------------------- */
  dialog { padding: 0; border: 1px solid var(--divider-color); border-radius: 16px;
           width: min(960px, calc(100vw - 32px)); max-height: calc(100vh - 32px); box-sizing: border-box;
           background: var(--ha-card-background, var(--card-background-color)); color: var(--primary-text-color);
           box-shadow: 0 16px 48px rgba(0, 0, 0, .45); font-family: var(--paper-font-body1_-_font-family, inherit); }
  dialog::backdrop { background: rgba(0, 0, 0, .55); }
  dialog[open] { display: flex; flex-direction: column; }
  .dhead { display: flex; align-items: center; gap: 12px; padding: 16px 20px 14px;
           border-bottom: 1px solid var(--divider-color); flex: none; }
  .dhead h2 { margin: 0; font-size: 18px; font-weight: 500; white-space: nowrap; }
  .dhead .note { font-size: 13px; color: var(--secondary-text-color); overflow: hidden; text-overflow: ellipsis; }
  .icon { width: 36px; height: 36px; border-radius: 18px; border: none; background: transparent; flex: none;
          color: var(--secondary-text-color); display: inline-flex; align-items: center; justify-content: center;
          cursor: pointer; }
  .icon:hover { background: var(--secondary-background-color); }
  .grow { flex-grow: 1; }
  .dbody { overflow: auto; flex: 1 1 auto; }
  .dfoot { display: flex; align-items: center; justify-content: flex-end; gap: 10px; padding: 12px 20px;
           border-top: 1px solid var(--divider-color); flex: none; }
  .status { flex-grow: 1; font-size: 13px; color: var(--secondary-text-color); display: flex;
            align-items: center; gap: 8px; min-height: 20px; }
  .status.error { color: var(--error-color); }
  .status svg, .tile .busy svg { color: var(--primary-color); animation: spin 1s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  @media (prefers-reduced-motion: reduce) { .status svg, .tile .busy svg { animation: none; } }

  .big { padding: 18px 20px 0; display: flex; flex-direction: column; align-items: center; gap: 8px; }
  .big .hero { width: min(560px, 100%); border-radius: 12px; box-shadow: 0 0 0 6px #0b0b0b; cursor: default; }
  .big .cap { font-size: 12px; color: var(--secondary-text-color); }
  .cols { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 0 32px; padding: 20px; }
  .col { display: flex; flex-direction: column; gap: 22px; min-width: 0; }
  .block { display: flex; flex-direction: column; gap: 10px; }
  .block > .row { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
  .block h3 { margin: 0; font-size: 15px; font-weight: 500; }
  .about { font-size: 13px; color: var(--secondary-text-color); margin-top: 3px; }
  .styles { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .style { font: inherit; text-align: left; padding: 0; border-radius: 10px; overflow: hidden; cursor: pointer;
           border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); }
  .style[aria-checked="true"] { border: 2px solid var(--primary-color); }
  .style .hero { border-radius: 0; box-shadow: none; aspect-ratio: 2.6; cursor: inherit; }
  .style .lab { display: flex; justify-content: space-between; align-items: center; padding: 8px 10px;
                font-size: 13px; }
  .style .tick { color: var(--primary-color); visibility: hidden; }
  .style[aria-checked="true"] .tick { visibility: visible; }
  .field { display: flex; align-items: center; gap: 14px; font-size: 13px; }
  .field > span:first-child { width: 110px; flex: none; color: var(--secondary-text-color); }
  .field input[type="range"] { flex-grow: 1; margin: 0; accent-color: var(--primary-color); }
  .field .val { width: 38px; text-align: right; }
  .sub { font-size: 12px; color: var(--secondary-text-color); }
  .grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
  .tile { position: relative; padding: 0; border-radius: 9px; overflow: hidden; cursor: pointer;
          border: 1px solid var(--divider-color); background: #0b0b0b; aspect-ratio: ${RATIO};
          container-type: size; font: inherit; color: var(--secondary-text-color); }
  .tile[aria-checked="true"] { border: 2px solid var(--primary-color); }
  .tile.plain { display: flex; align-items: center; justify-content: center; font-size: 12px; }
  .tile .busy { position: absolute; inset: 0; background: rgba(0, 0, 0, .55); color: #fff; font-size: 12px;
                display: flex; align-items: center; justify-content: center; gap: 8px; }
  .upload { position: relative; }
  .upload input { position: absolute; inset: 0; opacity: 0; cursor: pointer; }

  /* Fitting a photo ------------------------------------------------------ */
  .fitbody { padding: 18px 20px; display: flex; flex-direction: column; gap: 14px; }
  .stage { position: relative; width: 100%; aspect-ratio: 2.17; border-radius: 12px; overflow: hidden;
           background: #202020; touch-action: none; cursor: grab; }
  .stage.dragging { cursor: grabbing; }
  .stage canvas { width: 100%; height: 100%; display: block; }
  .stage .gestures { position: absolute; left: 50%; bottom: 12px; transform: translateX(-50%); font-size: 12px;
                     color: #e0e0e0; background: rgba(0, 0, 0, .5); padding: 5px 10px; border-radius: 12px;
                     white-space: nowrap; pointer-events: none; }
  .fitctl { display: flex; align-items: center; gap: 28px; flex-wrap: wrap; }
  .fitctl label { display: flex; align-items: center; gap: 10px; font-size: 13px; flex: 1 1 220px; }
  .fitctl label > span:first-child { color: var(--secondary-text-color); width: 52px; flex: none; }
  .fitctl input[type="range"] { flex-grow: 1; margin: 0; accent-color: var(--primary-color); }
  .fitctl .deg { width: 48px; text-align: right; }
  .fitctl .lens { color: var(--secondary-text-color); flex: none; }
  .fitctl .level { position: relative; flex-grow: 1; display: flex; align-items: center; }
  .fitctl .level input { width: 100%; }
  .fitctl .level::before { content: ''; position: absolute; left: 50%; top: -8px; width: 1px; height: 6px;
                           background: var(--secondary-text-color); pointer-events: none; }
  .button.plain { border-color: transparent; }
  .result { display: flex; align-items: center; gap: 20px; padding: 14px 16px; border-radius: 12px;
            background: var(--secondary-background-color); }
  .result canvas { width: 280px; height: 85px; flex: none; border-radius: 8px; box-shadow: 0 0 0 4px #0b0b0b; }
  .result div { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--secondary-text-color); }
  .result b { font-size: 14px; font-weight: 500; color: var(--primary-text-color); }

  /* Last in the sheet, so it wins over the rules it narrows. Too narrow for the strip beside its settings: the strip on top, the
     settings under it, each the card's full width. */
  @container (max-width: 440px) {
    .compact { flex-direction: column; }
    /* The heading and the screensaver switch first, then the strip, then the
       rest -- the order they are read in, which is not the order they sit in
       side by side. */
    .compact .main { display: contents; }
    .compact .headrow { order: 0; }
    .compact .side { order: 1; width: 100%; }
    .compact .line { order: 2; }
    .line input[type="range"] { max-width: none; }
  }
  @media (max-width: 700px) {
    dialog { width: 100vw; max-width: 100vw; height: 100vh; max-height: 100vh; border-radius: 0; border: none; }
    .cols { grid-template-columns: minmax(0, 1fr); gap: 22px; }
    .result { flex-direction: column; align-items: flex-start; }
    .result canvas { width: 100%; height: auto; aspect-ratio: ${RATIO}; }
    .dhead .note { display: none; }
  }
`;

class UgreenWallpaperCard extends HTMLElement {
  static getStubConfig() { return { device_id: '' }; }

  setConfig(config) {
    this._config = config || {};
    this._image = null;
    this._built = false;
    // What has been asked for and not yet confirmed; see `pending`.
    this._asked = pending();
    if (this.shadowRoot) this.shadowRoot.innerHTML = '';
  }

  set hass(hass) {
    this._hass = hass;
    applyTheme(this, hass);
    this._build();
    this._sync();
  }

  getCardSize() { return 3; }

  /* One string, in the viewer's language where there is one. */
  _t(key, vars) {
    const lang = (this._hass?.locale?.language || this._hass?.language || 'en')
      .toLowerCase().split('-')[0];
    const text = (TEXT[lang] || {})[key] ?? TEXT.en[key] ?? key;
    return vars ? text.replace(/\{(\w+)\}/g, (_, name) => vars[name]) : text;
  }

  /* Entities ------------------------------------------------------------ */

  _find(domain, suffix) {
    return findOne(this._hass, this._config.device_id, domain, suffix);
  }

  _entities() {
    return {
      screensaver: this._find('switch', '_screensaver'),
      format: this._find('select', '_time_format'),
      style: this._find('select', '_clock_style'),
      wallpaper: this._find('select', '_wallpaper'),
      brightness: this._find('number', '_screen_brightness'),
      screenOff: this._find('select', '_screen_off_time'),
    };
  }

  _state(id) { return id ? this._hass.states[id] : undefined; }

  /* Rendering ----------------------------------------------------------- */

  _build() {
    if (this._built) return;
    this._built = true;
    const t = (k, v) => this._t(k, v);
    // A root of its own. In one document this card's class names are ordinary
    // words -- `.row`, `.grid`, `.field` -- and a layout card holding it had
    // its own rows turned into flex rows by them. Nothing outside reaches in
    // here now, and nothing in here reaches out.
    this._root = mount(this, `
      <style>${css}</style>
      <ha-card>
        <div class="compact">
          <div class="side">
            <button type="button" class="hero open" aria-label="${t('edit')}">${HERO}</button>
            <button type="button" class="button open">${t('edit')}</button>
          </div>
          <div class="main">
            <div class="headrow">
              <h2 class="title">${this._config.title || t('title')}</h2>
              <label class="sw">${t('screensaver')}<ha-switch class="power" aria-label="${t('screensaver')}"></ha-switch></label>
            </div>
            <label class="line"><span>${t('brightness')}</span><input type="range" class="bright" min="0" max="100" step="1"><span class="val"></span></label>
            <label class="line"><span>${t('turnsOff')}</span><span class="pick"><select class="off" aria-label="${t('turnsOffAfter')}"></select>${CHEVRON}</span></label>
          </div>
        </div>
      </ha-card>

      <dialog aria-labelledby="ug-editor-title">
        <div class="view settings">
          <div class="dhead">
            <h2 id="ug-editor-title">${t('editor')}</h2>
            <span class="note">${t('applies')}</span>
            <span class="grow"></span>
            <button type="button" class="icon close" aria-label="${t('close')}"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"></path></svg></button>
          </div>
          <div class="dbody">
            <div class="big"><div class="hero">${HERO}</div><span class="cap">${t('atSize')}</span></div>
            <div class="cols">
              <div class="col">
                <div class="block"><div class="row">
                  <div><h3>${t('screensaver')}</h3><div class="about">${t('screensaverAbout')}</div></div>
                  <ha-switch class="power" aria-label="${t('screensaver')}"></ha-switch>
                </div></div>
                <div class="block">
                  <div class="row"><h3>${t('clock')}</h3>
                    <div class="seg" role="radiogroup" aria-label="${t('timeFormat')}">
                      <button type="button" role="radio" data-fmt="12h">${t('hours12')}</button>
                      <button type="button" role="radio" data-fmt="24h">${t('hours24')}</button>
                    </div>
                  </div>
                  <div class="styles" role="radiogroup" aria-label="${t('clockStyle')}">
                    <button type="button" role="radio" class="style" data-sty="style_1">
                      <span class="hero"><span class="face centre"><span class="blk"><b></b><i></i></span></span></span>
                      <span class="lab">${t('centred')}<span class="tick">✓</span></span>
                    </button>
                    <button type="button" role="radio" class="style" data-sty="style_2">
                      <span class="hero"><span class="face left"><span class="blk"><b></b><i></i></span></span></span>
                      <span class="lab">${t('onLeft')}<span class="tick">✓</span></span>
                    </button>
                  </div>
                </div>
                <div class="block"><h3>${t('display')}</h3>
                  <label class="field"><span>${t('brightness')}</span><input type="range" class="bright" min="0" max="100" step="1"><span class="val"></span></label>
                  <label class="field"><span>${t('turnsOffAfter')}</span><span class="pick"><select class="off"></select>${CHEVRON}</span></label>
                </div>
              </div>
              <div class="col">
                <div class="block">
                  <div class="row"><h3>${t('picture')}</h3>
                    <label class="button primary upload"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 16V4M6 10l6-6 6 6M4 20h16"></path></svg>${t('upload')}<input type="file" accept="image/*" aria-label="${t('upload')}"></label>
                  </div>
                  <span class="sub">${t('fromCharger')}</span>
                  <div class="grid stock" role="radiogroup" aria-label="${t('fromCharger')}"></div>
                  <span class="sub own-head" hidden>${t('yours')}</span>
                  <div class="grid mine" role="radiogroup" aria-label="${t('yours')}" hidden></div>
                </div>
              </div>
            </div>
          </div>
          <div class="dfoot"><span class="status"></span><button type="button" class="button quiet done">${t('done')}</button></div>
        </div>

        <div class="view fitting" hidden>
          <div class="dhead">
            <button type="button" class="icon back" aria-label="${t('back')}"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 6l-6 6 6 6"></path></svg></button>
            <h2>${t('fit')}</h2>
            <span class="note file"></span>
          </div>
          <div class="dbody"><div class="fitbody">
            <div class="stage" role="img" aria-label="${t('gestures')}"><canvas class="canvas"></canvas><span class="gestures">${t('gestures')}</span></div>
            <div class="fitctl">
              <label><span>${t('zoom')}</span>${LENS_OUT}<input type="range" class="zoom" min="0" max="1000" step="1" value="0" aria-label="${t('zoom')}">${LENS_IN}</label>
              <label><span>${t('angle')}</span><span class="level"><input type="range" class="angle" min="-180" max="180" step="0.5" value="0" aria-label="${t('angle')}"></span><span class="deg">0.0°</span></label>
              <button type="button" class="button quiet reset">${t('reset')}</button>
            </div>
            <div class="result"><canvas class="out" width="${OUT_W}" height="${OUT_H}"></canvas>
              <div><b>${t('onCharger')}</b><span>${t('sentAs')}</span><span>${t('staysHere')}</span></div>
            </div>
          </div></div>
          <div class="dfoot"><span class="status fitstatus"></span>
            <button type="button" class="button quiet plain cancel">${t('cancel')}</button>
            <button type="button" class="button primary use">${t('use')}</button>
          </div>
        </div>
      </dialog>`);

    const $ = (sel) => this._root.querySelector(sel);
    const $$ = (sel) => [...this._root.querySelectorAll(sel)];
    this._dialog = $('dialog');
    this._views = { settings: $('.view.settings'), fitting: $('.view.fitting') };
    this._grid = $('.grid.stock');
    this._mine = $('.grid.mine');
    this._ownHead = $('.own-head');
    this._statusEl = $('.status');
    this._fitStatus = $('.fitstatus');
    this._stage = $('.stage');
    this._canvas = $('.canvas');
    this._out = $('.out');
    this._zoomEl = $('.zoom');
    this._angleEl = $('.angle');
    this._degEl = $('.deg');

    for (const el of $$('.open')) el.addEventListener('click', () => this._openEditor());
    $('.close').addEventListener('click', () => this._dialog.close());
    $('.done').addEventListener('click', () => this._dialog.close());
    // A click on the backdrop lands on the dialog itself, outside its content.
    this._dialog.addEventListener('click', (e) => {
      if (e.target === this._dialog) this._dialog.close();
    });
    this._dialog.addEventListener('close', () => this._closeFit());

    for (const sw of $$('ha-switch.power')) {
      sw.addEventListener('change', () => {
        const id = this._entities().screensaver;
        if (!id) return;
        const wanted = sw.checked;
        this._asked.set('screensaver', wanted ? 'on' : 'off');
        this._sync();
        this._hass.callService('switch', wanted ? 'turn_on' : 'turn_off', { entity_id: id });
      });
    }
    for (const input of $$('input.bright')) {
      input.addEventListener('input', () => {
        for (const v of $$('.val')) v.textContent = `${input.value}%`;
      });
      input.addEventListener('change', () => {
        const id = this._entities().brightness;
        if (!id) return;
        this._asked.set('brightness', String(Number(input.value)));
        this._hass.callService('number', 'set_value', { entity_id: id, value: Number(input.value) });
      });
    }
    for (const select of $$('select.off')) {
      select.addEventListener('change', () => {
        const id = this._entities().screenOff;
        if (!id) return;
        this._asked.set('screenOff', select.value);
        this._sync();
        this._hass.callService('select', 'select_option', { entity_id: id, option: select.value });
      });
    }
    for (const b of $$('[data-fmt]')) b.addEventListener('click', () => this._select(this._entities().format, b.dataset.fmt, 'format'));
    for (const b of $$('[data-sty]')) b.addEventListener('click', () => this._select(this._entities().style, b.dataset.sty, 'style'));

    $('.upload input').addEventListener('change', (e) => {
      const file = e.target.files && e.target.files[0];
      e.target.value = '';
      if (file) this._open(file);
    });
    $('.back').addEventListener('click', () => this._closeFit());
    $('.cancel').addEventListener('click', () => this._closeFit());
    $('.use').addEventListener('click', () => this._upload());
    $('.reset').addEventListener('click', () => { this._angle = 0; this._fit(true); });
    this._zoomEl.addEventListener('input', () => {
      if (!this._image) return;
      this._scale = this._min * ZOOM_RANGE ** (Number(this._zoomEl.value) / 1000);
      this._clamp();
      this._draw();
    });
    this._angleEl.addEventListener('input', () => {
      if (!this._image) return;
      this._turnTo((Number(this._angleEl.value) * Math.PI) / 180);
    });
    this._gestures();
    if (this._resize) window.removeEventListener('resize', this._resize);
    this._resize = () => { if (this._image) this._fit(false); };
    window.addEventListener('resize', this._resize);
  }

  _openEditor() {
    this._views.settings.hidden = false;
    this._views.fitting.hidden = true;
    if (!this._dialog.open) this._dialog.showModal();
    this._sync();
  }

  _sync() {
    if (!this._built || !this._hass) return;
    const ent = this._entities();
    const on = this._asked.read('screensaver', this._state(ent.screensaver)?.state) === 'on';
    for (const sw of this._root.querySelectorAll('ha-switch.power')) sw.checked = on;

    const bright = this._state(ent.brightness);
    for (const input of this._root.querySelectorAll('input.bright')) {
      input.disabled = !bright;
      if (bright && this._root.activeElement !== input) {
        input.value = this._asked.read('brightness', bright.state);
      }
    }
    for (const v of this._root.querySelectorAll('.val')) {
      v.textContent = bright ? `${Math.round(Number(this._asked.read('brightness', bright.state)))}%` : '—';
    }

    const off = this._state(ent.screenOff);
    for (const select of this._root.querySelectorAll('select.off')) {
      const options = off?.attributes?.options || [];
      const same = select.options.length === options.length
        && [...select.options].every((o, i) => o.value === options[i]);
      if (!same) {
        select.replaceChildren(...options.map((option) => {
          const el = document.createElement('option');
          el.value = option;
          el.textContent = optionLabel(this._hass, ent.screenOff, option);
          return el;
        }));
      }
      select.disabled = !off;
      if (off) select.value = this._asked.read('screenOff', off.state);
    }

    const fmt = this._asked.read('format', this._state(ent.format)?.state);
    for (const b of this._root.querySelectorAll('[data-fmt]')) b.setAttribute('aria-checked', String(b.dataset.fmt === fmt));
    const sty = this._asked.read('style', this._state(ent.style)?.state);
    for (const b of this._root.querySelectorAll('[data-sty]')) b.setAttribute('aria-checked', String(b.dataset.sty === sty));

    const wall = this._state(ent.wallpaper);
    const list = wall?.attributes?.wallpapers || [];
    const current = this._asked.read('wallpaper', wall?.state);
    // Asked for and not yet on the charger: chosen at once, because it is what
    // was asked for, and marked while it travels -- a write takes seconds.
    const sending = wall && current !== wall.state ? current : null;
    const signature = JSON.stringify([list.map((w) => w.id), current, sending]);
    if (signature !== this._gridSig) {
      this._gridSig = signature;
      // Stock pictures and the owner's own are kept apart, as the app keeps them.
      const stock = list.filter((w) => w.stock);
      const mine = list.filter((w) => !w.stock);
      this._grid.replaceChildren(
        this._tile({ id: 'none' }, this._t('none'), current, sending),
        ...stock.map((w, i) => this._tile(w, this._t('stockPicture', { n: i + 1 }), current, sending)),
      );
      this._mine.replaceChildren(...mine.map((w, i) => this._tile(w, this._t('ownPicture', { n: i + 1 }), current, sending)));
      this._mine.hidden = mine.length === 0;
      this._ownHead.hidden = mine.length === 0;
    }
    // A message the viewer was just given -- a failure, a picture sent -- stays
    // long enough to be read; a poll five seconds later must not wipe it.
    const note = this._note && Date.now() < this._note.until ? this._note : null;
    if (!this._uploading) {
      if (note) this._status(note.text, note.bad);
      else this._status(sending ? this._t('sendingPicture') : '', false, Boolean(sending));
    }
    this._drawHero(list, current, sty, fmt);
  }

  /* The time and the date as the charger writes them. */
  _clock(fmt) {
    const now = new Date();
    const h = now.getHours();
    const hh = fmt === '12h' ? (h % 12 || 12) : h;
    return {
      time: `${fmt === '12h' ? hh : String(hh).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`
        + (fmt === '12h' ? (h < 12 ? ' AM' : ' PM') : ''),
      date: now.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: '2-digit' })
        .replace(/(\w+) (\w+) (\d+)/, '$1, $2 $3').toUpperCase(),
    };
  }

  /* Every strip on the card and in the editor: the chosen picture with the
     clock over it, in the chosen style. The app leads with the same thing. */
  _drawHero(list, current, sty, fmt) {
    const pic = list.find((w) => w.id === current && (w.preview || w.url));
    const clock = this._clock(fmt);
    for (const hero of this._root.querySelectorAll('.hero')) {
      const inStyle = hero.closest('.style');
      if (!inStyle) {
        const img = hero.querySelector('img');
        if (pic && img?.dataset.pic !== pic.id) {
          const target = img || hero.insertAdjacentElement('afterbegin', new Image());
          target.alt = '';
          target.dataset.pic = pic.id;
          this._show(target, pic);
        } else if (!pic && img) {
          img.remove();
        }
        hero.querySelector('.face').className = `face ${sty === 'style_2' ? 'left' : 'centre'}`;
      }
      hero.querySelector('.face b').textContent = clock.time;
      hero.querySelector('.face i').textContent = clock.date;
    }
    if (!this._tick) this._tick = setInterval(() => this._sync(), 20000);
  }

  disconnectedCallback() {
    if (this._tick) { clearInterval(this._tick); this._tick = null; }
    if (this._resize) window.removeEventListener('resize', this._resize);
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

  _tile(w, label, current, sending) {
    const el = document.createElement('button');
    el.type = 'button';
    const pic = w.preview || w.url;
    el.className = pic ? 'tile' : 'tile plain';
    el.setAttribute('role', 'radio');
    el.setAttribute('aria-checked', String(w.id === current));
    el.setAttribute('aria-label', label);
    if (pic) {
      const img = new Image();
      img.alt = '';
      el.appendChild(img);
      this._show(img, w);
    } else {
      el.textContent = label;
    }
    if (w.id === sending) {
      const busy = document.createElement('span');
      busy.className = 'busy';
      busy.innerHTML = `${SPINNER}<span></span>`;
      busy.lastElementChild.textContent = this._t('sending');
      el.appendChild(busy);
    }
    el.addEventListener('click', () => this._select(this._entities().wallpaper, w.id, 'wallpaper'));
    return el;
  }

  _select(entityId, option, key) {
    if (!entityId) return;
    if (key) this._asked.set(key, option);
    this._sync();
    this._hass.callService('select', 'select_option', { entity_id: entityId, option });
  }

  /* Say something and keep it on screen for a while. */
  _say(text, bad) {
    this._note = { text, bad: Boolean(bad), until: Date.now() + 8000 };
    this._status(text, bad);
  }

  _status(text, bad, busy) {
    const el = this._views?.fitting && !this._views.fitting.hidden ? this._fitStatus : this._statusEl;
    for (const target of [this._statusEl, this._fitStatus]) {
      if (target !== el) { target.replaceChildren(); target.classList.remove('error'); }
    }
    el.replaceChildren();
    if (busy) el.insertAdjacentHTML('afterbegin', SPINNER);
    if (text) el.append(document.createTextNode(text));
    el.classList.toggle('error', Boolean(bad));
  }

  /* Fitting a photo ------------------------------------------------------ */

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
      this._root.querySelector('.file').textContent = `${file.name}, ${this._t('size', { w: img.width, h: img.height })}`;
      this._views.settings.hidden = true;
      this._views.fitting.hidden = false;
      // Laid out first, measured second: the stage has no size until it shows.
      requestAnimationFrame(() => this._fit(true));
      this._status('');
    } catch (err) {
      this._say(err.message, true);
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  _closeFit() {
    this._image = null;
    if (this._views) {
      this._views.fitting.hidden = true;
      this._views.settings.hidden = false;
    }
    this._sync();
  }

  /* The screen's frame, centred in the stage, in canvas pixels. */
  _frame() {
    const w = this._canvas.width;
    const h = this._canvas.height;
    const fw = Math.min(w * 0.72, h * 0.5 * RATIO);
    return { x: (w - fw) / 2, y: (h - fw / RATIO) / 2, w: fw, h: fw / RATIO };
  }

  _span(angle) { return frameSpan(this._frame(), angle); }

  _cover(angle) { return coverScale(this._image, this._frame(), angle); }

  _fit(centre) {
    if (!this._image) return;
    const rect = this._stage.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const ratio = this._scale && this._min ? this._scale / this._min : 1;
    this._canvas.width = Math.max(1, Math.round(rect.width * dpr));
    this._canvas.height = Math.max(1, Math.round(rect.height * dpr));
    this._min = this._cover(this._angle);
    this._scale = centre ? this._min : this._min * ratio;
    if (centre || !this._offset) this._offset = { x: 0, y: 0 };
    this._clamp();
    this._draw();
  }

  /* Turn the photo, keeping how far it was zoomed in past its cover. */
  _turnTo(angle) {
    const ratio = this._scale / this._min;
    this._angle = angle;
    this._min = this._cover(angle);
    this._scale = this._min * ratio;
    this._clamp();
    this._draw();
  }

  /* Keep the zoom within its range and the frame inside the photo. */
  _clamp() {
    this._scale = Math.min(this._min * ZOOM_RANGE, Math.max(this._min, this._scale));
    this._offset = clampOffset(this._image, this._frame(), this._angle, this._scale, this._offset);
  }

  /* The photo, placed as it is now, into a context whose frame is `frame`. */
  _paint(ctx, frame, k) {
    ctx.save();
    ctx.translate(frame.x + frame.w / 2 + this._offset.x * k, frame.y + frame.h / 2 + this._offset.y * k);
    ctx.rotate(this._angle);
    ctx.scale(this._scale * k, this._scale * k);
    ctx.drawImage(this._image, -this._image.width / 2, -this._image.height / 2);
    ctx.restore();
  }

  /* The clock over a frame, in the chosen style and format. */
  _paintClock(ctx, frame, alpha) {
    const ent = this._entities();
    const fmt = this._asked.read('format', this._state(ent.format)?.state);
    const sty = this._asked.read('style', this._state(ent.style)?.state);
    const clock = this._clock(fmt);
    const family = getComputedStyle(this).fontFamily || 'sans-serif';
    const big = frame.h * 0.3;
    const small = frame.h * 0.09;
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.fillStyle = '#fff';
    ctx.shadowColor = 'rgba(0, 0, 0, .6)';
    ctx.shadowBlur = frame.h * 0.04;
    ctx.textBaseline = 'alphabetic';
    ctx.font = `500 ${big}px ${family}`;
    const timeW = ctx.measureText(clock.time).width;
    ctx.font = `400 ${small}px ${family}`;
    const dateW = ctx.measureText(clock.date).width * 1.12;
    const blockW = Math.max(timeW, dateW);
    const left = sty === 'style_2' ? frame.x + frame.w * 0.07 : frame.x + (frame.w - blockW) / 2;
    const top = frame.y + (frame.h - (big + small * 1.5)) / 2;
    ctx.font = `500 ${big}px ${family}`;
    ctx.fillText(clock.time, left, top + big * 0.85);
    ctx.font = `400 ${small}px ${family}`;
    if ('letterSpacing' in ctx) ctx.letterSpacing = `${small * 0.12}px`;
    ctx.fillText(clock.date, left, top + big + small * 1.1);
    ctx.restore();
  }

  _draw() {
    if (!this._canvas || !this._image) return;
    const ctx = this._canvas.getContext('2d');
    const { width: w, height: h } = this._canvas;
    ctx.clearRect(0, 0, w, h);
    const frame = this._frame();
    this._paint(ctx, frame, 1);
    ctx.fillStyle = 'rgba(0,0,0,.58)';
    ctx.fillRect(0, 0, w, frame.y);
    ctx.fillRect(0, frame.y + frame.h, w, h - frame.y - frame.h);
    ctx.fillRect(0, frame.y, frame.x, frame.h);
    ctx.fillRect(frame.x + frame.w, frame.y, w - frame.x - frame.w, frame.h);
    this._paintClock(ctx, frame, 0.55);
    const dpr = window.devicePixelRatio || 1;
    ctx.strokeStyle = 'rgba(255,255,255,.95)';
    ctx.lineWidth = 2 * dpr;
    ctx.strokeRect(frame.x, frame.y, frame.w, frame.h);
    ctx.fillStyle = 'rgba(255,255,255,.9)';
    ctx.font = `${12 * dpr}px ${getComputedStyle(this).fontFamily || 'sans-serif'}`;
    ctx.fillText(this._t('frame'), frame.x, frame.y - 8 * dpr);

    // What the charger will be sent, drawn the way it will be sent.
    const out = this._out.getContext('2d');
    out.fillStyle = '#000';
    out.fillRect(0, 0, OUT_W, OUT_H);
    this._paint(out, { x: 0, y: 0, w: OUT_W, h: OUT_H }, OUT_W / frame.w);
    this._paintClock(out, { x: 0, y: 0, w: OUT_W, h: OUT_H }, 1);

    this._zoomEl.value = String(Math.round((Math.log(this._scale / this._min) / Math.log(ZOOM_RANGE)) * 1000));
    let deg = ((this._angle * 180) / Math.PI) % 360;
    if (deg > 180) deg -= 360;
    if (deg < -180) deg += 360;
    this._angleEl.value = String(deg);
    this._degEl.textContent = `${deg.toFixed(1).replace('-', '−')}°`;
  }

  _gestures() {
    const stage = this._stage;
    const dpr = () => window.devicePixelRatio || 1;
    const points = new Map();
    let last = null;
    let pinch = null;

    stage.addEventListener('pointerdown', (e) => {
      if (!this._image) return;
      points.set(e.pointerId, e);
      stage.setPointerCapture(e.pointerId);
      last = { x: e.clientX, y: e.clientY };
      pinch = null;
      stage.classList.add('dragging');
    });
    stage.addEventListener('pointermove', (e) => {
      if (!this._image || !points.has(e.pointerId)) return;
      points.set(e.pointerId, e);
      if (points.size === 2) {
        // Two fingers: their distance is the zoom and the line between them
        // is the angle, both taken as a change from the last move.
        const [a, b] = [...points.values()];
        const dist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
        const turn = Math.atan2(b.clientY - a.clientY, b.clientX - a.clientX);
        if (pinch) {
          this._scale *= dist / pinch.dist;
          this._turnTo(this._angle + (turn - pinch.turn));
        }
        pinch = { dist, turn };
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
      pinch = null;
      last = null;
      stage.classList.remove('dragging');
    };
    stage.addEventListener('pointerup', up);
    stage.addEventListener('pointercancel', up);
    stage.addEventListener('wheel', (e) => {
      if (!this._image) return;
      e.preventDefault();
      this._scale *= Math.exp(-e.deltaY / 400);
      this._clamp();
      this._draw();
    }, { passive: false });
  }

  async _upload() {
    if (!this._image || this._busy) return;
    // The charger this card shows, which is the one the picture is for.
    const deviceId = resolveDevice(this._hass, this._config.device_id);
    if (!deviceId) {
      this._say(this._t('needDevice'), true);
      return;
    }
    this._busy = true;
    this._uploading = true;
    const use = this._root.querySelector('.use');
    use.disabled = true;
    this._status(this._t('uploading'), false, true);

    // The picture itself, without the clock: the charger draws its own.
    const out = document.createElement('canvas');
    out.width = OUT_W;
    out.height = OUT_H;
    const ctx = out.getContext('2d');
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, OUT_W, OUT_H);
    this._paint(ctx, { x: 0, y: 0, w: OUT_W, h: OUT_H }, OUT_W / this._frame().w);

    try {
      await this._hass.callService('ugreen_connect', 'set_wallpaper', {
        device_id: deviceId,
        image: out.toDataURL('image/jpeg', 0.9),
      });
      this._closeFit();
      this._say(this._t('sent'));
    } catch (err) {
      this._say(err?.message || this._t('failed'), true);
    } finally {
      this._busy = false;
      this._uploading = false;
      use.disabled = false;
    }
  }
}

// Defining an element twice throws, and this module can be loaded twice: a
// resource list left holding an older url for this same file is one way, a
// dashboard adding it by hand beside the integration's own is another. The
// first copy in wins and the rest do nothing, which is what a second <script>
// for one card should do.
if (!customElements.get('ugreen-wallpaper-card')) {
  customElements.define('ugreen-wallpaper-card', UgreenWallpaperCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: 'ugreen-wallpaper-card',
    name: 'UGREEN Screen',
    description: "The charger's screen: screensaver, brightness and screen-off, with an editor for the clock and the picture",
  });
}
