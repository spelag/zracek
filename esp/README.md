# Air-quality ring — firmware + BLE companion app

An ESP32-C3 ring that fetches an air-quality forecast and renders it on a
10-LED strip. This repo also contains a **Web Bluetooth companion app** for
configuring the ring from an Android phone.

> **Demo firmware:** deep sleep has been removed. The demo stays awake, runs a
> cycle on boot and on each button press, and never restarts the ESP. That
> avoids the white boot flash and repeated cold GPS hunts during a
> demonstration.

```
.
├── firmware.cpp          # ESP32-C3 firmware (Arduino)
└── companion/
    ├── index.html        # public settings + developer console
    ├── styles.css
    └── app.js            # Web Bluetooth transport + UI
```

---

## 1. What was added

### Configuration moved into NVS flash

Every runtime setting now lives in one `Config` struct and is saved as a JSON
blob in the `aircfg` NVS namespace. Defaults are C++ member initialisers, and a
stored blob is merged over those defaults at boot, so adding a new field in a
future firmware version is backward-compatible.

The `Config` field table (`FIELDS[]` in `firmware.cpp`) is the single source of
truth. It drives:

* JSON serialisation / deserialisation,
* validation and clamping,
* the developer schema sent to the app,
* and therefore the auto-generated developer form.

Add a field to the struct and one `FD(...)` row, and it appears in the app's
developer tab automatically.

Compile-time hardware facts (pin numbers, `NUM_LEDS`, `WS2812B`/`GRB`) remain
in firmware because they describe the physical build.

### Setup (config) mode

The demo firmware runs continuously and only exposes BLE while it is in a
dedicated setup mode. Enter it by either:

1. **Holding the button while cold-booting** the ring (power-cycle while
   holding), or
2. **Holding the button for ~1.8 s** while a forecast is displayed. This also
   works on the red “Wi-Fi failed” / “API failed” indication.

In setup mode:

* Wi-Fi is shut down before BLE starts (important on a C3 — see
  [Memory](#5-memory-notes)). The GPS UART keeps running so the fix stays warm.
* The ring advertises a custom GATT service and shows a live LED preview:
  the first pixel is green when a phone is connected, pulsing blue while
  advertising, and the rest shows the configured class colours at the current
  brightness.
* Everything is saved to NVS as it is changed.
* The app's **Exit setup** button (or the `exit` command) tears down BLE and
  returns to the demo loop. A 5-minute idle timeout does the same if no phone is
  connected (`configModeTimeoutMs`).
* **No deep sleep and no reboots.** Exiting setup returns to the idle demo
  state; the next button press runs a normal cycle.
* Holding the button at boot keeps USB serial alive, so flashing still works.

### Public vs developer access

| | Public interface | Developer console |
|---|---|---|
| GPS anonymization distance | ✅ bounded by `minAnonymizeMeters` / `maxAnonymizeMeters` | ✅ plus the bounds themselves |
| LED brightness | ✅ capped by `maxBrightness` | ✅ plus the cap |
| Everything else | ❌ | ✅ behind a PIN |
| Read Wi-Fi password | ❌ never sent | ❌ never sent (write-only) |

The public app can only read/write fields flagged `FF_PUBLIC`. The firmware
clamps public writes to the developer-configured limits and returns the actual
stored values, so the UI always reflects what the ring accepted.

The developer PIN is a **compile-time constant** in `firmware.cpp`:

```cpp
constexpr const char* DEV_PIN = "271828";
```

Change it before shipping. After 5 wrong attempts the ring disconnects the
client. The PIN is a convenience lock, not cryptographic security: BLE links are
not paired/encrypted in this design, and anyone with the firmware image can read
the PIN. Do not reuse it for anything sensitive.

---

## 2. Building the firmware

### Requirements

* Arduino ESP32 core **3.3.x** (verified with 3.3.0; also builds with the
  ESP32-C3 3.3.x cores used by recent Arduino IDE releases). **Core 4.0
  currently fails to compile NimBLE-Arduino 2.5.2 itself** (`ble_npl_event`
  layout mismatch), so use 3.3.x for now.
* Board: ESP32-C3 (GPIO0–5 usable, GPIO21 LED data — adjust compile-time pins if
  your board differs).
* Libraries:
  * **NimBLE-Arduino 2.x** (`NimBLEDevice.h`) — install “NimBLE-Arduino” from the
    Library Manager, version 2.0 or newer (verified with 2.5.2). The code targets
    the 2.x callback API (`NimBLEConnInfo&`); it will **not** compile against 1.x
    without changes.
  * FastLED (verified 3.10.10)
  * TinyGPSPlus (verified 1.0.3)
  * ArduinoJson 7.x (verified 7.4.3)
  * Preferences (bundled)

### Partition scheme

The image now links BLE and TLS. With the default 4 MB / 1.2 MB app layout the
build is **too big** (about 1.45 MB), so select a partition scheme with a 2 MB
app partition:

* Arduino IDE: **Tools → Partition Scheme → “No OTA (2MB APP/2MB SPIFFS)”**
  (or “No FS 4MB (2MB APP x2)”).
* arduino-cli: add `:PartitionScheme=no_ota` to the FQBN.

Verified build output:

```
Sketch uses 1449212 bytes (69%) of program storage space. Maximum is 2097152 bytes.
Global variables use 47844 bytes (14%) of dynamic memory, leaving 279836 bytes for local variables.
```

The default 1.2 MB partition reports `Sketch too big`. There is no PSRAM on the
C3, so this partition choice matters.

### Optional: trimming NimBLE further

NimBLE is already about 3× smaller than Bluedroid in RAM, but if you are tight
you can compile only the peripheral role. With PlatformIO:

```ini
build_flags =
  -DCONFIG_BT_NIMBLE_ROLE_CENTRAL_DISABLE=1
  -DCONFIG_BT_NIMBLE_ROLE_OBSERVER_DISABLE=1
  -DCONFIG_BT_NIMBLE_MAX_CONNECTIONS=1
  -DCONFIG_BT_NIMBLE_ATT_PREFERRED_MTU=247
```

(These are library compile-time options; the values above are safe for this
firmware, which is peripheral-only and has exactly one client.)

### Flashing

Holding the button at cold boot enters setup mode and keeps the serial port
alive, so the usual “hold the button to flash” workflow still works. Setup mode
never sleeps; it only returns to the demo loop after the idle timeout or when
the app exits it, so you can stay connected as long as you need.

---

## 3. Running the companion app

### It must be a secure context

Web Bluetooth only exists in a **secure context**: `https://`, or
`http://localhost` / `http://127.0.0.1`. A plain `http://192.168.x.x` page will
throw `SecurityError`, and Chrome will not show the Bluetooth picker.

Chrome on Android supports Web Bluetooth; Firefox, Safari and iOS do not.

Three practical ways to run it:

**A. Local dev over `adb reverse` (recommended while hacking)**

```bash
cd companion
python3 -m http.server 8080
# in another terminal, with the phone connected over USB:
adb reverse tcp:8080 tcp:8080
```

Then open `http://localhost:8080` on the phone. `localhost` counts as a secure
context even over `adb reverse`.

**B. Host it with HTTPS**

Put the `companion/` directory on GitHub Pages, Netlify, Cloudflare Pages or any
HTTPS static host. No build step, no backend.

**C. Local HTTPS**

Use `mkcert` (or any trusted local certificate) and serve the folder with an
HTTPS static server. A self-signed certificate that Chrome rejects will not give
you a secure context.

### Android permissions

* Bluetooth on.
* **Location services on** — Android's BLE scan path historically requires it,
  and Chrome may return an empty picker otherwise.
* Allow the “Nearby devices” / Bluetooth permission when Chrome prompts.
* Keep the ring within a few metres during pairing.

### Using it

1. Put the ring in setup mode.
2. Open the app and tap **Connect to ring**; pick `AirRing-XXXX` in the chooser.
3. **Ring settings** (public):
   * GPS anonymization distance (grid snap before upload).
   * LED brightness, capped by the developer's maximum. The 10-dot preview
     dims with the slider.
   * **Exit setup** hands the ring back to the demo when you are done.
4. **Developer**:
   * Enter the PIN.
   * The form is generated from the ring's schema, grouped into sections.
   * `public` / `secret` badges explain each field.
   * Secret fields (Wi-Fi password) are never read back; leave blank to keep
     the current value, type a new one to replace it.
   * **Apply changes** sends only the fields you edited.
   * **Exit setup** leaves setup mode; BLE is torn down and the ring returns to
     its idle demo state (next button press shows a forecast).
   * **Factory reset** erases the `aircfg` NVS namespace, applies defaults in
     place, and stays in setup mode. It does **not** restart the ESP.

The collapsible **Protocol log** shows every request/response, which is the
fastest way to debug a mismatch.

---

## 4. BLE protocol

### GATT

| UUID | Direction | Purpose |
|---|---|---|
| `5f9b1000-7a1e-4c3b-9b1d-1e2f3a4b5c6d` | service | config service (advertised) |
| `5f9b1001-7a1e-4c3b-9b1d-1e2f3a4b5c6d` | phone → ring (write) | request frames |
| `5f9b1002-7a1e-4c3b-9b1d-1e2f3a4b5c6d` | ring → phone (notify) | response frames |

The device advertises the service UUID, so the app filters by service rather
than by name. The advertised name is `<bleName>-<chip suffix>` (default
`AirRing-1A2B`).

### Framing

Characteristics are limited to 512 bytes and notifications are limited by the
negotiated MTU, so every message is split into chunks:

```
byte 0:   flags   bit0 = first chunk, bit1 = last chunk
byte 1..: UTF-8 JSON payload (up to 180 bytes per chunk)
```

The receiver concatenates chunks until the last flag. A single-chunk message has
flags `0b11`. NimBLE's default preferred MTU is 255, so a 181-byte notification
frame fits comfortably once Android Chrome negotiates.<br>
The firmware also tracks the negotiated MTU and reduces the chunk size
automatically if a client somehow stays at the default 23-byte MTU. Chrome on
Android negotiates a large MTU; the 180-byte chunk keeps a safety margin for
write handling.

### Requests

All requests/responses are JSON. An optional integer `id` is echoed in the
response; the app uses it to match replies. Requests are serialised (one in
flight at a time).

```jsonc
// public
{"id":1,"op":"hello"}
{"op":"getPublic"}
{"op":"setPublic","values":{"anonymizeMeters":250,"ledBrightness":160}}

// developer
{"op":"auth","pin":"271828"}
{"op":"getSchema"}
{"op":"getDev"}
{"op":"setDev","values":{"requestHours":24,"colorClass1":"#00FF80"}}

// lifecycle
{"op":"exit"}           // leave setup mode, no reset (no auth)
{"op":"sleep"}          // legacy alias for exit (no auth)
{"op":"factoryReset"}   // auth; apply defaults, stay running
```

The firmware deliberately has no reboot/sleep operation: nothing in the demo
restarts or deep-sleeps the ESP. A legacy client sending `{"op":"reboot"}` gets
`{"ok":false,"error":"unsupported"}`.

Example responses:

```json
{"id":1,"ok":true,"device":"AirRing-1A2B","fw":"1.1.0-ble","mac":"…",
 "configMode":true,"authenticated":false,"limits":{…}}

{"ok":true,
 "config":{"anonymizeMeters":250,"ledBrightness":160},
 "limits":{"anonymizeMin":50,"anonymizeMax":2000,"brightnessMax":200}}

{"ok":true,"applied":2,"rejected":0,"saved":true,
 "config":{…},"limits":{…}}
```

Errors are `{"ok":false,"error":"…"}`. Important codes:

| error | meaning |
|---|---|
| `auth_required` | developer operation attempted before `auth` |
| `bad_pin` | wrong PIN; `attemptsLeft` is included |
| `low_memory` | device could not allocate the response safely |
| `missing_values` | request had no `values` object |
| `nvs_erase_failed` | factory reset could not open/clear NVS |
| `bad_json` / `unknown_op` | malformed request |

### Schema

`getSchema` returns (after auth):

```json
{"ok":true,"schema":{"version":1,"device":"air-ring","fields":[
  {"key":"anonymizeMeters","label":"GPS anonymization distance",
   "group":"Public settings","type":"float","min":0,"max":100000,
   "step":10,"public":true,"secret":false,"reboot":false,
   "help":"…","default":250},
  …
]}}
```

Types: `bool`, `uint`, `float`, `double`, `string`, `color` (`#RRGGBB`).
Strings include `maxLength`. Secret strings omit `default` and are never sent in
`getDev` responses.

---

## 5. Memory notes (ESP32-C3)

BLE + TLS on a C3 is the classic out-of-memory trap. This firmware handles it in
several layers:

1. **NimBLE instead of Bluedroid.** NimBLE's static RAM footprint is roughly
   53 KB versus ~180 KB for Bluedroid, and ~50% less flash. It is still linked
   into the image and reserves its static buffers from boot, even during normal
   fetch/display cycles.
2. **BLE and TLS are mutually exclusive at runtime.**
   * Normal cycles never initialise BLE.
   * Setup mode shuts Wi-Fi down *before* Bluetooth starts, and only if Wi-Fi was
     actually initialised (so a cold-boot setup session never allocates it).
   * Setup mode never fetches over HTTPS.
   * Leaving setup mode deinitialises the BLE stack (`NimBLEDevice::deinit(true)`),
     returning its dynamic allocations to the heap before the next fetch. The
     ESP stays awake, so the TLS heap gate is what protects fragmented sessions
     rather than a reset.
   * The GPS UART keeps running during setup and while idle, so fixes stay warm
     and repeated button presses do not re-trigger cold-start hunting.
3. **TLS heap gate.** Before an HTTPS request the firmware checks the *largest
   contiguous internal-RAM block* (`heap_caps_get_largest_free_block`), not just
   free heap. If it is under ~45 KB it skips the fetch and shows the red status
   LED instead of letting mbedTLS fail mid-handshake. Free heap and the largest
   block are logged every cycle.
4. **TLS handshake timeout.** `setHandshakeTimeout(15)` prevents a weak link
   from blocking for the library default. The old `setBufferSizes()` knob is no
   longer exposed by the secure client in ESP32 core 3.x/4.x, so the heap gate
   above is the primary protection.
5. **Setup-mode guards.** Adding/removing config, building the schema, and
   saving NVS all check free heap and fail with `low_memory` rather than
   crashing. The BLE request queue is checked at startup.
6. **Defaults in flash.** `g_defaults` is `constexpr`, so the default
   `Config` copy lives in flash instead of RAM.

Still stuck? Trim NimBLE to peripheral-only with the build flags in
[Building](#2-building-the-firmware), and pick a partition scheme with a bigger
app slot.

---

## 6. Field reference

Everything below is editable in the developer tab. Fields marked **public** are
also in the public interface.

### Public settings
| key | default | notes |
|---|---|---|
| `anonymizeMeters` | 250 | grid snap before upload, clamped to the limits below |
| `ledBrightness` | 160 | 0–255, clamped to `maxBrightness` |

### Public limits
| key | default | notes |
|---|---|---|
| `minAnonymizeMeters` | 50 | |
| `maxAnonymizeMeters` | 2000 | |
| `maxBrightness` | 200 | hard ceiling for public brightness |

### Diagnostics
`debugMode`, `dumpRawNmea`, `serialWaitOnBoot`, `serialWaitMaxMs`.

### LED ring
`ledVolts`, `ledMaxMa`, `enableStatusLed`, `bootSweepEnabled`, `bootSweepMs`,
`fadeMs`, `requestHours`, `includeCurrent`, `downscaleWorstCase`,
`startLedIndex`, `reverseRing`, `colorClass1`…`colorClass5`.

### Timing
`displayDurationMs`, `wifiTimeoutMs`, `gpsMaxFixAgeMs`, `httpTimeoutMs`,
`configModeTimeoutMs`.

### Location
`useLocationCache`, `locationValidHours`, `gpsQuickProbeMs`, `gpsStaleRetryMs`,
`gpsColdHuntMs`, `useDefaultLocation`, `defaultLat`, `defaultLon`, `gpsBaud`.

### Network
`wifiSsid`, `wifiPassword` (write-only), `apiEndpoint`, `tlsInsecure`,
`rootCaCert`.

### Bluetooth
`bleName` (default `AirRing`; a chip suffix is appended; the new name is used on
the next setup-mode entry).

Compile-time only (not in the app): pin assignments, `NUM_LEDS`, LED chipset and
colour order, `CONFIG_HOLD_MS`, `DEV_PIN`.

---

## 7. Testing checklist

1. Flash, cold-boot with the button held → serial prints `entering setup mode`
   and the first LED pulses blue; the rest shows the palette. It must **not**
   reset/crash while the button is held.
2. `getSchema` and the developer form render all fields; editing a field and
   pressing Apply returns `applied: 1, saved: true`.
3. Exit setup from the app → the ring disconnects, returns to idle, and the next
   button press shows a forecast **without an ESP reset**.
4. Wake with a press, then hold ~2 s during the forecast → setup mode re-appears.
5. Set public brightness to 255 with `maxBrightness=100`; the app should read
   back 100 and the ring should visibly cap.
6. Set `anonymizeMeters` below `minAnonymizeMeters`; it should clamp to the
   minimum in the response.
7. Wrong PIN five times → disconnect; reconnect and the PIN counter resets.
8. Factory reset → defaults are applied immediately (250 / 160 and 50 / 2000 /
   200) and the ESP does **not** restart.
9. Leave setup mode with no phone connected → it returns to the demo loop after
   the idle timeout (5 min default).
10. Press the button repeatedly: each cycle should start quickly because Wi-Fi
    stays connected and the GPS parser keeps running while idle.
11. Run a normal cycle and check the serial log for `largest internal block`
    before the HTTPS request; it should be comfortably above 45 KB. If not,
    reduce the app/partition size or trim NimBLE.

---

## References

* [MDN: Web Bluetooth API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Bluetooth_API) — secure context, transient user activation, Android Chrome support.
* [MDN: Bluetooth.requestDevice()](https://developer.mozilla.org/en-US/docs/Web/API/Bluetooth/requestDevice) — filters, `optionalServices`, errors.
* [Chrome: Communicating with Bluetooth devices over JavaScript](https://developer.chrome.com/docs/capabilities/bluetooth) — GATT usage and platform notes.
* [Web Bluetooth spec — security and privacy](https://webbluetoothcg.github.io/web-bluetooth/) — permissions policy and user-gesture requirements.
* [NimBLE-Arduino 1.x → 2.x migration guide](https://github.com/h2zero/NimBLE-Arduino/blob/master/docs/1.x_to2.x_migration_guide.md) — callback signatures, advertising and MTU changes.
* [NimBLE-Arduino command-line/config options](https://h2zero.github.io/NimBLE-Arduino/md__command__line__config.html) — preferred MTU, role pruning.
* [Arduino-ESP32 WiFiClientSecure heap optimisation](https://github.com/espressif/arduino-esp32/pull/471) — buffer sizing and TLS memory behaviour.
