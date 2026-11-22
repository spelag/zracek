package com.example.air.data.model

import java.time.LocalTime
import java.time.ZoneId

/**
 * A 12-segment color wheel. Each value is in the range 1..5 and maps to a fixed palette color.
 * Segment [i] covers the i-th twelfth of the ring (starting at 12 o'clock, clockwise).
 *
 * When [uniformColor] is set every segment uses that single ARGB color instead of the palette;
 * this is used for the blue fallback shown when no location is available.
 */
data class ColorWheel(
    val values: List<Int>,
    val uniformColor: Int? = null
) {

    init {
        require(values.size == SEGMENTS) {
            "A color wheel needs exactly $SEGMENTS values, got ${values.size}"
        }
        require(values.all { it in MIN_VALUE..MAX_VALUE }) {
            "Every wheel value must be between $MIN_VALUE and $MAX_VALUE, got $values"
        }
    }

    val segmentColors: List<Int>
        get() = segmentColorsForHour(LocalTime.now(ZoneId.systemDefault()).hour)

    /**
     * Returns the 12 segment ARGB colors rotated clockwise by [hour] (0..23),
     * so `values[0]` aligns with the `[hour % 12]` clock position and wraps around.
     */
    fun segmentColorsForHour(hour: Int): List<Int> {
        uniformColor?.let { color ->
            return List(SEGMENTS) { color or OPAQUE_ALPHA }
        }
        val offset = Math.floorMod(hour, SEGMENTS)
        return List(SEGMENTS) { segmentIndex ->
            val valueIndex = Math.floorMod(segmentIndex - offset, SEGMENTS)
            colorFor(values[valueIndex])
        }
    }

    fun rotatedForHour(
        hour: Int = LocalTime.now(ZoneId.systemDefault()).hour
    ): ColorWheel {
        if (uniformColor != null) return this
        val offset = Math.floorMod(hour, SEGMENTS)
        val rotated = List(SEGMENTS) { segmentIndex ->
            values[Math.floorMod(segmentIndex - offset, SEGMENTS)]
        }
        return copy(values = rotated)
    }

    /** Compact representation, e.g. "135241352413". */
    fun toCompactString(): String = values.joinToString("")

    /** Human readable representation, e.g. "1 3 5 2 ...". */
    fun toSpacedString(): String = values.joinToString(" ")

    companion object {
        const val SEGMENTS = 12
        const val MIN_VALUE = 1
        const val MAX_VALUE = 5

        /**
         * Palette indexed by value - 1. Entries may be written as 24-bit RGB values; [colorFor]
         * forces them opaque so a missing alpha channel can never render segments invisible.
         */
        val PALETTE: IntArray = intArrayOf(
            0x2ECC70.toInt(),
            0xA3D977.toInt(),
            0xF1C40F.toInt(),
            0xE67E22.toInt(),
            0xC0392B.toInt()
        )

        private val OPAQUE_ALPHA = 0xFF000000.toInt()

        fun colorFor(value: Int): Int {
            require(value in MIN_VALUE..MAX_VALUE) { "Wheel value out of range: $value" }
            return PALETTE[value - 1] or OPAQUE_ALPHA
        }

        /** Blue used for the fallback arc when no location data is available. */
        const val FALLBACK_COLOR = 0xFF4285F4.toInt()

        /** Solid blue wheel used before the first successful sync or without location data. */
        val FALLBACK: ColorWheel = ColorWheel(
            values = List(SEGMENTS) { MAX_VALUE },
            uniformColor = FALLBACK_COLOR
        )
    }
}
