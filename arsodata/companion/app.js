'use strict';

// ============================================================================
// Air Ring companion - Web Bluetooth client
//
// Secure context required (HTTPS or localhost). Android Chrome. The ring must
// be in setup mode: hold the button while powering on, or hold it for ~2 s
// while a forecast is showing.
// ============================================================================

const SERVICE_UUID = '5f9b1000-7a1e-4c3b-9b1d-1e2f3a4b5c6d';
const RX_UUID      = '5f9b1001-7a1e-4c3b-9b1d-1e2f3a4b5c6d'; // central writes here
const TX_UUID      = '5f9b1002-7a1e-4c3b-9b1d-1e2f3a4b5c6d'; // ring notifies here

const CHUNK_SIZE = 180;
const PALETTE = ['#00C83C', '#FFC800', '#FF5A00', '#D21400', '#820000'];

const $ = (id) => document.getElementById(id);
const encoder = new TextEncoder();
const decoder = new TextDecoder();

const state = {
  device: null,
  connected: false,
  writeChar: null,
  notifyChar: null,
  notifyHandler: null,
  pending: null,
  reqSeq: 0,
  rxParts: [],
  rxBytes: 0,
  info: null,
  limits: null,
  publicCfg: null,
  devAuthed: false,
  schema: null,
  devCfg: null,
  previewWarned: false,
};

// Requests are serialised: one in flight at a time. The device processes them
// in order and sends exactly one response per request.
let requestChain = Promise.resolve();
let manualDisconnect = false;

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------
function clamp(v, lo, hi) {
  return Math.min(hi, Math.max(lo, v));
}

function parseRgba(value) {
  const s = String(value || '').trim();
  const m = /^#?([0-9a-f]{6})([0-9a-f]{2})?$/i.exec(s);
  if (!m) return { r: 0, g: 0, b: 0, a: 255 };
  return {
    r: parseInt(m[1].slice(0, 2), 16),
    g: parseInt(m[1].slice(2, 4), 16),
    b: parseInt(m[1].slice(4, 6), 16),
    a: m[2] ? parseInt(m[2], 16) : 255,
  };
}

function byteHex(n) {
  return clamp(Math.round(Number(n) || 0), 0, 255).toString(16).padStart(2, '0');
}

function composeRgba(c) {
  return `#${byteHex(c.r)}${byteHex(c.g)}${byteHex(c.b)}${byteHex(c.a)}`.toLowerCase();
}

function normalizeColor(v) {
  return composeRgba(parseRgba(v));
}

function setStatus(el, message, kind = '') {
  if (!el) return;
  el.textContent = message;
  el.dataset.kind = kind;
}

function setBusy(button, busy, labelWhenBusy, labelWhenIdle) {
  if (!button) return;
  if (busy) {
    button.dataset.idleLabel = button.dataset.idleLabel || button.textContent;
    button.disabled = true;
    if (labelWhenBusy) button.textContent = labelWhenBusy;
  } else {
    button.disabled = false;
    button.textContent = labelWhenIdle || button.dataset.idleLabel || button.textContent;
  }
}

function log(kind, payload) {
  const pre = $('log');
  if (!pre) return;
  let text;
  if (typeof payload === 'string') {
    text = payload;
  } else {
    try { text = JSON.stringify(payload); } catch { text = String(payload); }
  }
  if (text.length > 700) text = `${text.slice(0, 700)} … (${text.length} chars)`;
  const stamp = new Date().toLocaleTimeString([], { hour12: false });
  pre.textContent += `[${stamp}] ${kind}  ${text}\n`;
  const lines = pre.textContent.split('\n');
  if (lines.length > 420) pre.textContent = lines.slice(-320).join('\n');
  pre.scrollTop = pre.scrollHeight;
}

// Keep secrets out of the visible protocol log.
function redactForLog(message) {
  if (!message || typeof message !== 'object') return message;
  const copy = { ...message };
  if (copy.op === 'auth' && 'pin' in copy) copy.pin = '••••••';
  if (copy.values && typeof copy.values === 'object') {
    copy.values = { ...copy.values };
    for (const key of Object.keys(copy.values)) {
      if (/pass|pin|secret|token|key/i.test(key)) copy.values[key] = '••••••';
    }
  }
  return copy;
}

// ---------------------------------------------------------------------------
// Environment check
// ---------------------------------------------------------------------------
function environmentProblem() {
  if (!('bluetooth' in navigator)) {
    return 'This browser does not expose Web Bluetooth. Use Chrome on Android (desktop Chrome also works on some machines).';
  }
  if (!window.isSecureContext) {
    return 'Web Bluetooth needs a secure page: HTTPS, or http://localhost. For local testing with adb, run "adb reverse tcp:8080 tcp:8080" and open http://localhost:8080 on the phone.';
  }
  return '';
}

// ---------------------------------------------------------------------------
// BLE transport: chunked JSON frames
// ---------------------------------------------------------------------------
async function writeFrame(frame) {
  if (!state.writeChar) throw new Error('Bluetooth characteristic is missing');
  const write = state.writeChar.writeValueWithResponse
    ? state.writeChar.writeValueWithResponse(frame)
    : state.writeChar.writeValue(frame);

  await Promise.race([
    write,
    new Promise((_, reject) => setTimeout(() => reject(new Error('Bluetooth write timed out')), 8000)),
  ]);
}

async function sendJson(message) {
  const bytes = encoder.encode(JSON.stringify(message));
  for (let offset = 0; offset < bytes.length; offset += CHUNK_SIZE) {
    const n = Math.min(CHUNK_SIZE, bytes.length - offset);
    const frame = new Uint8Array(n + 1);
    frame[0] = (offset === 0 ? 0x01 : 0x00) | (offset + n === bytes.length ? 0x02 : 0x00);
    frame.set(bytes.subarray(offset, offset + n), 1);
    await writeFrame(frame);
  }
}

function onNotify(event) {
  const dv = event.target.value;
  if (!dv || dv.byteLength < 1) return;

  const bytes = new Uint8Array(dv.buffer, dv.byteOffset, dv.byteLength);
  const flags = bytes[0];

  if (flags & 0x01) {
    state.rxParts = [];
    state.rxBytes = 0;
  }
  const part = bytes.slice(1);
  state.rxParts.push(part);
  state.rxBytes += part.length;

  if (state.rxBytes > 65536) {
    state.rxParts = [];
    state.rxBytes = 0;
    log('rx?', 'response too large, dropped');
    return;
  }
  if (!(flags & 0x02)) return;

  const all = new Uint8Array(state.rxBytes);
  let offset = 0;
  for (const p of state.rxParts) {
    all.set(p, offset);
    offset += p.length;
  }
  state.rxParts = [];
  state.rxBytes = 0;

  const text = decoder.decode(all);
  let message;
  try {
    message = JSON.parse(text);
  } catch {
    log('rx?', text);
    return;
  }

  log('rx', message);
  const pending = state.pending;
  if (pending && (message.id === undefined || message.id === pending.id)) {
    state.pending = null;
    clearTimeout(pending.timer);
    pending.resolve(message);
  }
}

function requestNow(op, extra = {}, timeoutMs = 15000) {
  if (!state.connected || !state.writeChar) {
    return Promise.reject(new Error('Ring is not connected'));
  }

  const id = ++state.reqSeq;
  const message = { id, op, ...extra };
  log('tx', redactForLog(message));

  return new Promise((resolve, reject) => {
    state.pending = {
      id,
      resolve,
      reject,
      timer: setTimeout(() => {
        if (state.pending && state.pending.id === id) {
          state.pending = null;
          reject(new Error(`Timed out waiting for "${op}"`));
        }
      }, timeoutMs),
    };

    sendJson(message).catch((err) => {
      const pending = state.pending;
      if (pending && pending.id === id) {
        state.pending = null;
        clearTimeout(pending.timer);
        pending.reject(err);
      }
    });
  });
}

function request(op, extra = {}, timeoutMs = 15000) {
  const run = () => requestNow(op, extra, timeoutMs);
  const result = requestChain.then(run, run);
  requestChain = result.then(() => undefined, () => undefined);
  return result;
}

// ---------------------------------------------------------------------------
// Connection lifecycle
// ---------------------------------------------------------------------------
async function attach() {
  if (!state.device) throw new Error('No Bluetooth device selected');

  $('connText').textContent = 'Connecting…';
  $('connectBtn').disabled = true;
  $('connectBtn').textContent = 'Connecting…';

  const server = await state.device.gatt.connect();
  const service = await server.getPrimaryService(SERVICE_UUID);
  state.writeChar = await service.getCharacteristic(RX_UUID);
  state.notifyChar = await service.getCharacteristic(TX_UUID);

  if (state.notifyHandler) {
    state.notifyChar.removeEventListener('characteristicvaluechanged', state.notifyHandler);
  }
  state.notifyHandler = (event) => onNotify(event);
  state.notifyChar.addEventListener('characteristicvaluechanged', state.notifyHandler);
  await state.notifyChar.startNotifications();

  state.connected = true;
  state.reqSeq = 0;
  state.rxParts = [];
  state.rxBytes = 0;
  state.pending = null;

  setConnectionUI();
  showTab('public');
  $('disconnectBtn').hidden = false;

  const info = await request('hello', {}, 8000);
  if (!info.ok) throw new Error(info.error || 'Ring did not introduce itself');

  state.info = info;
  state.limits = info.limits || state.limits;
  $('deviceName').textContent = info.device || state.device.name || 'Air ring';
  $('deviceFw').textContent = info.fw ? `firmware ${info.fw}` : '';
  $('connectHint').textContent = '';

  await loadPublic();
  log('info', 'ready');
}

async function connect() {
  if (state.connected) return;

  const problem = environmentProblem();
  if (problem) {
    $('secureHint').hidden = false;
    $('secureHint').textContent = problem;
    return;
  }

  $('connectHint').textContent = '';
  $('secureHint').hidden = true;
  $('connectBtn').disabled = true;
  $('connectBtn').textContent = 'Opening picker…';

  try {
    if (!state.device) {
      state.device = await navigator.bluetooth.requestDevice({
        filters: [{ services: [SERVICE_UUID] }],
        optionalServices: [SERVICE_UUID],
      });
      state.device.addEventListener('gattserverdisconnected', onGattDisconnected);
    }
    await attach();
  } catch (err) {
    console.error(err);
    state.connected = false;
    state.writeChar = null;
    state.notifyChar = null;
    state.device = null;

    if (err.name === 'NotFoundError') {
      $('connectHint').textContent = 'No device selected. Make sure the ring is in setup mode, then try again.';
    } else if (err.name === 'SecurityError') {
      $('connectHint').textContent = 'Bluetooth was blocked. Web Bluetooth requires HTTPS or http://localhost.';
    } else if (/no longer in range|not found|failed to connect/i.test(err.message || '')) {
      $('connectHint').textContent = 'Could not reach the ring. Put it back in setup mode (hold the button while powering on) and try again.';
    } else {
      $('connectHint').textContent = `Connection failed: ${err.message || err}`;
    }
    setConnectionUI();
    $('connectBtn').disabled = false;
    $('connectBtn').textContent = 'Connect to ring';
  }
}

function abortPending(reason) {
  const pending = state.pending;
  if (!pending) return;
  state.pending = null;
  clearTimeout(pending.timer);
  pending.reject(new Error(reason));
}

function onGattDisconnected() {
  const wasConnected = state.connected;
  state.connected = false;
  state.writeChar = null;
  state.notifyChar = null;
  abortPending('Ring disconnected');
  setConnectionUI();

  if (wasConnected) {
    if (!manualDisconnect) {
      $('connectHint').textContent = 'The ring disconnected. It may have left setup mode. Hold the button while powering on (or during a forecast) to re-enter it.';
    }
  }
}

function disconnect() {
  manualDisconnect = true;
  try {
    if (state.device && state.device.gatt && state.device.gatt.connected) {
      state.device.gatt.disconnect();
    }
  } catch { /* ignore */ }
  // gattserverdisconnected normally fires; force the UI if it does not.
  setTimeout(() => {
    if (!state.device || !state.device.gatt || !state.device.gatt.connected) {
      onGattDisconnected();
    }
    manualDisconnect = false;
  }, 350);
}

function setConnectionUI() {
  const blocked = Boolean(environmentProblem());
  $('connDot').classList.toggle('on', state.connected);
  $('connText').textContent = state.connected ? 'Connected' : 'Not connected';
  $('tabs').hidden = !state.connected;
  $('logCard').hidden = !state.connected;
  $('disconnectBtn').hidden = !state.connected;
  $('connectBtn').disabled = state.connected || blocked;
  $('connectBtn').textContent = state.connected ? 'Connected' : 'Connect to ring';

  if (!state.connected) {
    $('publicTab').hidden = true;
    $('devTab').hidden = true;
    lockDev();
    state.limits = null;
    state.publicCfg = null;
    state.info = null;
  }
}

function showTab(name) {
  if (name !== 'dev' && $('devTab') && !$('devTab').hidden) {
    clearColorPreview();
  }
  document.querySelectorAll('.tab').forEach((button) => {
    button.classList.toggle('active', button.dataset.tab === name);
  });
  $('publicTab').hidden = !state.connected || name !== 'public';
  $('devTab').hidden = !state.connected || name !== 'dev';
}

// ---------------------------------------------------------------------------
// Public settings
// ---------------------------------------------------------------------------
async function loadPublic() {
  const response = await request('getPublic', {}, 12000);
  if (!response.ok) throw new Error(response.error || 'Could not read settings');
  state.limits = response.limits || state.limits;
  state.publicCfg = response.config;
  renderPublic();
}

function renderPublic() {
  const cfg = state.publicCfg || {};
  const limits = state.limits || {};

  const anonMin = Math.round(Number(limits.anonymizeMin ?? 0));
  const anonMax = Math.round(Number(limits.anonymizeMax ?? 5000));
  const brightnessMax = Math.round(Number(limits.brightnessMax ?? 255));

  for (const el of [$('anonRange'), $('anonNum')]) {
    el.min = String(anonMin);
    el.max = String(anonMax);
  }

  const anon = clamp(Math.round(Number(cfg.anonymizeMeters ?? anonMin)), anonMin, anonMax);
  $('anonRange').value = String(anon);
  $('anonNum').value = String(anon);
  $('anonOut').textContent = `${anon} m`;

  $('brightRange').min = '0';
  $('brightRange').max = String(brightnessMax);
  const brightness = clamp(Math.round(Number(cfg.ledBrightness ?? 0)), 0, brightnessMax);
  $('brightRange').value = String(brightness);
  $('brightOut').textContent = String(brightness);

  const lockedAnon = anonMin === anonMax;
  $('anonRange').disabled = lockedAnon;
  $('anonNum').disabled = lockedAnon;
  $('brightRange').disabled = brightnessMax === 0;

  $('brightHelp').textContent = brightnessMax < 255
    ? `Your developer has capped brightness at ${brightnessMax}.`
    : 'The ring flickers a little at full brightness on cheap strips.';

  updatePreview();
  setStatus($('publicStatus'), '', '');
}

function updatePreview() {
  const container = $('brightPreview');
  if (!container) return;

  if (!container.children.length) {
    for (let i = 0; i < 10; i++) {
      const dot = document.createElement('span');
      dot.style.background = PALETTE[i % PALETTE.length];
      dot.style.color = PALETTE[i % PALETTE.length];
      container.appendChild(dot);
    }
  }

  const brightness = Number($('brightRange').value || 0);
  const scale = 0.06 + 0.94 * (brightness / 255);
  container.style.filter = `brightness(${scale.toFixed(3)})`;
  container.style.opacity = brightness === 0 ? '0.28' : '1';
}

async function applyPublic() {
  if (!state.connected) return;

  const anonMin = Number($('anonNum').min || 0);
  const anonMax = Number($('anonNum').max || 5000);
  const brightnessMax = Number($('brightRange').max || 255);

  const anon = clamp(Math.round(Number($('anonNum').value || 0)), anonMin, anonMax);
  const brightness = clamp(Math.round(Number($('brightRange').value || 0)), 0, brightnessMax);

  if (state.publicCfg &&
      anon === Math.round(Number(state.publicCfg.anonymizeMeters)) &&
      brightness === Math.round(Number(state.publicCfg.ledBrightness))) {
    setStatus($('publicStatus'), 'No changes to apply.', '');
    return;
  }

  setBusy($('applyPublicBtn'), true, 'Applying…');
  setStatus($('publicStatus'), '', '');

  try {
    const response = await request('setPublic', {
      values: { anonymizeMeters: anon, ledBrightness: brightness },
    }, 12000);

    if (response.config) state.publicCfg = response.config;
    if (response.limits) state.limits = response.limits;
    renderPublic();

    if (!response.ok) {
      setStatus($('publicStatus'), response.errors || response.error || 'The ring rejected some values.', 'err');
    } else if (response.saved === false) {
      setStatus($('publicStatus'), 'Applied, but the ring could not save to flash.', 'err');
    } else {
      const appliedAnon = Math.round(Number(response.config?.anonymizeMeters ?? anon));
      const appliedBright = Math.round(Number(response.config?.ledBrightness ?? brightness));
      setStatus(
        $('publicStatus'),
        `Saved. Privacy grid ${appliedAnon} m · brightness ${appliedBright}.`,
        'ok',
      );
      updatePreview();
    }
  } catch (err) {
    setStatus($('publicStatus'), err.message || String(err), 'err');
  } finally {
    setBusy($('applyPublicBtn'), false, null, 'Apply to ring');
  }
}

// ---------------------------------------------------------------------------
// Developer mode
// ---------------------------------------------------------------------------
function lockDev(message = '') {
  state.devAuthed = false;
  state.schema = null;
  state.devCfg = null;
  state.previewWarned = false;
  const lock = $('devLock');
  const panel = $('devPanel');
  const form = $('devForm');
  if (lock) lock.hidden = false;
  if (panel) panel.hidden = true;
  if (form) form.textContent = '';
  if (message) setStatus($('authStatus'), message, '');
  else setStatus($('authStatus'), '', '');
}

async function unlock() {
  const pin = $('pinInput').value.trim();
  if (!pin) {
    setStatus($('authStatus'), 'Enter the developer PIN.', 'err');
    return;
  }

  setBusy($('unlockBtn'), true, 'Checking…');
  setStatus($('authStatus'), '', '');

  try {
    const response = await request('auth', { pin }, 10000);
    if (response.ok) {
      state.devAuthed = true;
      state.previewWarned = false;
      $('pinInput').value = '';
      $('devLock').hidden = true;
      $('devPanel').hidden = false;
      setStatus($('authStatus'), '', '');
      await loadDev(true);
    } else if (response.error === 'bad_pin') {
      const left = Number(response.attemptsLeft);
      setStatus(
        $('authStatus'),
        Number.isFinite(left) ? `Wrong PIN. ${left} attempt${left === 1 ? '' : 's'} left.` : 'Wrong PIN.',
        'err',
      );
    } else {
      setStatus($('authStatus'), response.error || 'Unlock failed.', 'err');
    }
  } catch (err) {
    setStatus($('authStatus'), err.message || String(err), 'err');
  } finally {
    setBusy($('unlockBtn'), false, null, 'Unlock');
  }
}

async function loadDev(reloadSchema = false) {
  try {
    if (!state.schema || reloadSchema) {
      const schemaResponse = await request('getSchema', {}, 30000);
      if (!schemaResponse.ok) throw new Error(schemaResponse.error || 'Could not load schema');
      state.schema = schemaResponse.schema;
    }

    const response = await request('getDev', {}, 30000);
    if (!response.ok) throw new Error(response.error || 'Could not load settings');

    state.devCfg = response.config;
    state.limits = response.limits || state.limits;
    renderDev();
    setStatus($('devStatus'), `Loaded ${state.schema.fields.length} settings.`, 'ok');
  } catch (err) {
    handleDevError(err);
  }
}

function handleDevError(err) {
  const message = String(err && err.message ? err.message : err);
  if (message.includes('auth_required')) {
    lockDev('Developer session expired. Unlock again.');
  } else if (message.includes('low_memory')) {
    setStatus($('devStatus'), 'The ring is low on memory. Disconnect other devices and try again.', 'err');
  } else {
    setStatus($('devStatus'), message, 'err');
  }
}

function badge(text, extraClass = '') {
  const span = document.createElement('span');
  span.className = `badge ${extraClass}`.trim();
  span.textContent = text;
  return span;
}

let colorPreviewTimer = null;

// Explicit R/G/B/A controls. A scales the colour's intensity on the LEDs; it is
// not true transparency. Every change also streams a live preview to the ring.
function buildRgbaEditor(key, value) {
  const rgba = parseRgba(value);
  const editor = document.createElement('div');
  editor.className = 'rgbaEditor';
  editor.dataset.type = 'color';

  const swatch = document.createElement('span');
  swatch.className = 'swatch';
  swatch.setAttribute('aria-hidden', 'true');
  editor.appendChild(swatch);

  const channels = {};
  for (const channel of ['r', 'g', 'b', 'a']) {
    const wrap = document.createElement('label');
    wrap.className = 'chan';
    const cap = document.createElement('span');
    cap.textContent = channel.toUpperCase();
    const num = document.createElement('input');
    num.type = 'number';
    num.min = '0';
    num.max = '255';
    num.step = '1';
    num.inputMode = 'numeric';
    num.value = String(rgba[channel]);
    num.dataset.channel = channel;
    num.setAttribute('aria-label', `${channel.toUpperCase()} channel for ${key}`);
    num.addEventListener('input', () => {
      update();
      scheduleColorPreview();
    });
    wrap.appendChild(cap);
    wrap.appendChild(num);
    channels[channel] = num;
    editor.appendChild(wrap);
  }

  const hex = document.createElement('span');
  hex.className = 'hex';
  editor.appendChild(hex);

  function read() {
    return {
      r: clamp(Math.round(Number(channels.r.value) || 0), 0, 255),
      g: clamp(Math.round(Number(channels.g.value) || 0), 0, 255),
      b: clamp(Math.round(Number(channels.b.value) || 0), 0, 255),
      a: clamp(Math.round(Number(channels.a.value) || 0), 0, 255),
    };
  }

  function update() {
    const c = read();
    const alpha = c.a / 255;
    swatch.style.background =
      `rgb(${Math.round(c.r * alpha)}, ${Math.round(c.g * alpha)}, ${Math.round(c.b * alpha)})`;
    hex.textContent = composeRgba(c);
  }

  editor._readRgba = read;
  update();
  return editor;
}

function colorEditorsInOrder() {
  if (!state.schema) return [];
  return state.schema.fields
    .filter((f) => f.type === 'color')
    .map((f) => document.getElementById(`f_${f.key}`))
    .filter((el) => el && typeof el._readRgba === 'function');
}

function supportsColorPreview() {
  return Array.isArray(state.info?.ops) && state.info.ops.includes('previewColors');
}

function scheduleColorPreview() {
  if (!state.connected || !state.devAuthed) return;
  if (!supportsColorPreview()) {
    if (!state.previewWarned) {
      state.previewWarned = true;
      setStatus(
        $('devStatus'),
        'Live preview needs the latest ring firmware (it did not advertise previewColors). Reflash and reconnect.',
        'err',
      );
    }
    return;
  }
  clearTimeout(colorPreviewTimer);
  colorPreviewTimer = setTimeout(sendColorPreview, 120);
}

async function sendColorPreview() {
  if (!state.connected || !state.devAuthed) return;
  if (!supportsColorPreview()) {
    scheduleColorPreview();   // shows the one-time warning
    return;
  }
  const editors = colorEditorsInOrder().slice(0, 5);
  if (!editors.length) return;
  const colors = editors.map((el) => composeRgba(el._readRgba()));
  try {
    const response = await request('previewColors', { colors }, 8000);
    if (!response.ok) {
      setStatus($('devStatus'), `Colour preview failed: ${response.error || 'unknown error'}`, 'err');
    }
  } catch (err) {
    setStatus($('devStatus'), `Colour preview failed: ${err.message || err}`, 'err');
  }
}

function clearColorPreview() {
  clearTimeout(colorPreviewTimer);
  if (state.connected && state.devAuthed) {
    request('clearPreview', {}, 5000).catch(() => {});
  }
}

function makeField(field) {
  const row = document.createElement('div');
  row.className = 'field';

  const label = document.createElement('label');
  label.className = 'fieldLabel';
  label.htmlFor = `f_${field.key}`;
  label.textContent = field.label;

  const meta = document.createElement('div');
  meta.className = 'fieldMeta';
  meta.appendChild(document.createTextNode(field.key));
  if (field.public) meta.appendChild(badge('public'));
  if (field.secret) meta.appendChild(badge('secret', 'secret'));
  if (field.reboot) meta.appendChild(badge('reboot', 'reboot'));
  label.appendChild(meta);

  const current = state.devCfg ? state.devCfg[field.key] : undefined;
  let input;

  switch (field.type) {
    case 'bool':
      input = document.createElement('input');
      input.type = 'checkbox';
      input.checked = Boolean(current);
      break;

    case 'uint':
    case 'float':
    case 'double':
      input = document.createElement('input');
      input.type = 'number';
      input.inputMode = 'decimal';
      if (field.min !== undefined) input.min = String(field.min);
      if (field.max !== undefined) input.max = String(field.max);
      if (field.step !== undefined) input.step = String(field.step);
      input.value = current === undefined ? '' : String(current);
      break;

    case 'color':
      input = buildRgbaEditor(field.key, current);
      break;

    case 'string':
      if (field.maxLength && field.maxLength > 200) {
        input = document.createElement('textarea');
        input.rows = 5;
      } else {
        input = document.createElement('input');
        input.type = field.secret ? 'password' : 'text';
      }
      input.value = field.secret ? '' : (current === undefined ? '' : String(current));
      if (field.secret) input.placeholder = 'unchanged (leave blank)';
      if (field.maxLength) input.maxLength = field.maxLength;
      break;

    default:
      input = document.createElement('input');
      input.type = 'text';
      input.value = current === undefined ? '' : String(current);
  }

  input.id = `f_${field.key}`;
  input.dataset.key = field.key;
  input.dataset.type = field.type;
  if (field.secret) input.dataset.secret = '1';

  const help = document.createElement('p');
  help.className = 'help';
  help.textContent = field.help || '';
  if (field.type === 'string' && field.maxLength) {
    help.textContent += ` Max ${field.maxLength} characters.`;
  }
  if (field.type === 'color') {
    help.textContent += ' R/G/B/A are 0–255; A scales the colour intensity.';
  }
  if (field.secret) {
    help.textContent += ' Stored on the ring but never sent back; leave blank to keep the current value.';
  }

  if (field.type === 'bool') {
    row.classList.add('boolField');
    const line = document.createElement('div');
    line.className = 'boolLine';
    line.appendChild(input);
    line.appendChild(label);
    row.appendChild(line);
    row.appendChild(help);
    return row;
  }

  if (field.type === 'color') row.classList.add('colorField');

  const control = document.createElement('div');
  control.className = 'fieldControl';
  control.appendChild(input);

  row.appendChild(label);
  row.appendChild(control);
  row.appendChild(help);
  return row;
}

function renderDev() {
  const form = $('devForm');
  form.textContent = '';
  if (!state.schema || !Array.isArray(state.schema.fields)) return;

  const groups = new Map();
  for (const field of state.schema.fields) {
    if (!groups.has(field.group)) groups.set(field.group, []);
    groups.get(field.group).push(field);
  }

  for (const [groupName, fields] of groups) {
    const group = document.createElement('fieldset');
    group.className = 'card';
    const legend = document.createElement('legend');
    legend.textContent = groupName;
    group.appendChild(legend);

    if (fields.some((f) => f.type === 'color')) {
      const row = document.createElement('div');
      row.className = 'previewRow';

      const note = document.createElement('p');
      note.className = 'help';
      note.textContent = supportsColorPreview()
        ? 'Edit colours with the R/G/B/A fields. Changes preview live on the first five LEDs; A scales intensity.'
        : 'Live preview needs the latest ring firmware (previewColors was not advertised). Reflash and reconnect.';

      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'ghost smallBtn';
      button.textContent = 'Preview colours';
      button.disabled = !supportsColorPreview();
      button.addEventListener('click', () => sendColorPreview());

      row.appendChild(note);
      row.appendChild(button);
      group.appendChild(row);
    }

    for (const field of fields) group.appendChild(makeField(field));
    form.appendChild(group);
  }

  // A form rebuild invalidates any live colour preview that was in progress.
  clearColorPreview();
}

function collectDevChanges() {
  const values = {};

  for (const field of state.schema.fields) {
    const input = document.getElementById(`f_${field.key}`);
    if (!input) continue;
    const current = state.devCfg ? state.devCfg[field.key] : undefined;

    if (field.type === 'bool') {
      if (input.checked !== Boolean(current)) values[field.key] = input.checked;
      continue;
    }

    if (field.type === 'uint' || field.type === 'float' || field.type === 'double') {
      if (input.value === '') continue;
      const next = Number(input.value);
      if (!Number.isFinite(next)) continue;
      if (next !== Number(current)) values[field.key] = next;
      continue;
    }

    if (field.type === 'color') {
      const next = (input && typeof input._readRgba === 'function')
        ? composeRgba(input._readRgba())
        : normalizeColor(current);
      if (next !== normalizeColor(current)) values[field.key] = next;
      continue;
    }

    // string
    if (field.secret) {
      if (input.value !== '') values[field.key] = input.value;
    } else if (input.value !== String(current ?? '')) {
      values[field.key] = input.value;
    }
  }

  return values;
}

async function applyDev() {
  if (!state.schema) return;
  const values = collectDevChanges();
  const keys = Object.keys(values);

  if (!keys.length) {
    setStatus($('devStatus'), 'Nothing changed.', '');
    return;
  }

  setBusy($('applyDevBtn'), true, 'Applying…');
  setStatus($('devStatus'), `Applying ${keys.length} change${keys.length === 1 ? '' : 's'}…`, '');

  try {
    const response = await request('setDev', { values }, 30000);

    if (response.error === 'auth_required') {
      lockDev('Developer session expired. Unlock again.');
      return;
    }

    if (response.config) {
      state.devCfg = response.config;
      state.limits = response.limits || state.limits;
    }
    renderDev();

    const parts = [];
    if (response.applied !== undefined) parts.push(`${response.applied} applied`);
    if (response.rejected) parts.push(`${response.rejected} rejected`);
    if (response.errors) parts.push(response.errors);
    if (response.saved === false) parts.push('flash save failed');

    const good = response.ok && response.saved !== false;
    setStatus($('devStatus'), parts.join(' · ') || 'Done.', good ? 'ok' : 'err');

    // Keep the public tab consistent if the developer changed public settings.
    try { await loadPublic(); } catch { /* device may be busy */ }
  } catch (err) {
    handleDevError(err);
  } finally {
    setBusy($('applyDevBtn'), false, null, 'Apply changes');
  }
}

async function exitSetup() {
  try {
    const response = await request('exit', {}, 6000);
    if (!response.ok) throw new Error(response.error || 'Exit request failed');
    setStatus($('devStatus'), 'Ring is leaving setup mode.', 'ok');
    setTimeout(disconnect, 900);
  } catch (err) {
    setStatus($('devStatus'), err.message || String(err), 'err');
  }
}

async function exitSetupFromPublic() {
  try {
    const response = await request('exit', {}, 6000);
    if (!response.ok) throw new Error(response.error || 'Exit request failed');
    setStatus($('publicStatus'), 'Ring is leaving setup mode.', 'ok');
    setTimeout(disconnect, 900);
  } catch (err) {
    setStatus($('publicStatus'), err.message || String(err), 'err');
  }
}

async function factoryReset() {
  const ok = window.confirm(
    'Factory reset will erase every saved setting on the ring and restore defaults. Continue?',
  );
  if (!ok) return;
  try {
    const response = await request('factoryReset', {}, 10000);
    if (!response.ok) throw new Error(response.error || 'Factory reset failed');

    if (response.config) state.devCfg = response.config;
    if (response.limits) state.limits = response.limits;
    renderDev();
    try { await loadPublic(); } catch { /* keep the status below */ }

    setStatus(
      $('devStatus'),
      response.saved === false
        ? 'Defaults applied, but the ring could not save them to flash.'
        : 'Defaults restored. The ring is still in setup mode.',
      response.saved === false ? 'err' : 'ok',
    );
  } catch (err) {
    setStatus($('devStatus'), err.message || String(err), 'err');
  }
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------
function init() {
  $('connectBtn').addEventListener('click', connect);
  $('disconnectBtn').addEventListener('click', disconnect);

  document.querySelectorAll('.tab').forEach((button) => {
    button.addEventListener('click', () => showTab(button.dataset.tab));
  });

  $('anonRange').addEventListener('input', () => {
    $('anonNum').value = $('anonRange').value;
    $('anonOut').textContent = `${$('anonRange').value} m`;
  });

  $('anonNum').addEventListener('input', () => {
    const value = Number($('anonNum').value);
    const min = Number($('anonNum').min || 0);
    const max = Number($('anonNum').max || 5000);
    if (Number.isFinite(value)) {
      $('anonRange').value = String(clamp(Math.round(value), min, max));
      $('anonOut').textContent = `${Math.round(value)} m`;
    }
  });

  $('brightRange').addEventListener('input', () => {
    $('brightOut').textContent = $('brightRange').value;
    updatePreview();
  });

  $('applyPublicBtn').addEventListener('click', applyPublic);
  $('exitPublicBtn').addEventListener('click', exitSetupFromPublic);

  $('unlockBtn').addEventListener('click', unlock);
  $('pinInput').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      unlock();
    }
  });

  $('reloadDevBtn').addEventListener('click', () => loadDev(true));
  $('applyDevBtn').addEventListener('click', applyDev);
  $('exitSetupBtn').addEventListener('click', exitSetup);
  $('factoryBtn').addEventListener('click', factoryReset);

  $('devForm').addEventListener('submit', (event) => event.preventDefault());

  $('clearLogBtn').addEventListener('click', () => {
    $('log').textContent = '';
  });

  const problem = environmentProblem();
  if (problem) {
    $('secureHint').hidden = false;
    $('secureHint').textContent = problem;
    $('connectBtn').disabled = true;
  }

  updatePreview();
  setConnectionUI();
}

document.addEventListener('DOMContentLoaded', init);
