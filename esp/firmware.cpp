#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClient.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <FastLED.h>
#include <TinyGPSPlus.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <NimBLEDevice.h>
#include "esp_heap_caps.h"

// ============================================================================
// AIR-QUALITY RING - locate, fetch, render (continuous demo build)
//
// This demo build deliberately has NO deep sleep and never restarts the ESP.
// The device boots, runs one cycle, then waits for button presses and runs a
// cycle per press. Wi-Fi stays connected between cycles and the GPS UART is
// pumped continuously, so fixes stay warm and subsequent presses are fast.
//
// Location policy:
//   * A live fix is cached in NVS flash together with its UTC timestamp.
//   * A cycle probes for a fresh fix for ~gpsQuickProbeMs.
//   * If none arrives:
//       - cached fix younger than locationValidHours -> use it immediately.
//       - cached fix older (or age unknown)          -> try a bit longer,
//                                                       then use it anyway.
//       - no cached fix at all                       -> hunt up to gpsColdHuntMs.
//
// Companion-app / configuration support:
//   * Every runtime setting lives in the Config struct below and is persisted
//     in NVS. Compile-time hardware facts (pins, LED count, LED chipset) stay
//     in firmware.
//   * Holding the button while cold-booting, or holding it for ~1.8 s while
//     the ring is displaying a forecast, enters setup mode. In setup mode the
//     device advertises a BLE service; Wi-Fi is stopped and BLE is torn down
//     again when the app exits setup mode or the idle timeout expires.
//   * The BLE protocol is a chunked JSON request/response protocol. Public
//     clients may read/write the two public settings. Everything else is
//     behind the developer PIN.
// ============================================================================

// ============================================================================
// COMPILE-TIME HARDWARE CONFIGURATION
// These describe the physical build, so they cannot move into NVS.
// ============================================================================

// --- Pinout ---
constexpr uint8_t  BUTTON_PIN               = 1;      // switch between GPIO1 and 3.3V (GPIO0-5 on ESP32-C3)
constexpr uint8_t  LED_DATA_PIN             = 21;     // WS2812B data pin
constexpr uint8_t  GPS_RX_PIN               = 6;      // ESP32 RX <- NEO-7M TX
constexpr uint8_t  GPS_TX_PIN               = 5;      // ESP32 TX -> NEO-7M RX

// --- LED ring hardware ---
constexpr uint16_t NUM_LEDS                 = 10;     // physical LEDs in the ring
#define            LED_TYPE                 WS2812B
#define            COLOR_ORDER              GRB

// Setup-mode behaviour (not exposed: it guards entry into the config UI).
constexpr uint32_t CONFIG_HOLD_MS          = 1800;   // hold this long while displaying -> setup mode
constexpr const char* DEV_PIN              = "271828"; // developer PIN (change before shipping!)
constexpr const char* FW_VERSION           = "1.1.0-ble";

// ============================================================================
// RUNTIME CONFIGURATION
// Defaults are C++ member initializers, so a default-constructed Config is the
// firmware default. NVS values are merged over those defaults at boot, which
// makes adding new fields backward-compatible.
// ============================================================================
struct Config {
    // ---- Public settings (safe for anyone to change) ----
    float    anonymizeMeters     = 250.0f;   // GPS grid snap before upload (0 = exact)
    uint8_t  ledBrightness       = 160;      // 0..255, also capped by maxBrightness

    // ---- Developer-managed limits for the public settings ----
    float    minAnonymizeMeters  = 50.0f;
    float    maxAnonymizeMeters  = 2000.0f;
    uint8_t  maxBrightness       = 200;

    // ---- Diagnostics ----
    bool     debugMode           = false;    // verbose logs
    bool     dumpRawNmea         = false;    // echo raw NMEA (debug builds)
    bool     serialWaitOnBoot    = true;     // brief wait for USB CDC reconnect
    uint32_t serialWaitMaxMs     = 1200;

    // ---- LED ring & forecast mapping ----
    uint8_t  ledVolts            = 3;        // FastLED power limiting
    uint16_t ledMaxMa            = 500;
    bool     enableStatusLed     = true;
    bool     bootSweepEnabled    = true;     // one-time sweep to verify every LED
    uint32_t bootSweepMs         = 1400;     // two laps + fade out
    uint16_t fadeMs              = 450;      // crossfade when the display changes
    uint8_t  requestHours        = 12;       // hours requested from the API
    bool     includeCurrent      = true;     // index 0 = latest measurement
    bool     downscaleWorstCase  = true;     // bucket downscale = worst class
    uint8_t  startLedIndex       = 0;        // ring index for hour [0]
    bool     reverseRing         = false;
    uint32_t colorClass1         = 0x00C83C; // good         - green
    uint32_t colorClass2         = 0xFFC800; // satisfactory - yellow
    uint32_t colorClass3         = 0xFF5A00; // moderate     - orange
    uint32_t colorClass4         = 0xD21400; // bad          - red
    uint32_t colorClass5         = 0x820000; // very bad     - dark red

    // ---- Timing (ms unless noted) ----
    uint32_t displayDurationMs   = 8000;
    uint32_t wifiTimeoutMs       = 8000;
    uint32_t gpsMaxFixAgeMs      = 2000;
    uint32_t httpTimeoutMs       = 15000;
    uint32_t configModeTimeoutMs = 300000;   // leave setup mode after this idle time

    // ---- Location cache policy ----
    bool     useLocationCache    = true;
    uint32_t locationValidHours  = 6;
    uint32_t gpsQuickProbeMs     = 2500;
    uint32_t gpsStaleRetryMs     = 6000;
    uint32_t gpsColdHuntMs       = 60000;

    // ---- Demo fallback ----
    bool     useDefaultLocation  = true;
    double   defaultLat          = 46.0569;  // Ljubljana centre
    double   defaultLon          = 14.5058;

    // ---- GPS ----
    uint32_t gpsBaud             = 9600;

    // ---- Wi-Fi ----
    char     wifiSsid[34]        = "fJ47DeQ1";
    char     wifiPassword[65]    = "fJ47DeQ1";

    // ---- API ----
    char     apiEndpoint[192]    = "https://ada.juje.si/air-quality";
    bool     tlsInsecure         = true;
    char     rootCaCert[2048]    = "-----BEGIN CERTIFICATE-----\n"
                                   "REPLACE_WITH_YOUR_SERVER_ROOT_CA_IF_TLS_INSECURE_IS_FALSE\n"
                                   "-----END CERTIFICATE-----\n";

    // ---- BLE identity ----
    char     bleName[24]         = "AirRing";
};

static Config            g_cfg;          // active configuration
static constexpr Config  g_defaults{};   // pristine defaults, kept in flash (not RAM)

static const char* CFG_NAMESPACE = "aircfg";
static const char* CFG_KEY       = "cfg";

// ============================================================================
// CONFIG FIELD TABLE
// The same table drives JSON read/write, validation, the developer schema, and
// therefore the entire developer UI. Add a field here and it shows up in the
// app automatically.
// ============================================================================
enum class FType : uint8_t { BOOL, UINT, FLOAT, DOUBLE, STRING, COLOR };

enum : uint8_t {
    FF_PUBLIC = 1 << 0,   // visible/writable through the public interface
    FF_SECRET = 1 << 1,   // never sent to clients; blank on write = unchanged
    FF_REBOOT = 1 << 2    // useful after reboot (UI hint)
};

struct FieldDef {
    const char* key;
    const char* label;
    const char* group;
    FType       type;
    uint16_t    offset;
    uint16_t    size;
    double      lo;
    double      hi;
    double      step;
    uint8_t     flags;
    const char* help;
};

#define CFG_OFF(f) ((uint16_t)offsetof(Config, f))
#define CFG_SZ(f)  ((uint16_t)sizeof(((Config*)nullptr)->f))
#define FD(k, l, g, t, f, lo, hi, st, fl, h) \
    { k, l, g, t, CFG_OFF(f), CFG_SZ(f), lo, hi, st, fl, h }

static const FieldDef FIELDS[] = {
    // ---- Public settings ----
    FD("anonymizeMeters", "GPS anonymization distance", "Public settings",
       FType::FLOAT, anonymizeMeters, 0, 100000, 10, FF_PUBLIC,
       "Snap the GPS position to a grid of this size before uploading. Larger = more privacy."),
    FD("ledBrightness", "LED brightness", "Public settings",
       FType::UINT, ledBrightness, 0, 255, 1, FF_PUBLIC,
       "Ring brightness, capped by the developer's maximum brightness."),

    // ---- Public limits ----
    FD("minAnonymizeMeters", "Minimum anonymization distance", "Public limits",
       FType::FLOAT, minAnonymizeMeters, 0, 100000, 10, 0,
       "Public users cannot choose a smaller anonymization distance than this."),
    FD("maxAnonymizeMeters", "Maximum anonymization distance", "Public limits",
       FType::FLOAT, maxAnonymizeMeters, 0, 100000, 10, 0,
       "Public users cannot choose a larger anonymization distance than this."),
    FD("maxBrightness", "Maximum public brightness", "Public limits",
       FType::UINT, maxBrightness, 0, 255, 1, 0,
       "Hard cap applied to the public brightness setting."),

    // ---- Diagnostics ----
    FD("debugMode", "Verbose serial logging", "Diagnostics",
       FType::BOOL, debugMode, 0, 1, 1, 0, "Print detailed debug output to serial."),
    FD("dumpRawNmea", "Dump raw NMEA", "Diagnostics",
       FType::BOOL, dumpRawNmea, 0, 1, 1, 0, "Echo raw GPS NMEA sentences (requires debug logging)."),
    FD("serialWaitOnBoot", "Wait for USB serial on boot", "Diagnostics",
       FType::BOOL, serialWaitOnBoot, 0, 1, 1, 0, "Give USB CDC a moment to reconnect before logging."),
    FD("serialWaitMaxMs", "USB serial wait limit (ms)", "Diagnostics",
       FType::UINT, serialWaitMaxMs, 0, 10000, 100, 0, "Maximum time to wait for USB serial."),

    // ---- LED ring ----
    FD("ledVolts", "LED supply voltage", "LED ring",
       FType::UINT, ledVolts, 1, 5, 1, 0, "Voltage used by FastLED power limiting."),
    FD("ledMaxMa", "LED current limit (mA)", "LED ring",
       FType::UINT, ledMaxMa, 50, 2000, 50, 0, "FastLED current limit for the whole ring."),
    FD("enableStatusLed", "Enable status LED", "LED ring",
       FType::BOOL, enableStatusLed, 0, 1, 1, 0, "Use one pixel for acquisition/error feedback."),
    FD("bootSweepEnabled", "Boot sweep", "LED ring",
       FType::BOOL, bootSweepEnabled, 0, 1, 1, 0, "Sweep the ring once per cold boot to verify every LED."),
    FD("bootSweepMs", "Boot sweep duration (ms)", "LED ring",
       FType::UINT, bootSweepMs, 0, 20000, 100, 0, "Length of the cold-boot LED check."),
    FD("fadeMs", "Crossfade duration (ms)", "LED ring",
       FType::UINT, fadeMs, 0, 5000, 50, 0, "Fade time when the display changes."),
    FD("requestHours", "Forecast hours requested", "LED ring",
       FType::UINT, requestHours, 1, 48, 1, 0, "How many hours of forecast to fetch from the API."),
    FD("includeCurrent", "Include current measurement", "LED ring",
       FType::BOOL, includeCurrent, 0, 1, 1, 0, "Use the latest measurement as index 0."),
    FD("downscaleWorstCase", "Downscale by worst case", "LED ring",
       FType::BOOL, downscaleWorstCase, 0, 1, 1, 0,
       "When mapping hours onto LEDs, take the worst class rather than the average."),
    FD("startLedIndex", "First ring index", "LED ring",
       FType::UINT, startLedIndex, 0, NUM_LEDS - 1, 1, 0, "Physical LED used for the first forecast hour."),
    FD("reverseRing", "Reverse ring direction", "LED ring",
       FType::BOOL, reverseRing, 0, 1, 1, 0, "Run the forecast clockwise instead of counter-clockwise."),
    FD("colorClass1", "Class 1 colour (good)", "LED ring",
       FType::COLOR, colorClass1, 0, 0, 0, 0, "Colour for the best air-quality class."),
    FD("colorClass2", "Class 2 colour (satisfactory)", "LED ring",
       FType::COLOR, colorClass2, 0, 0, 0, 0, "Colour for class 2."),
    FD("colorClass3", "Class 3 colour (moderate)", "LED ring",
       FType::COLOR, colorClass3, 0, 0, 0, 0, "Colour for class 3."),
    FD("colorClass4", "Class 4 colour (bad)", "LED ring",
       FType::COLOR, colorClass4, 0, 0, 0, 0, "Colour for class 4."),
    FD("colorClass5", "Class 5 colour (very bad)", "LED ring",
       FType::COLOR, colorClass5, 0, 0, 0, 0, "Colour for the worst air-quality class."),

    // ---- Timing ----
    FD("displayDurationMs", "Forecast display time (ms)", "Timing",
       FType::UINT, displayDurationMs, 0, 600000, 500, 0, "How long a forecast stays on the ring."),
    FD("wifiTimeoutMs", "Wi-Fi timeout (ms)", "Timing",
       FType::UINT, wifiTimeoutMs, 1000, 120000, 500, 0, "Give up on Wi-Fi after this long."),
    FD("gpsMaxFixAgeMs", "Maximum fix age (ms)", "Timing",
       FType::UINT, gpsMaxFixAgeMs, 100, 60000, 100, 0, "Reject NMEA fixes older than this."),
    FD("httpTimeoutMs", "HTTP timeout (ms)", "Timing",
       FType::UINT, httpTimeoutMs, 1000, 120000, 500, 0, "Air-quality API request timeout."),
    FD("configModeTimeoutMs", "Setup mode idle timeout (ms)", "Timing",
       FType::UINT, configModeTimeoutMs, 30000, 3600000, 30000, 0,
       "Return to the demo loop after this long with no BLE connection in setup mode."),

    // ---- Location ----
    FD("useLocationCache", "Use cached location", "Location",
       FType::BOOL, useLocationCache, 0, 1, 1, 0, "Fall back to the last known fix when no new one arrives."),
    FD("locationValidHours", "Cached fix validity (hours)", "Location",
       FType::UINT, locationValidHours, 1, 720, 1, 0, "Trust a cached fix for this long without a new fix."),
    FD("gpsQuickProbeMs", "Quick GPS probe (ms)", "Location",
       FType::UINT, gpsQuickProbeMs, 500, 120000, 100, 0, "Initial probe before falling back to the cache."),
    FD("gpsStaleRetryMs", "Stale-cache retry (ms)", "Location",
       FType::UINT, gpsStaleRetryMs, 500, 600000, 500, 0, "Extra probing when the cached fix is stale."),
    FD("gpsColdHuntMs", "Cold-start hunt (ms)", "Location",
       FType::UINT, gpsColdHuntMs, 1000, 600000, 1000, 0, "Probing time when there is no cached fix at all."),
    FD("useDefaultLocation", "Use demo fallback", "Location",
       FType::BOOL, useDefaultLocation, 0, 1, 1, 0, "Use a fixed location when nothing else is available."),
    FD("defaultLat", "Demo latitude", "Location",
       FType::DOUBLE, defaultLat, -90, 90, 0.000001, 0, "Fallback latitude for demos."),
    FD("defaultLon", "Demo longitude", "Location",
       FType::DOUBLE, defaultLon, -180, 180, 0.000001, 0, "Fallback longitude for demos."),
    FD("gpsBaud", "GPS baud rate", "Location",
       FType::UINT, gpsBaud, 4800, 115200, 1, 0, "UART baud rate for the NEO-7M (normally 9600)."),

    // ---- Network ----
    FD("wifiSsid", "Wi-Fi SSID", "Network",
       FType::STRING, wifiSsid, 0, 0, 0, 0, "Wi-Fi network used for the air-quality API."),
    FD("wifiPassword", "Wi-Fi password", "Network",
       FType::STRING, wifiPassword, 0, 0, 0, FF_SECRET, "Saved on the device but never sent back."),
    FD("apiEndpoint", "API endpoint", "Network",
       FType::STRING, apiEndpoint, 0, 0, 0, 0, "Air-quality JSON endpoint."),
    FD("tlsInsecure", "Skip TLS verification", "Network",
       FType::BOOL, tlsInsecure, 0, 1, 1, 0,
       "Accept any HTTPS certificate. Set false and install a root CA for real use."),
    FD("rootCaCert", "Root CA certificate (PEM)", "Network",
       FType::STRING, rootCaCert, 0, 0, 0, 0, "Used when TLS verification is enabled."),

    // ---- Identity ----
    FD("bleName", "Bluetooth name", "Bluetooth",
       FType::STRING, bleName, 0, 0, 0, 0, "Advertising name prefix. A short chip suffix is appended; applied on the next setup-mode entry."),
};

static const size_t FIELD_COUNT = sizeof(FIELDS) / sizeof(FIELDS[0]);

// ============================================================================
// TYPES USED IN FUNCTION SIGNATURES
// The Arduino IDE auto-generates prototypes above the first function
// definition, so every type referenced by a signature must already be declared
// at that point. (A plain .cpp build does not need these hoisted.)
// ============================================================================
enum class LocSource : uint8_t { NONE, FRESH, CACHED, FALLBACK };

struct LocOutcome {
    LocSource source   = LocSource::NONE;
    double    lat      = 0.0;
    double    lon      = 0.0;
    uint32_t  ageSec   = 0;
    bool      ageKnown = false;
};

struct AcquireResult {
    bool       wifiOk = false;
    LocOutcome loc;
};

struct ApplyReport {
    int    applied  = 0;
    int    rejected = 0;
    bool   reboot   = false;
    String errors;
};

// ============================================================================
// LOGGING
// ============================================================================
#define INFO(...)   Serial.printf(__VA_ARGS__)
#define DBG(...)    do { if (g_cfg.debugMode) Serial.printf(__VA_ARGS__); } while (0)

// ============================================================================
// CONFIG HELPERS
// ============================================================================
static uint8_t* fieldBase(Config& c, const FieldDef& f) {
    return reinterpret_cast<uint8_t*>(&c) + f.offset;
}
static const uint8_t* fieldBase(const Config& c, const FieldDef& f) {
    return reinterpret_cast<const uint8_t*>(&c) + f.offset;
}

static bool readBool(const Config& c, const FieldDef& f) {
    uint8_t v = 0;
    memcpy(&v, fieldBase(c, f), f.size);
    return v != 0;
}
static uint32_t readUInt(const Config& c, const FieldDef& f) {
    uint32_t v = 0;
    memcpy(&v, fieldBase(c, f), f.size);
    return v;
}
static float readFloat(const Config& c, const FieldDef& f) {
    float v = 0;
    memcpy(&v, fieldBase(c, f), sizeof(v));
    return v;
}
static double readDouble(const Config& c, const FieldDef& f) {
    double v = 0;
    memcpy(&v, fieldBase(c, f), sizeof(v));
    return v;
}
static const char* readString(const Config& c, const FieldDef& f) {
    return reinterpret_cast<const char*>(fieldBase(c, f));
}
static uint32_t readColor(const Config& c, const FieldDef& f) {
    uint32_t v = 0;
    memcpy(&v, fieldBase(c, f), sizeof(v));
    return v & 0xFFFFFFu;
}

static const FieldDef* findField(const char* key) {
    for (size_t i = 0; i < FIELD_COUNT; i++) {
        if (strcmp(FIELDS[i].key, key) == 0) return &FIELDS[i];
    }
    return nullptr;
}

static const char* typeName(FType t) {
    switch (t) {
        case FType::BOOL:   return "bool";
        case FType::UINT:   return "uint";
        case FType::FLOAT:  return "float";
        case FType::DOUBLE: return "double";
        case FType::STRING: return "string";
        case FType::COLOR:  return "color";
    }
    return "unknown";
}

// Keep the two public settings inside the developer-configured limits.
static void normalizeConfig(Config& c) {
    if (c.minAnonymizeMeters < 0.0f) c.minAnonymizeMeters = 0.0f;
    if (c.maxAnonymizeMeters > 100000.0f) c.maxAnonymizeMeters = 100000.0f;
    if (c.minAnonymizeMeters > c.maxAnonymizeMeters) {
        const float t = c.minAnonymizeMeters;
        c.minAnonymizeMeters = c.maxAnonymizeMeters;
        c.maxAnonymizeMeters = t;
    }
    if (c.anonymizeMeters < c.minAnonymizeMeters) c.anonymizeMeters = c.minAnonymizeMeters;
    if (c.anonymizeMeters > c.maxAnonymizeMeters) c.anonymizeMeters = c.maxAnonymizeMeters;

    if (c.maxBrightness > 255) c.maxBrightness = 255;
    if (c.ledBrightness > c.maxBrightness) c.ledBrightness = c.maxBrightness;

    if (c.startLedIndex >= NUM_LEDS) c.startLedIndex = 0;
    if (c.requestHours < 1) c.requestHours = 1;
    if (c.requestHours > 48) c.requestHours = 48;
    if (c.configModeTimeoutMs < 30000) c.configModeTimeoutMs = 30000;
}

static void configToJson(JsonObject o, const Config& c, bool publicOnly, bool includeSecret) {
    for (size_t i = 0; i < FIELD_COUNT; i++) {
        const FieldDef& f = FIELDS[i];
        if (publicOnly && !(f.flags & FF_PUBLIC)) continue;
        if ((f.flags & FF_SECRET) && !includeSecret) continue;

        switch (f.type) {
            case FType::BOOL:
                o[f.key] = readBool(c, f);
                break;
            case FType::UINT:
                o[f.key] = readUInt(c, f);
                break;
            case FType::FLOAT:
                o[f.key] = readFloat(c, f);
                break;
            case FType::DOUBLE:
                o[f.key] = readDouble(c, f);
                break;
            case FType::STRING:
                o[f.key] = readString(c, f);
                break;
            case FType::COLOR: {
                char buf[8];
                snprintf(buf, sizeof(buf), "#%06X", (unsigned)readColor(c, f));
                o[f.key] = buf;
                break;
            }
        }
    }
}

static void limitsToJson(JsonObject o, const Config& c) {
    o["anonymizeMin"]   = c.minAnonymizeMeters;
    o["anonymizeMax"]   = c.maxAnonymizeMeters;
    o["brightnessMax"]  = c.maxBrightness;
}

static bool setFieldFromJson(Config& c, const FieldDef& f, JsonVariantConst v, String& err) {
    uint8_t* dst = fieldBase(c, f);

    switch (f.type) {
        case FType::BOOL: {
            bool b;
            if (v.is<bool>())            b = v.as<bool>();
            else if (v.is<long long>())  b = v.as<long long>() != 0;
            else { err = "expected a boolean"; return false; }
            const uint8_t raw = b ? 1 : 0;
            memcpy(dst, &raw, f.size);
            return true;
        }
        case FType::UINT: {
            if (!v.is<long long>()) { err = "expected an integer"; return false; }
            const long long n = v.as<long long>();
            if (n < (long long)f.lo || n > (long long)f.hi) {
                char buf[64];
                snprintf(buf, sizeof(buf), "must be between %ld and %ld",
                         (long)f.lo, (long)f.hi);
                err = buf;
                return false;
            }
            if (f.size == 4) {
                const uint32_t x = (uint32_t)n;
                memcpy(dst, &x, 4);
            } else if (f.size == 2) {
                const uint16_t x = (uint16_t)n;
                memcpy(dst, &x, 2);
            } else {
                const uint8_t x = (uint8_t)n;
                memcpy(dst, &x, 1);
            }
            return true;
        }
        case FType::FLOAT:
        case FType::DOUBLE: {
            if (!(v.is<double>() || v.is<float>() || v.is<long long>() || v.is<unsigned long long>())) {
                err = "expected a number";
                return false;
            }
            const double d = v.as<double>();
            if (!isfinite(d) || d < f.lo || d > f.hi) {
                char buf[80];
                snprintf(buf, sizeof(buf), "must be between %.6g and %.6g", f.lo, f.hi);
                err = buf;
                return false;
            }
            if (f.type == FType::FLOAT) {
                const float x = (float)d;
                memcpy(dst, &x, sizeof(x));
            } else {
                memcpy(dst, &d, sizeof(d));
            }
            return true;
        }
        case FType::STRING: {
            if (!v.is<const char*>()) { err = "expected a string"; return false; }
            const char* s = v.as<const char*>();
            const size_t n = strlen(s);
            if (n > (size_t)f.size - 1) {
                char buf[64];
                snprintf(buf, sizeof(buf), "too long (max %u characters)", (unsigned)(f.size - 1));
                err = buf;
                return false;
            }
            memset(dst, 0, f.size);
            memcpy(dst, s, n);
            return true;
        }
        case FType::COLOR: {
            uint32_t rgb = 0;
            if (v.is<const char*>()) {
                const char* s = v.as<const char*>();
                if (s[0] == '#') s++;
                if (strlen(s) != 6) { err = "expected #RRGGBB"; return false; }
                char* end = nullptr;
                rgb = (uint32_t)strtoul(s, &end, 16);
                if (end == s || *end != '\0' || rgb > 0xFFFFFFu) {
                    err = "expected #RRGGBB";
                    return false;
                }
            } else if (v.is<long long>()) {
                const long long n = v.as<long long>();
                if (n < 0 || n > 0xFFFFFF) { err = "colour out of range"; return false; }
                rgb = (uint32_t)n;
            } else {
                err = "expected #RRGGBB";
                return false;
            }
            memcpy(dst, &rgb, sizeof(rgb));
            return true;
        }
    }
    err = "unsupported type";
    return false;
}

// Apply a JSON object of key/value pairs to a Config. Unknown keys and (in
// public mode) non-public keys are rejected and reported.
static void applyJsonToConfig(JsonObjectConst vals, Config& c, bool publicOnly, ApplyReport& rep) {
    for (JsonPairConst kv : vals) {
        const char* key = kv.key().c_str();
        const FieldDef* f = findField(key);
        if (!f) {
            rep.rejected++;
            if (rep.errors.length()) rep.errors += "; ";
            rep.errors += String("unknown setting '") + key + "'";
            continue;
        }
        if (publicOnly && !(f->flags & FF_PUBLIC)) {
            rep.rejected++;
            if (rep.errors.length()) rep.errors += "; ";
            rep.errors += String("'") + key + "' is developer-only";
            continue;
        }
        String err;
        if (!setFieldFromJson(c, *f, kv.value(), err)) {
            rep.rejected++;
            if (rep.errors.length()) rep.errors += "; ";
            rep.errors += String("'") + key + "': " + err;
            continue;
        }
        rep.applied++;
        if (f->flags & FF_REBOOT) rep.reboot = true;
    }
}

static void fieldDefaultToJson(JsonObject o, const FieldDef& f) {
    if (f.type == FType::STRING) {
        o["maxLength"] = (unsigned)(f.size - 1);
        if (!(f.flags & FF_SECRET)) o["default"] = readString(g_defaults, f);
        return;
    }
    if (f.flags & FF_SECRET) return;

    switch (f.type) {
        case FType::BOOL:   o["default"] = readBool(g_defaults, f);   break;
        case FType::UINT:   o["default"] = readUInt(g_defaults, f);   break;
        case FType::FLOAT:  o["default"] = readFloat(g_defaults, f);  break;
        case FType::DOUBLE: o["default"] = readDouble(g_defaults, f); break;
        case FType::COLOR: {
            char buf[8];
            snprintf(buf, sizeof(buf), "#%06X", (unsigned)readColor(g_defaults, f));
            o["default"] = buf;
            break;
        }
        case FType::STRING: break;
    }
}

static void schemaToJson(JsonObject root) {
    root["version"] = 1;
    root["device"]  = "air-ring";
    JsonArray arr = root["fields"].to<JsonArray>();
    for (size_t i = 0; i < FIELD_COUNT; i++) {
        const FieldDef& f = FIELDS[i];
        JsonObject o = arr.add<JsonObject>();
        o["key"]    = f.key;
        o["label"]  = f.label;
        o["group"]  = f.group;
        o["type"]   = typeName(f.type);
        o["public"] = (f.flags & FF_PUBLIC) != 0;
        o["secret"] = (f.flags & FF_SECRET) != 0;
        o["reboot"] = (f.flags & FF_REBOOT) != 0;
        o["help"]   = f.help;

        if (f.type == FType::UINT || f.type == FType::FLOAT || f.type == FType::DOUBLE) {
            o["min"]  = f.lo;
            o["max"]  = f.hi;
            o["step"] = f.step;
        }
        fieldDefaultToJson(o, f);
    }
}

// ---------------------------------------------------------------------------
// NVS persistence
// ---------------------------------------------------------------------------
static bool saveConfig() {
    JsonDocument doc;
    configToJson(doc.to<JsonObject>(), g_cfg, false, true);
    String out;
    serializeJson(doc, out);

    Preferences prefs;
    if (!prefs.begin(CFG_NAMESPACE, false)) {
        INFO("[CFG] save failed: cannot open NVS\n");
        return false;
    }
    const size_t written = prefs.putBytes(CFG_KEY, out.c_str(), out.length());
    prefs.end();
    if (written != out.length()) {
        INFO("[CFG] save failed (%u/%u bytes)\n", (unsigned)written, (unsigned)out.length());
        return false;
    }
    INFO("[CFG] saved %u bytes to NVS\n", (unsigned)written);
    return true;
}

static void loadConfig() {
    Config loaded;   // defaults

    Preferences prefs;
    if (prefs.begin(CFG_NAMESPACE, false)) {
        const size_t len = prefs.getBytesLength(CFG_KEY);
        if (len > 0 && len <= 16384) {
            uint8_t* buf = (uint8_t*)malloc(len + 1);
            if (buf) {
                const size_t got = prefs.getBytes(CFG_KEY, buf, len);
                buf[got] = '\0';
                JsonDocument doc;
                DeserializationError err = deserializeJson(doc, (const char*)buf, got);
                if (!err && doc.is<JsonObject>()) {
                    ApplyReport rep;
                    applyJsonToConfig(doc.as<JsonObjectConst>(), loaded, false, rep);
                    INFO("[CFG] loaded from NVS (%u applied, %u rejected)\n",
                         (unsigned)rep.applied, (unsigned)rep.rejected);
                    if (rep.errors.length()) INFO("[CFG] %s\n", rep.errors.c_str());
                } else {
                    INFO("[CFG] stored config invalid; using defaults\n");
                }
                free(buf);
            }
        }
        prefs.end();
    }

    normalizeConfig(loaded);
    g_cfg = loaded;
}

// Apply settings that FastLED consumes at runtime.
static void applyRuntimeConfig() {
    FastLED.setBrightness(g_cfg.ledBrightness);
    FastLED.setMaxPowerInVoltsAndMilliamps(g_cfg.ledVolts, g_cfg.ledMaxMa);
}

// ============================================================================
// PERSISTENT / GLOBAL STATE
// The demo never deep sleeps, so there is no RTC RAM state. The location cache
// is still persisted in NVS so a power cycle can recover the last fix.
// ============================================================================
bool     rtcHaveLocation = false;
double   rtcLastLat      = 0.0;
double   rtcLastLon      = 0.0;
uint32_t rtcLastFixEpoch = 0;   // UTC seconds of last real fix; 0 = unknown

constexpr const char* LOC_NAMESPACE = "airsense";
bool gButtonArmed  = false;     // true once the button has been seen released
bool gWifiStarted  = false;     // Wi-Fi driver is up
bool gGpsStarted   = false;     // GPS UART is up

// ============================================================================
// GLOBALS
// ============================================================================
CRGB           leds[NUM_LEDS];
TinyGPSPlus    gps;
HardwareSerial gpsSerial(1);

// LocSource / LocOutcome / AcquireResult are declared near the top so the
// Arduino IDE's generated prototypes can resolve them.

// ============================================================================
// SMALL HELPERS
// ============================================================================
// True once per press (rising edge), and only after the button has been seen
// released at least once, so a held button is not counted repeatedly.
bool buttonPressed() {
    if (digitalRead(BUTTON_PIN) == HIGH) {
        if (gButtonArmed) {
            gButtonArmed = false;
            return true;
        }
        return false;
    }
    gButtonArmed = true;
    return false;
}

const char* wifiStatusToString(wl_status_t status) {
    switch (status) {
        case WL_IDLE_STATUS:     return "IDLE";
        case WL_NO_SSID_AVAIL:   return "NO_SSID";
        case WL_SCAN_COMPLETED:  return "SCAN_DONE";
        case WL_CONNECTED:       return "CONNECTED";
        case WL_CONNECT_FAILED:  return "CONNECT_FAILED";
        case WL_CONNECTION_LOST: return "CONNECTION_LOST";
        case WL_DISCONNECTED:    return "DISCONNECTED";
        default:                 return "UNKNOWN";
    }
}

// TLS needs one large *contiguous* internal-RAM block for the handshake. Free
// heap alone can look healthy while fragmentation makes a handshake impossible,
// so gate on the largest block instead.
static uint32_t largestInternalBlock() {
    return (uint32_t)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
}

// Convert UTC date/time to Unix epoch seconds. Returns 0 if implausible
// (e.g. an unset GPS RTC reporting year 1980), which callers treat as "unknown".
uint32_t utcToEpoch(uint16_t year, uint8_t month, uint8_t day,
                    uint8_t hour, uint8_t minute, uint8_t second) {
    if (year < 2024 || year > 2100 || month < 1 || month > 12 || day < 1 || day > 31) {
        return 0;
    }
    // Howard Hinnant's days_from_civil
    int y = year;
    const int m = month;
    const int d = day;
    y -= (m <= 2);
    const long long era = (y >= 0 ? y : y - 399) / 400;
    const unsigned yoe  = static_cast<unsigned>(y - era * 400);
    const unsigned doy  = static_cast<unsigned>((153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1);
    const unsigned doe  = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    const long long days = era * 146097 + static_cast<long long>(doe) - 719468;
    return static_cast<uint32_t>(days * 86400LL + hour * 3600L + minute * 60L + second);
}

// Current GPS time as UTC epoch, or 0 if not (yet) trustworthy.
uint32_t currentGpsEpoch() {
    if (!gps.time.isValid() || !gps.date.isValid()) return 0;
    if (gps.time.age() > 5000 || gps.date.age() > 5000) return 0;   // stale / never updated
    return utcToEpoch(gps.date.year(), gps.date.month(), gps.date.day(),
                      gps.time.hour(), gps.time.minute(), gps.time.second());
}

bool isFreshFix() {
    return gps.location.isValid() && gps.location.age() < g_cfg.gpsMaxFixAgeMs;
}

void storeLocation(double lat, double lon) {
    rtcLastLat      = lat;
    rtcLastLon      = lon;
    rtcLastFixEpoch = currentGpsEpoch();   // 0 if time unknown; treated as stale next time
    rtcHaveLocation = true;

    // The demo can produce a fix every few seconds and the GPS stays warm. Only
    // write flash occasionally so repeated cycles do not wear the NVS partition.
    static bool     savedOnce  = false;
    static uint32_t lastSaveMs = 0;
    const bool shouldSave = !savedOnce || (millis() - lastSaveMs >= 60000UL);
    if (!shouldSave) return;
    savedOnce  = true;
    lastSaveMs = millis();

    Preferences prefs;
    if (prefs.begin(LOC_NAMESPACE, false)) {
        prefs.putBool("have", true);
        prefs.putDouble("lat", rtcLastLat);
        prefs.putDouble("lon", rtcLastLon);
        prefs.putULong("epoch", rtcLastFixEpoch);
        prefs.end();
        INFO("[GPS] cached fix at %.6f, %.6f (saved to flash)\n", lat, lon);
    } else {
        INFO("[GPS] cached fix at %.6f, %.6f (flash write FAILED)\n", lat, lon);
    }
}

void restoreOrResetCache() {
    // Load the last known location from flash.
    Preferences prefs;
    // Open read-write so the namespace is created on first boot (read-only open
    // of a missing namespace logs an E ... NOT_FOUND error).
    if (prefs.begin(LOC_NAMESPACE, false)) {
        rtcHaveLocation = prefs.getBool("have", false);
        rtcLastLat      = prefs.getDouble("lat", 0.0);
        rtcLastLon      = prefs.getDouble("lon", 0.0);
        rtcLastFixEpoch = prefs.getULong("epoch", 0);
        prefs.end();
    }
    if (!rtcHaveLocation) {
        rtcLastLat      = 0.0;
        rtcLastLon      = 0.0;
        rtcLastFixEpoch = 0;
    }
}

// ============================================================================
// GPS UART (the demo keeps the module powered continuously)
// ============================================================================
void startGps() {
    if (gGpsStarted) return;
    gpsSerial.begin(g_cfg.gpsBaud, SERIAL_8N1, GPS_RX_PIN, GPS_TX_PIN);
    // Wake a u-blox module that a previous firmware build left in backup mode.
    // A stray 0xFF is harmless to the NMEA parser.
    gpsSerial.write(0xFF);
    gpsSerial.flush();
    gGpsStarted = true;
    INFO("[GPS] UART started at %lu baud\n", static_cast<unsigned long>(g_cfg.gpsBaud));
}

void restartGps() {
    if (gGpsStarted) {
        gpsSerial.end();
        gGpsStarted = false;
    }
    startGps();
}

// Drain and parse whatever the GPS has sent. Called while idle so the parser
// (and therefore location age) stays current between button presses.
size_t pumpGps() {
    if (!gGpsStarted) return 0;
    size_t count = 0;
    while (gpsSerial.available() > 0) {
        const char c = static_cast<char>(gpsSerial.read());
        if (g_cfg.dumpRawNmea && g_cfg.debugMode) Serial.write(c);
        gps.encode(c);
        count++;
    }
    return count;
}

// ============================================================================
// COORDINATE ANONYMIZATION
// ============================================================================
void anonymizeCoordinates(double lat, double lon, double meters, double &outLat, double &outLon) {
    if (meters <= 0.0) {
        outLat = lat;
        outLon = lon;
        return;
    }
    constexpr double METERS_PER_DEG_LAT = 111320.0;
    const double dLat = meters / METERS_PER_DEG_LAT;

    double cosLat = cos(lat * DEG_TO_RAD);
    if (cosLat < 0.01) cosLat = 0.01;
    const double dLon = meters / (METERS_PER_DEG_LAT * cosLat);

    outLat = round(lat / dLat) * dLat;
    outLon = round(lon / dLon) * dLon;
}

// ============================================================================
// LED HELPERS
// ============================================================================
CRGB classToColor(int aqClass) {
    uint32_t rgb = 0;
    switch (aqClass) {
        case 1: rgb = g_cfg.colorClass1; break;
        case 2: rgb = g_cfg.colorClass2; break;
        case 3: rgb = g_cfg.colorClass3; break;
        case 4: rgb = g_cfg.colorClass4; break;
        case 5: rgb = g_cfg.colorClass5; break;
        default: return CRGB(0, 0, 0);
    }
    return CRGB((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF);
}

int getCircularLedIndex(uint16_t logicalIndex) {
    int32_t offset = g_cfg.reverseRing ? -static_cast<int32_t>(logicalIndex)
                                       : static_cast<int32_t>(logicalIndex);
    int32_t idx = (static_cast<int32_t>(g_cfg.startLedIndex) + offset) % static_cast<int32_t>(NUM_LEDS);
    if (idx < 0) idx += NUM_LEDS;
    return idx;
}

// Crossfade the whole ring from its current colours to `target`.
void crossfadeTo(const CRGB* target, uint16_t durationMs) {
    CRGB from[NUM_LEDS];
    memcpy(from, leds, sizeof(CRGB) * NUM_LEDS);

    const uint32_t start = millis();
    for (;;) {
        const uint32_t t = millis() - start;
        uint8_t f = 255;
        if (durationMs > 0 && t < durationMs) {
            f = static_cast<uint8_t>((t * 255UL) / durationMs);
        }
        for (uint16_t i = 0; i < NUM_LEDS; i++) {
            leds[i] = blend(from[i], target[i], f);
        }
        FastLED.show();
        if (t >= durationMs) break;
        delay(12);
    }
}

void showStatusLed(CRGB color, const char* reason) {
    INFO("[LED] status: %s\n", reason);
    if (!g_cfg.enableStatusLed) return;
    CRGB target[NUM_LEDS];
    fill_solid(target, NUM_LEDS, CRGB::Black);
    target[g_cfg.startLedIndex % NUM_LEDS] = color;
    crossfadeTo(target, g_cfg.fadeMs / 2);
}

void renderClassesToRing(const int* classes, size_t count) {
    CRGB target[NUM_LEDS];
    fill_solid(target, NUM_LEDS, CRGB::Black);

    if (count > 0 && NUM_LEDS > 0) {
        INFO("[LED] rendering %u hours -> %u LEDs (brightness %u)\n",
             static_cast<unsigned>(count), static_cast<unsigned>(NUM_LEDS),
             static_cast<unsigned>(g_cfg.ledBrightness));

        for (uint16_t logicalLed = 0; logicalLed < NUM_LEDS; logicalLed++) {
            uint32_t startIdx = (static_cast<uint32_t>(logicalLed) * count) / NUM_LEDS;
            uint32_t endIdx   = (static_cast<uint32_t>(logicalLed + 1) * count) / NUM_LEDS;
            if (endIdx <= startIdx) endIdx = startIdx + 1;
            if (endIdx > count)     endIdx = count;

            int resolvedClass = 0;
            if (g_cfg.downscaleWorstCase) {
                for (uint32_t h = startIdx; h < endIdx; h++) {
                    if (classes[h] >= 1 && classes[h] <= 5 && classes[h] > resolvedClass) {
                        resolvedClass = classes[h];
                    }
                }
            } else {
                int sum = 0, valid = 0;
                for (uint32_t h = startIdx; h < endIdx; h++) {
                    if (classes[h] >= 1 && classes[h] <= 5) { sum += classes[h]; valid++; }
                }
                if (valid > 0) resolvedClass = static_cast<int>(roundf(static_cast<float>(sum) / valid));
            }

            const CRGB color  = classToColor(resolvedClass);
            const int  physId = getCircularLedIndex(logicalLed);
            target[physId] = color;
            INFO("[LED] slot %02d <- hour %lu, class %d -> R%u G%u B%u\n",
                 physId,
                 static_cast<unsigned long>(startIdx),
                 resolvedClass,
                 static_cast<unsigned>(color.r),
                 static_cast<unsigned>(color.g),
                 static_cast<unsigned>(color.b));
        }
    }

    crossfadeTo(target, g_cfg.fadeMs);
}

// One-time boot sweep: a warm dot runs around the ring twice, then fades out.
void bootSweep() {
    if (!g_cfg.bootSweepEnabled) return;

    const uint16_t steps  = NUM_LEDS * 2;
    const uint16_t stepMs = (steps > 0) ? (g_cfg.bootSweepMs / steps) : 0;

    for (uint16_t s = 0; s < steps; s++) {
        fill_solid(leds, NUM_LEDS, CRGB::Black);
        for (int8_t tail = 0; tail < 3; tail++) {
            int idx = static_cast<int>(s % NUM_LEDS) - tail;
            if (idx < 0) idx += NUM_LEDS;
            CRGB c(255, 120, 0);
            c.nscale8(static_cast<uint8_t>(255 - tail * 80));
            leds[idx] = c;
        }
        FastLED.show();
        delay(stepMs);
    }

    CRGB black[NUM_LEDS];
    fill_solid(black, NUM_LEDS, CRGB::Black);
    crossfadeTo(black, 250);
}

// ============================================================================
// ============================================================================
// NETWORKING
// ============================================================================
void startWiFi() {
    if (gWifiStarted) return;
    gWifiStarted = true;
    WiFi.persistent(false);
    WiFi.mode(WIFI_STA);
    WiFi.setTxPower(WIFI_POWER_11dBm);
    WiFi.setAutoReconnect(true);
    DBG("[WiFi] connecting to '%s'\n", g_cfg.wifiSsid);
    WiFi.begin(g_cfg.wifiSsid, g_cfg.wifiPassword);
}

// ============================================================================
// LOCATION ACQUISITION (Wi-Fi and GPS polled concurrently)
// ============================================================================
// Decide how long to keep probing for a fix, based on the cached fix's age.
uint32_t decideGpsBudget(uint32_t elapsedMs) {
    if (!g_cfg.useLocationCache || !rtcHaveLocation) {
        INFO("[GPS] no cached fix; hunting up to %lus\n",
             static_cast<unsigned long>(g_cfg.gpsColdHuntMs / 1000));
        return g_cfg.gpsColdHuntMs;
    }

    const uint32_t now = currentGpsEpoch();
    if (now == 0 || rtcLastFixEpoch == 0) {
        INFO("[GPS] cached age unknown; trying a little longer\n");
        return g_cfg.gpsStaleRetryMs;
    }

    const uint32_t ageSec = (now > rtcLastFixEpoch) ? (now - rtcLastFixEpoch) : 0;
    const uint32_t validSec = g_cfg.locationValidHours * 3600UL;
    if (ageSec > validSec) {
        INFO("[GPS] cached fix %luh old; trying a little longer\n",
             static_cast<unsigned long>(ageSec / 3600));
        return g_cfg.gpsStaleRetryMs;
    }

    INFO("[GPS] no new fix; cached fix %lum old is still valid\n",
         static_cast<unsigned long>(ageSec / 60));
    return elapsedMs;   // stop probing now, use the cache
}

AcquireResult acquireWifiAndLocation() {
    AcquireResult res;
    const uint32_t startMs = millis();

    bool wifiReady         = false;
    bool wifiTimedOut      = false;
    bool haveFresh         = false;
    bool gpsSettled        = false;   // budget decision made
    bool gpsDone           = false;   // no more probing
    uint32_t gpsEndMs      = g_cfg.gpsQuickProbeMs;
    uint32_t uartBytes     = 0;

    for (;;) {
        const uint32_t elapsed = millis() - startMs;

        // ---- Wi-Fi ----
        if (!wifiReady && !wifiTimedOut) {
            wl_status_t st = WiFi.status();

            if (st == WL_CONNECTED) {
                wifiReady = true;
                INFO("[WiFi] connected in %lu ms (RSSI %d)\n",
                     static_cast<unsigned long>(elapsed), static_cast<int>(WiFi.RSSI()));
            } else if (elapsed >= g_cfg.wifiTimeoutMs) {
                wifiTimedOut = true;
                INFO("[WiFi] timeout (%s)\n", wifiStatusToString(st));
            }
        }

        // Wi-Fi failure is fatal for this cycle: stop and show red rather than
        // continuing to pulse as if we were still seeking.
        if (wifiTimedOut) break;

        // ---- GPS: always drain and try to catch a fresh fix ----
        uartBytes += pumpGps();

        if (!haveFresh && isFreshFix()) {
            haveFresh = true;
            res.loc.source = LocSource::FRESH;
            res.loc.lat    = gps.location.lat();
            res.loc.lon    = gps.location.lng();
            res.loc.ageSec   = 0;
            res.loc.ageKnown = true;
            storeLocation(res.loc.lat, res.loc.lon);
            INFO("[GPS] fix in %lu ms (%lu sats)\n",
                 static_cast<unsigned long>(elapsed),
                 static_cast<unsigned long>(gps.satellites.value()));
        }

        // A press while waiting means "stop waiting, use what we have".
        if (wifiReady && buttonPressed()) {
            INFO("[BTN] pressed during acquisition; using best available location\n");
            break;
        }

        // ---- Decide when to stop probing if still no fix ----
        if (!haveFresh && !gpsDone) {
            if (!gpsSettled) {
                // Wait for GPS time to arrive (one NMEA cycle) or the quick window to expire.
                if (currentGpsEpoch() != 0 || elapsed >= g_cfg.gpsQuickProbeMs) {
                    gpsSettled = true;
                    gpsEndMs   = decideGpsBudget(elapsed);
                }
            }
            if (gpsSettled && elapsed >= gpsEndMs) {
                gpsDone = true;
            }
        }

        if ((wifiReady || wifiTimedOut) && (haveFresh || gpsDone)) break;

        // Breathing blue while seeking (Wi-Fi or GPS) - the "working on it" cue.
        if (g_cfg.enableStatusLed) {
            const uint8_t pulse = beatsin8(40, 12, 90);
            leds[g_cfg.startLedIndex % NUM_LEDS] = CRGB(0, 0, pulse);
            FastLED.show();
        }

        if (g_cfg.debugMode && (elapsed % 1000) < 6) {
            DBG("[wait] %lu ms | wifi %s | uart %lu B | sats %lu\n",
                static_cast<unsigned long>(elapsed), wifiReady ? "OK" : "..",
                static_cast<unsigned long>(uartBytes),
                static_cast<unsigned long>(gps.satellites.value()));
        }
        delay(5);
    }

    res.wifiOk = wifiReady;

    // ---- Resolve the best available location ----
    if (haveFresh) {
        return res;
    }

    if (g_cfg.useLocationCache && rtcHaveLocation) {
        res.loc.source = LocSource::CACHED;
        res.loc.lat    = rtcLastLat;
        res.loc.lon    = rtcLastLon;
        const uint32_t now = currentGpsEpoch();
        res.loc.ageKnown = (now != 0 && rtcLastFixEpoch != 0 && now > rtcLastFixEpoch);
        res.loc.ageSec   = res.loc.ageKnown ? (now - rtcLastFixEpoch) : 0;
        return res;
    }

    if (g_cfg.useDefaultLocation) {
        res.loc.source = LocSource::FALLBACK;
        res.loc.lat    = g_cfg.defaultLat;
        res.loc.lon    = g_cfg.defaultLon;
        return res;
    }

    if (uartBytes == 0) {
        INFO("[GPS] no data on RX pin; check module power / TX wiring\n");
    } else if (gps.passedChecksum() == 0) {
        INFO("[GPS] data but no valid NMEA; check baud rate\n");
    } else {
        INFO("[GPS] valid NMEA but no fix (%lu sats in solution)\n",
             static_cast<unsigned long>(gps.satellites.value()));
    }
    res.loc.source = LocSource::NONE;
    return res;
}

// ============================================================================
// API FETCH & RENDER
// ============================================================================
bool fetchAndRenderAirQuality(double lat, double lon) {
    char fullUrl[320];
    snprintf(fullUrl, sizeof(fullUrl), "%s?lat=%.6f&lon=%.6f&hours=%u&include_current=%d",
             g_cfg.apiEndpoint, lat, lon,
             static_cast<unsigned>(g_cfg.requestHours), g_cfg.includeCurrent ? 1 : 0);

    const bool isHttps = (strncmp(g_cfg.apiEndpoint, "https://", 8) == 0);
    DBG("[HTTP] GET %s\n", fullUrl);

    // NimBLE is linked into the image (and reserves static RAM even though it
    // is never initialised outside setup mode). Refuse a TLS handshake that is
    // certain to fail rather than letting mbedTLS abort inside the stack.
    constexpr uint32_t MIN_TLS_BLOCK = 45000;
    INFO("[HTTP] heap %lu B, largest internal block %lu B\n",
         (unsigned long)ESP.getFreeHeap(), (unsigned long)largestInternalBlock());
    if (isHttps && largestInternalBlock() < MIN_TLS_BLOCK) {
        INFO("[HTTP] not enough contiguous RAM for TLS (need ~%lu B); skipping fetch\n",
             (unsigned long)MIN_TLS_BLOCK);
        return false;
    }

    HTTPClient http;
    http.setTimeout(g_cfg.httpTimeoutMs);

    WiFiClient plainClient;
    WiFiClientSecure secureClient;

    bool begun = false;
    if (isHttps) {
        if (g_cfg.tlsInsecure) secureClient.setInsecure();
        else                   secureClient.setCACert(g_cfg.rootCaCert);
        // Keep the handshake from blocking for minutes on a weak link.
        // (Core 3.x/4.x no longer expose the old setBufferSizes() knob, so the
        // heap gate above is the primary protection on the C3.)
        secureClient.setHandshakeTimeout(15);
        begun = http.begin(secureClient, fullUrl);
    } else {
        begun = http.begin(plainClient, fullUrl);
    }

    if (!begun) {
        INFO("[HTTP] begin() failed\n");
        return false;
    }

    const uint32_t reqStart = millis();
    const int httpCode = http.GET();
    INFO("[HTTP] %d in %lu ms\n", httpCode, static_cast<unsigned long>(millis() - reqStart));

    if (httpCode <= 0 || httpCode >= 400) {
        if (httpCode < 0) INFO("[HTTP] network/TLS failure\n");
        else              INFO("[HTTP] server error: %s\n", http.getString().c_str());
        http.end();
        return false;
    }

    String payload = http.getString();
    http.end();
    INFO("[API] body: %s\n", payload.c_str());

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, payload);
    if (err) {
        INFO("[HTTP] JSON error: %s\n", err.c_str());
        return false;
    }
    if (!doc.is<JsonArray>()) {
        INFO("[HTTP] unexpected JSON shape\n");
        return false;
    }

    JsonArray arr = doc.as<JsonArray>();
    const size_t count = arr.size();
    if (count == 0) {
        INFO("[HTTP] empty array\n");
        return false;
    }

    int* classes = new int[count];
    if (!classes) {
        INFO("[HTTP] out of memory\n");
        return false;
    }

    size_t i = 0;
    for (JsonVariant v : arr) {
        if (v.isNull()) {
            classes[i] = 0;
        } else {
            const int c = v.as<int>();
            classes[i] = (c >= 1 && c <= 5) ? c : 0;
        }
        i++;
    }

    renderClassesToRing(classes, count);
    delete[] classes;
    return true;
}

// ============================================================================
// RADIO MANAGEMENT
// ============================================================================
// The demo never deep sleeps. Setup mode shuts Wi-Fi down to keep memory free
// for BLE; the next normal cycle starts it again lazily.
void stopWiFi() {
    if (!gWifiStarted) return;
    WiFi.disconnect(true);
    WiFi.mode(WIFI_OFF);
    gWifiStarted = false;
    INFO("[WiFi] stopped\n");
}

// ============================================================================
// BLE CONFIGURATION SERVICE
//
// MEMORY MODEL (important on the ESP32-C3):
//   * NimBLE is used instead of Bluedroid because its static footprint is about
//     53 KB of RAM versus roughly 180 KB. Even so, those static buffers are
//     reserved as soon as the image boots, whether or not BLE is initialised.
//   * The BLE stack is initialised when setup mode starts and deinitialised when
//     it ends, freeing its dynamic allocations before the next HTTPS fetch.
//     Setup mode also shuts Wi-Fi down first, so BLE and TLS never overlap.
//   * Normal fetch/display cycles never touch the BLE stack.
//   * The HTTPS path additionally refuses to start when the largest contiguous
//     internal-RAM block is below ~45 KB, and uses a 15 s handshake timeout. A
//     failed fetch becomes a red status LED, not a crash.
//   * JSON documents for schema/config transfer are transient and only exist
//     in setup mode, where the TLS clients are already gone.
//
// GATT layout:
//   5f9b1000-... : config service
//   5f9b1001-... : RX  (write)   chunked request frames
//   5f9b1002-... : TX  (notify)  chunked response frames
//
// Every frame is [flags][payload...] where flags bit0 = first chunk and bit1 =
// last chunk. The payload is UTF-8 JSON. Chunking keeps every ATT packet well
// inside the negotiated MTU; Web Bluetooth also caps characteristic writes.
//
// Protocol (JSON request -> JSON response, an optional "id" is echoed):
//   {"op":"hello"}                              device information
//   {"op":"getPublic"}                          public settings + limits
//   {"op":"setPublic","values":{...}}           apply public settings
//   {"op":"auth","pin":"..."}                   unlock developer mode
//   {"op":"getSchema"}                          developer schema (auth)
//   {"op":"getDev"}                             all settings (auth)
//   {"op":"setDev","values":{...}}              apply any settings (auth)
//   {"op":"exit"}                               leave setup mode (no auth)
//   {"op":"sleep"}                              alias for exit (no auth)
//   {"op":"factoryReset"}                       restore defaults, stay running (auth)
// ============================================================================
#define CFG_SERVICE_UUID "5f9b1000-7a1e-4c3b-9b1d-1e2f3a4b5c6d"
#define CFG_RX_UUID      "5f9b1001-7a1e-4c3b-9b1d-1e2f3a4b5c6d"
#define CFG_TX_UUID      "5f9b1002-7a1e-4c3b-9b1d-1e2f3a4b5c6d"

static const size_t CFG_CHUNK     = 180;    // bytes of payload per BLE frame
static const size_t CFG_MAX_MSG   = 12288;  // maximum reassembled message
static const int    CFG_MAX_TRIES = 5;

static NimBLECharacteristic* gTxChar      = nullptr;
static NimBLEServer*         gServer      = nullptr;
static QueueHandle_t         gReqQueue    = nullptr;
static String                gRxFrame;            // BLE task only
static char                  gAdvName[40] = "AirRing";
static volatile bool         gBleConnected = false;
static volatile bool         gBleStarted   = false;
static volatile uint16_t     gConnHandle   = 0xFFFF;
static volatile bool         gAuthOk       = false;
static volatile int          gAuthAttempts = 0;
static volatile bool         gExitSetup    = false;
static volatile bool         gWantDisconnect = false;
static volatile bool         gRestartAdvertising = false;
static volatile uint16_t     gNegotiatedMtu = 23;
static uint32_t              gLastActivityMs = 0;

// Advertising API differs between the legacy and Bluetooth-5 extended builds.
// ESP32 core 4.x enables CONFIG_BT_NIMBLE_EXT_ADV on the C3.
static bool restartAdvertising() {
#if CONFIG_BT_NIMBLE_EXT_ADV
    return NimBLEDevice::startAdvertising(0);
#else
    return NimBLEDevice::startAdvertising();
#endif
}

static bool pinMatches(const char* pin) {
    const size_t n = strlen(DEV_PIN);
    if (strlen(pin) != n) return false;
    uint8_t diff = 0;
    for (size_t i = 0; i < n; i++) diff |= (uint8_t)(pin[i] ^ DEV_PIN[i]);
    return diff == 0;
}

static void sendFrame(const String& payload) {
    if (!gTxChar || !gBleConnected) return;

    size_t off = 0;
    // A notification payload is limited to ATT_MTU-3, minus our flag byte.
    // Chrome on Android normally negotiates 247+, but fall back gracefully if
    // an MTU exchange never happens.
    const uint16_t mtu = gNegotiatedMtu ? gNegotiatedMtu : 23;
    size_t maxPayload = (mtu > 4) ? (size_t)(mtu - 4) : 19;
    if (maxPayload > CFG_CHUNK) maxPayload = CFG_CHUNK;

    do {
        const size_t remaining = payload.length() - off;
        const size_t n = remaining < maxPayload ? remaining : maxPayload;
        uint8_t buf[CFG_CHUNK + 1];
        buf[0] = (uint8_t)((off == 0 ? 0x01 : 0x00) |
                           (off + n == payload.length() ? 0x02 : 0x00));
        memcpy(buf + 1, payload.c_str() + off, n);
        gTxChar->setValue(buf, n + 1);

        // Notifications can be refused while the stack's TX queue is full.
        // Retry briefly, then pace chunks so Android's connection interval can
        // drain them. A lost chunk would corrupt the JSON transfer.
        uint8_t attempts = 0;
        while (!gTxChar->notify() && attempts < 4) {
            delay(10);
            attempts++;
        }
        off += n;
        delay(10);
    } while (off < payload.length());
}

static void sendResponse(JsonDocument& res) {
    String out;
    serializeJson(res, out);
    DBG("[BLE] tx %s\n", out.c_str());
    sendFrame(out);
}

class CfgServerCallbacks : public NimBLEServerCallbacks {
    void onConnect(NimBLEServer* server, NimBLEConnInfo& info) override {
        (void)server;
        gBleConnected  = true;
        gConnHandle    = info.getConnHandle();
        gNegotiatedMtu = info.getMTU();
        gAuthOk        = false;
        gAuthAttempts  = 0;
        gLastActivityMs = millis();
        gRxFrame       = "";
        INFO("[BLE] client connected (handle %u)\n", (unsigned)gConnHandle);
    }

    void onDisconnect(NimBLEServer* server, NimBLEConnInfo& info, int reason) override {
        (void)server;
        (void)info;
        gBleConnected = false;
        gConnHandle   = 0xFFFF;
        gAuthOk       = false;
        gRxFrame      = "";
        INFO("[BLE] client disconnected (reason %d); advertising again\n", reason);
        // Restart from the main loop: the advertising instance is still being
        // torn down while this callback runs.
        gRestartAdvertising = true;
    }

    void onMTUChange(uint16_t mtu, NimBLEConnInfo& info) override {
        (void)info;
        gNegotiatedMtu = mtu;
        INFO("[BLE] MTU is now %u\n", (unsigned)mtu);
    }
};

class CfgRxCallbacks : public NimBLECharacteristicCallbacks {
    void onWrite(NimBLECharacteristic* ch, NimBLEConnInfo& info) override {
        (void)info;
        auto value = ch->getValue();
        if (value.size() < 1) return;

        const uint8_t* data  = value.data();
        const uint8_t  flags = data[0];
        const size_t   n     = value.size() - 1;

        if (flags & 0x01) gRxFrame = "";
        if (gRxFrame.length() + n > CFG_MAX_MSG) {
            INFO("[BLE] request too large; dropped\n");
            gRxFrame = "";
            return;
        }
        gRxFrame.concat(reinterpret_cast<const char*>(data + 1), n);

        if (flags & 0x02) {
            String* msg = new String(gRxFrame);
            gRxFrame = "";
            if (!msg) return;
            if (xQueueSend(gReqQueue, &msg, 0) != pdTRUE) {
                INFO("[BLE] request queue full; dropped\n");
                delete msg;
            }
        }
    }
};

static bool startBle() {
    if (gBleStarted) return true;

    snprintf(gAdvName, sizeof(gAdvName), "%s-%04X",
             g_cfg.bleName, (unsigned)(ESP.getEfuseMac() & 0xFFFF));

    // NOTE: do not call NimBLEDevice::setMTU() before init(); it touches the
    // host mutex, which only exists after NimBLEDevice::init(). NimBLE's
    // default preferred MTU is 255, which is all we need. sendFrame() adapts
    // its chunk size to whatever MTU is actually negotiated.
    if (!NimBLEDevice::init(gAdvName)) {
        INFO("[BLE] NimBLE init failed\n");
        return false;
    }

    // Keep the request queue for the life of the firmware. It is drained when
    // the stack is torn down and reused on the next setup session, so callbacks
    // can never race a queue deletion.
    if (!gReqQueue) {
        gReqQueue = xQueueCreate(6, sizeof(String*));
    }
    if (!gReqQueue) {
        INFO("[BLE] failed to allocate request queue\n");
        NimBLEDevice::deinit(true);
        return false;
    }

    gServer = NimBLEDevice::createServer();
    if (!gServer) {
        INFO("[BLE] failed to create server\n");
        NimBLEDevice::deinit(true);
        return false;
    }
    gServer->setCallbacks(new CfgServerCallbacks());

    NimBLEService* service = gServer->createService(CFG_SERVICE_UUID);
    if (!service) {
        INFO("[BLE] failed to create service\n");
        gServer = nullptr;
        NimBLEDevice::deinit(true);
        return false;
    }

    NimBLECharacteristic* rx = service->createCharacteristic(
        CFG_RX_UUID, NIMBLE_PROPERTY::WRITE);
    rx->setCallbacks(new CfgRxCallbacks());

    gTxChar = service->createCharacteristic(CFG_TX_UUID, NIMBLE_PROPERTY::NOTIFY);

    service->start();

#if CONFIG_BT_NIMBLE_EXT_ADV
    // Core 4.x enables Bluetooth-5 extended advertising for the C3. A
    // connectable extended advertisement cannot also be scannable, so the name
    // and service UUID go in the same advertisement (comfortably < 251 bytes).
    NimBLEExtAdvertising* adv = NimBLEDevice::getAdvertising();
    NimBLEExtAdvertisement data;
    data.setConnectable(true);
    data.setName(gAdvName);
    data.addServiceUUID(CFG_SERVICE_UUID);
    if (!adv->setInstanceData(0, data)) {
        INFO("[BLE] failed to register advertising data\n");
    }
    if (!adv->start(0)) {
        INFO("[BLE] failed to start advertising\n");
    }
#else
    NimBLEAdvertising* adv = NimBLEDevice::getAdvertising();
    // Enable scan response FIRST: NimBLE puts the name in the scan response
    // when possible, leaving room for the 128-bit service UUID in the 31-byte
    // advertisement. Web Bluetooth filters on that advertised UUID.
    adv->enableScanResponse(true);
    adv->setName(gAdvName);
    adv->addServiceUUID(CFG_SERVICE_UUID);
    adv->start();
#endif

    gBleStarted = true;
    INFO("[BLE] advertising as '%s' (heap %lu B, largest block %lu B)\n",
         gAdvName, (unsigned long)ESP.getFreeHeap(),
         (unsigned long)largestInternalBlock());
    return true;
}

// Tear the BLE stack down when leaving setup mode. This frees NimBLE's dynamic
// allocations before the next HTTPS fetch. (Its static RAM is linked in either
// way, which is why the TLS heap gate still matters.)
static void stopBle() {
    if (!gBleStarted) return;

    NimBLEDevice::stopAdvertising();

    if (gBleConnected && gServer) {
        gServer->disconnect(gConnHandle);
        delay(200);
    }

    gTxChar  = nullptr;
    gServer  = nullptr;
    gBleConnected = false;
    gConnHandle = 0xFFFF;
    gAuthOk = false;
    gRxFrame = "";

    // Stop the host, then discard any queued requests. The queue itself is
    // kept for the next setup session (see startBle()).
    if (!NimBLEDevice::deinit(true)) {
        INFO("[BLE] deinit reported an error\n");
    }
    if (gReqQueue) {
        String* pending = nullptr;
        while (xQueueReceive(gReqQueue, &pending, 0) == pdTRUE) {
            delete pending;
        }
    }

    gBleStarted = false;
    gNegotiatedMtu = 23;
    gRestartAdvertising = false;
    INFO("[BLE] stack stopped (heap %lu B, largest block %lu B)\n",
         (unsigned long)ESP.getFreeHeap(), (unsigned long)largestInternalBlock());
}

static void handleRequest(const String& raw) {
    DBG("[BLE] rx %s\n", raw.c_str());

    JsonDocument req;
    JsonDocument res;
    JsonObject   out = res.to<JsonObject>();

    DeserializationError err = deserializeJson(req, raw);
    if (req["id"].is<int>()) out["id"] = req["id"].as<int>();

    if (err) {
        out["ok"]    = false;
        out["error"] = "bad_json";
        sendResponse(res);
        return;
    }

    const char* op = req["op"] | "";

    auto error = [&](const char* code) {
        out["ok"]    = false;
        out["error"] = code;
        sendResponse(res);
    };

    if (!strcmp(op, "hello") || !strcmp(op, "info")) {
        out["ok"]            = true;
        out["device"]        = gAdvName;
        out["fw"]            = FW_VERSION;
        out["configMode"]    = true;
        out["authenticated"] = (bool)gAuthOk;

        char mac[18];
        const uint64_t m = ESP.getEfuseMac();
        snprintf(mac, sizeof(mac), "%02X:%02X:%02X:%02X:%02X:%02X",
                 (unsigned)(m & 0xFF), (unsigned)((m >> 8) & 0xFF),
                 (unsigned)((m >> 16) & 0xFF), (unsigned)((m >> 24) & 0xFF),
                 (unsigned)((m >> 32) & 0xFF), (unsigned)((m >> 40) & 0xFF));
        out["mac"] = mac;

        JsonObject lim = out["limits"].to<JsonObject>();
        limitsToJson(lim, g_cfg);

        JsonArray ops = out["ops"].to<JsonArray>();
        ops.add("getPublic");
        ops.add("setPublic");
        ops.add("auth");
        ops.add("getSchema");
        ops.add("getDev");
        ops.add("setDev");
        ops.add("exit");
        ops.add("factoryReset");

        sendResponse(res);
        return;
    }

    if (!strcmp(op, "getPublic")) {
        out["ok"] = true;
        JsonObject cfg = out["config"].to<JsonObject>();
        configToJson(cfg, g_cfg, true, false);
        JsonObject lim = out["limits"].to<JsonObject>();
        limitsToJson(lim, g_cfg);
        sendResponse(res);
        return;
    }

    if (!strcmp(op, "setPublic")) {
        if (!req["values"].is<JsonObjectConst>()) { error("missing_values"); return; }

        const float   prevAnon   = g_cfg.anonymizeMeters;
        const uint8_t prevBright = g_cfg.ledBrightness;

        ApplyReport rep;
        applyJsonToConfig(req["values"].as<JsonObjectConst>(), g_cfg, true, rep);
        normalizeConfig(g_cfg);
        applyRuntimeConfig();

        // Avoid an NVS write when a client re-sends identical values.
        const bool changed = (g_cfg.anonymizeMeters != prevAnon) ||
                             (g_cfg.ledBrightness != prevBright);
        const bool saved = changed ? saveConfig() : true;

        out["ok"]       = (rep.rejected == 0);
        out["applied"]  = rep.applied;
        out["rejected"] = rep.rejected;
        out["saved"]    = saved;
        out["changed"]  = changed;
        if (rep.errors.length()) out["errors"] = rep.errors;

        JsonObject cfg = out["config"].to<JsonObject>();
        configToJson(cfg, g_cfg, true, false);
        JsonObject lim = out["limits"].to<JsonObject>();
        limitsToJson(lim, g_cfg);

        INFO("[CFG] public write: %d applied, %d rejected\n", rep.applied, rep.rejected);
        sendResponse(res);
        return;
    }

    if (!strcmp(op, "auth")) {
        if (!req["pin"].is<const char*>()) { error("missing_pin"); return; }

        if (pinMatches(req["pin"].as<const char*>())) {
            gAuthOk       = true;
            gAuthAttempts = 0;
            out["ok"]            = true;
            out["authenticated"] = true;
            INFO("[CFG] developer unlocked\n");
        } else {
            gAuthAttempts++;
            out["ok"]            = false;
            out["error"]         = "bad_pin";
            out["authenticated"] = false;
            out["attemptsLeft"]  = CFG_MAX_TRIES - gAuthAttempts;
            INFO("[CFG] bad developer PIN (%d/%d)\n", gAuthAttempts, CFG_MAX_TRIES);
            delay(350);   // slow down brute force a little
            if (gAuthAttempts >= CFG_MAX_TRIES) {
                out["disconnect"] = true;
                gWantDisconnect = true;
            }
        }
        sendResponse(res);
        return;
    }

    if (!strcmp(op, "getSchema") || !strcmp(op, "getDev") || !strcmp(op, "setDev")) {
        if (!gAuthOk) { error("auth_required"); return; }
    }

    if (!strcmp(op, "getSchema")) {
        // The schema document plus its serialised copy is the largest transfer
        // this firmware makes. Decline early instead of failing mid-allocation.
        if (ESP.getFreeHeap() < 60000) { error("low_memory"); return; }
        out["ok"] = true;
        JsonObject schema = out["schema"].to<JsonObject>();
        schemaToJson(schema);
        sendResponse(res);
        return;
    }

    if (!strcmp(op, "getDev")) {
        if (ESP.getFreeHeap() < 40000) { error("low_memory"); return; }
        out["ok"] = true;
        JsonObject cfg = out["config"].to<JsonObject>();
        configToJson(cfg, g_cfg, false, false);
        JsonObject lim = out["limits"].to<JsonObject>();
        limitsToJson(lim, g_cfg);
        sendResponse(res);
        return;
    }

    if (!strcmp(op, "setDev")) {
        if (!req["values"].is<JsonObjectConst>()) { error("missing_values"); return; }
        if (ESP.getFreeHeap() < 40000) { error("low_memory"); return; }

        const uint32_t prevBaud = g_cfg.gpsBaud;

        ApplyReport rep;
        applyJsonToConfig(req["values"].as<JsonObjectConst>(), g_cfg, false, rep);
        normalizeConfig(g_cfg);
        applyRuntimeConfig();
        if (g_cfg.gpsBaud != prevBaud && gGpsStarted) restartGps();
        const bool saved = saveConfig();

        out["ok"]       = (rep.rejected == 0);
        out["applied"]  = rep.applied;
        out["rejected"] = rep.rejected;
        out["saved"]    = saved;
        out["reboot"]   = rep.reboot;
        if (rep.errors.length()) out["errors"] = rep.errors;

        JsonObject cfg = out["config"].to<JsonObject>();
        configToJson(cfg, g_cfg, false, false);
        JsonObject lim = out["limits"].to<JsonObject>();
        limitsToJson(lim, g_cfg);

        INFO("[CFG] developer write: %d applied, %d rejected\n", rep.applied, rep.rejected);
        sendResponse(res);
        return;
    }

    if (!strcmp(op, "exit") || !strcmp(op, "sleep")) {
        // Not auth-gated: leaving setup mode is harmless, and it lets a public
        // user hand the ring back to the demo without the developer PIN.
        out["ok"] = true;
        sendResponse(res);
        gExitSetup = true;
        return;
    }

    if (!strcmp(op, "reboot")) {
        // The demo firmware never restarts the ESP.
        error("unsupported");
        return;
    }

    if (!strcmp(op, "factoryReset")) {
        if (!gAuthOk) { error("auth_required"); return; }
        bool wiped = false;
        Preferences prefs;
        if (prefs.begin(CFG_NAMESPACE, false)) {
            wiped = prefs.clear();
            prefs.end();
        }
        if (!wiped) { error("nvs_erase_failed"); return; }

        // Apply defaults in place; no reboot, no deep sleep.
        g_cfg = g_defaults;
        normalizeConfig(g_cfg);
        applyRuntimeConfig();
        if (gGpsStarted) restartGps();
        const bool saved = saveConfig();

        out["ok"]    = true;
        out["saved"] = saved;
        JsonObject cfg = out["config"].to<JsonObject>();
        configToJson(cfg, g_cfg, false, false);
        JsonObject lim = out["limits"].to<JsonObject>();
        limitsToJson(lim, g_cfg);

        INFO("[CFG] factory reset applied (defaults saved: %s)\n", saved ? "yes" : "no");
        sendResponse(res);
        return;
    }

    error("unknown_op");
}

// Draw a live preview of the ring while in setup mode. The palette shows the
// current brightness and the configured class colours; the first pixel shows
// connection state (green = connected, blue pulse = advertising).
static void drawConfigPreview() {
    CRGB target[NUM_LEDS];
    fill_solid(target, NUM_LEDS, CRGB::Black);

    for (uint16_t i = 0; i < NUM_LEDS; i++) {
        target[getCircularLedIndex(i)] = classToColor((i % 5) + 1);
    }

    const int statusIdx = getCircularLedIndex(0);
    if (gBleConnected) {
        target[statusIdx] = CRGB(0, 255, 0);
    } else {
        target[statusIdx] = CRGB(0, 0, beatsin8(30, 20, 150));
    }

    FastLED.show();
}

// Runs until the app leaves setup mode or the idle timeout expires, then
// returns to the demo loop. Setup mode no longer sleeps or resets the ESP.
static void runConfigMode() {
    INFO("[CFG] entering setup mode (hold button %.1fs while displaying to re-enter)\n",
         CONFIG_HOLD_MS / 1000.0f);

    // Free Wi-Fi's memory for BLE. The GPS UART is left running so the fix
    // stays warm; pumpGps() below keeps parsing it.
    stopWiFi();

    applyRuntimeConfig();
    FastLED.clear(true);

    INFO("[CFG] heap before BLE: %lu B (largest block %lu B)\n",
         (unsigned long)ESP.getFreeHeap(), (unsigned long)largestInternalBlock());

    if (!startBle()) {
        INFO("[CFG] BLE unavailable; returning to demo mode\n");
        showStatusLed(CRGB::Red, "BLE init failed");
        delay(1500);
        FastLED.clear(true);
        return;
    }

    gExitSetup         = false;
    gWantDisconnect    = false;
    gRestartAdvertising = false;
    gLastActivityMs    = millis();
    drawConfigPreview();

    for (;;) {
        String* msg = nullptr;
        if (xQueueReceive(gReqQueue, &msg, pdMS_TO_TICKS(50)) == pdTRUE) {
            if (msg) {
                gLastActivityMs = millis();
                handleRequest(*msg);
                delete msg;
            }
        }

        if (gWantDisconnect) {
            gWantDisconnect = false;
            delay(250);   // let the final notification go out
            if (gBleConnected) {
                gServer->disconnect(gConnHandle);
                INFO("[CFG] too many bad PIN attempts; disconnecting\n");
            }
        }

        if (gExitSetup) {
            INFO("[CFG] leaving setup mode on request\n");
            break;
        }

        if (gRestartAdvertising) {
            gRestartAdvertising = false;
            const bool ok = restartAdvertising();
            INFO("[BLE] advertising restart %s\n", ok ? "ok" : "FAILED");
        }

        if (!gBleConnected &&
            (millis() - gLastActivityMs) >= g_cfg.configModeTimeoutMs) {
            INFO("[CFG] setup mode idle timeout; returning to demo mode\n");
            break;
        }

        drawConfigPreview();
        pumpGps();
        delay(20);
    }

    stopBle();
    FastLED.clear(true);
}

// ============================================================================
// ONE RUN CYCLE
// ============================================================================
// Wait for `durationMs`, but return early if the button is held long enough to
// request setup mode. If the display window ends while the button is still
// held, keep waiting until the long-press threshold (or a release).
bool waitForDisplayOrConfig(uint32_t durationMs) {
    const uint32_t start = millis();
    int last = digitalRead(BUTTON_PIN);
    uint32_t highSince = (last == HIGH) ? start : 0;

    for (;;) {
        const uint32_t now = millis();
        const int level = digitalRead(BUTTON_PIN);

        if (level == HIGH && last != HIGH) highSince = now;
        if (level == HIGH && (now - highSince) >= CONFIG_HOLD_MS) {
            return true;
        }

        if ((now - start) >= durationMs && level == LOW) {
            return false;
        }

        // Keep the GPS parser current during the display window too.
        pumpGps();
        last = level;
        delay(20);
    }
}

// Bring up whichever radios a normal cycle needs. Lazy so a setup session can
// stop Wi-Fi and hand back without special-casing the caller.
void ensureRadios() {
    startWiFi();
    startGps();
}

void runOneCycle() {
    ensureRadios();

    const AcquireResult r = acquireWifiAndLocation();

    bool configRequested = false;

    if (!r.wifiOk) {
        showStatusLed(CRGB::Red, "Wi-Fi connection failed");
        configRequested = waitForDisplayOrConfig(1500);
    } else if (r.loc.source == LocSource::NONE) {
        // The silent hunt already ran; nothing to show, retry on the next press.
        INFO("[SYS] no location available; LEDs off\n");
    } else {
        switch (r.loc.source) {
            case LocSource::FRESH:   INFO("[SYS] location: fresh fix\n");            break;
            case LocSource::CACHED:
                if (r.loc.ageKnown)
                    INFO("[SYS] location: cached (%lum old)\n",
                         static_cast<unsigned long>(r.loc.ageSec / 60));
                else
                    INFO("[SYS] location: cached (age unknown)\n");
                break;
            case LocSource::FALLBACK: INFO("[SYS] location: default (demo)\n");      break;
            default: break;
        }

        double anonLat = 0.0, anonLon = 0.0;
        anonymizeCoordinates(r.loc.lat, r.loc.lon, g_cfg.anonymizeMeters, anonLat, anonLon);

        if (fetchAndRenderAirQuality(anonLat, anonLon)) {
            configRequested = waitForDisplayOrConfig(g_cfg.displayDurationMs);
        } else {
            showStatusLed(CRGB::Red, "API fetch failed");
            configRequested = waitForDisplayOrConfig(1500);
        }
    }

    FastLED.clear(true);

    if (configRequested) {
        INFO("[BTN] long press -> setup mode\n");
        runConfigMode();
        FastLED.clear(true);
    }
}

// ============================================================================
// SETUP / DEMO LOOP
// ============================================================================
void setup() {
    // Hold the strip's data line low immediately so boot noise does not latch
    // into the LEDs. (The C3 ROM prints its boot log on UART0 TX = GPIO21, which
    // is also LED_DATA_PIN; that is the source of the white flash on reset.)
    pinMode(LED_DATA_PIN, OUTPUT);
    digitalWrite(LED_DATA_PIN, LOW);

    pinMode(BUTTON_PIN, INPUT_PULLDOWN);

    Serial.begin(115200);

    // Configuration must be loaded before anything reads a runtime setting.
    loadConfig();

    // FastLED first, so the strip is under our control from the earliest moment.
    FastLED.addLeds<LED_TYPE, LED_DATA_PIN, COLOR_ORDER>(leds, NUM_LEDS)
           .setCorrection(TypicalLEDStrip);
    applyRuntimeConfig();
    FastLED.clear();
    FastLED.show();

    if (g_cfg.serialWaitOnBoot) {
        const uint32_t waitStart = millis();
        while (!Serial && (millis() - waitStart < g_cfg.serialWaitMaxMs)) delay(10);
    }

    restoreOrResetCache();

    // Start the GPS UART early so the receiver can warm up even while the user
    // is sitting in setup mode. Wi-Fi is started lazily when a cycle runs.
    startGps();

    // Verify every LED once per power-up.
    bootSweep();

    // A button held at boot enters setup mode. No deep sleep: after setup mode
    // ends, the demo carries on normally.
    if (digitalRead(BUTTON_PIN) == HIGH) {
        INFO("[SYS] GPIO%u held at boot - entering setup mode\n",
             static_cast<unsigned>(BUTTON_PIN));
        runConfigMode();
        FastLED.clear(true);

        // Don't let the same hold count as a cycle trigger / long-press.
        const uint32_t releaseStart = millis();
        while (digitalRead(BUTTON_PIN) == HIGH && (millis() - releaseStart) < 3000) delay(10);
    }

    INFO("[BOOT] heap %lu B | largest block %lu B | %s | %s\n",
         static_cast<unsigned long>(ESP.getFreeHeap()),
         static_cast<unsigned long>(largestInternalBlock()),
         rtcHaveLocation ? "location cached" : "no cached location",
         gWifiStarted ? "wifi up" : "wifi off");
    if (rtcHaveLocation) {
        DBG("[BOOT] cached fix: %.6f, %.6f (epoch %lu)\n",
            rtcLastLat, rtcLastLon, static_cast<unsigned long>(rtcLastFixEpoch));
    }

    // First forecast at power-up; loop() handles subsequent button presses.
    runOneCycle();
}

void loop() {
    // Keep the GPS parser (and therefore the fix age) current while idle, so a
    // button press can use a warm fix immediately.
    pumpGps();

    if (buttonPressed()) {
        runOneCycle();
    }

    delay(10);
}
