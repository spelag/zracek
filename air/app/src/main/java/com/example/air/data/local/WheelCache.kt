package com.example.air.data.local

import android.content.Context
import com.example.air.data.model.ColorWheel

/**
 * Small persistent cache for the last successfully fetched 12-segment color wheel.
 *
 * Keeping the last known value lets the complication data source answer requests instantly
 * (including while offline) and keeps the border stable across complication refreshes.
 */
class WheelCache(context: Context) {

    private val prefs = context.applicationContext
        .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    /** Returns the cached wheel, or null if nothing (valid) has been stored yet. */
    fun load(): ColorWheel? {
        val raw = prefs.getString(KEY_VALUES, null) ?: return null
        val values = raw.split(",").mapNotNull { it.trim().toIntOrNull() }
        val uniformColor = if (prefs.contains(KEY_UNIFORM_COLOR)) {
            prefs.getInt(KEY_UNIFORM_COLOR, 0)
        } else {
            null
        }
        return try {
            ColorWheel(values, uniformColor)
        } catch (e: IllegalArgumentException) {
            null
        }
    }

    fun save(wheel: ColorWheel) {
        val editor = prefs.edit()
            .putString(KEY_VALUES, wheel.values.joinToString(","))
            .putLong(KEY_UPDATED_AT, System.currentTimeMillis())

        if (wheel.uniformColor != null) {
            editor.putInt(KEY_UNIFORM_COLOR, wheel.uniformColor)
        } else {
            editor.remove(KEY_UNIFORM_COLOR)
        }
        editor.apply()
    }

    /** Wall-clock time (millis) of the last successful save, or 0 if never saved. */
    fun lastUpdated(): Long = prefs.getLong(KEY_UPDATED_AT, 0L)

    private companion object {
        const val PREFS_NAME = "color_wheel_cache"
        const val KEY_VALUES = "values"
        const val KEY_UNIFORM_COLOR = "uniform_color"
        const val KEY_UPDATED_AT = "updated_at"
    }
}
