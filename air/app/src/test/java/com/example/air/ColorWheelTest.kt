package com.example.air

import com.example.air.data.model.ColorWheel
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test

class ColorWheelTest {

    @Test
    fun valuesMapToPaletteColors() {
        val wheel = ColorWheel(listOf(1, 2, 3, 4, 5, 1, 2, 3, 4, 5, 1, 2))
        assertEquals(12, wheel.segmentColors.size)
        for (value in 1..5) {
            assertEquals(
                ColorWheel.PALETTE[value - 1] or 0xFF000000.toInt(),
                ColorWheel.colorFor(value)
            )
        }
        // Different values produce different colors.
        assertNotEquals(ColorWheel.colorFor(1), ColorWheel.colorFor(2))
    }

    @Test
    fun allSegmentColorsAreOpaque() {
        val wheel = ColorWheel(listOf(1, 2, 3, 4, 5, 1, 2, 3, 4, 5, 1, 2))
        wheel.segmentColors.forEach { color ->
            assertEquals(0xFF, (color ushr 24) and 0xFF)
        }
        ColorWheel.FALLBACK.segmentColors.forEach { color ->
            assertEquals(0xFF, (color ushr 24) and 0xFF)
        }
    }

    @Test(expected = IllegalArgumentException::class)
    fun rejectsWrongNumberOfSegments() {
        ColorWheel(listOf(1, 2, 3))
    }

    @Test(expected = IllegalArgumentException::class)
    fun rejectsValuesOutsideOneToFive() {
        ColorWheel(listOf(1, 2, 3, 4, 5, 1, 2, 3, 4, 5, 1, 6))
    }

    @Test
    fun rotationAlignsValuesWithClockHour() {
        val values = listOf(1, 2, 3, 4, 5, 1, 2, 3, 4, 5, 1, 2)
        val wheel = ColorWheel(values)
        for (hour in 0..23) {
            val offset = hour % ColorWheel.SEGMENTS
            val colors = wheel.segmentColorsForHour(hour)
            for (segment in 0 until ColorWheel.SEGMENTS) {
                val valueIndex =
                    Math.floorMod(segment - offset, ColorWheel.SEGMENTS)
                assertEquals(ColorWheel.colorFor(values[valueIndex]), colors[segment])
            }
        }
        // rotatedForHour(h) then hour 0 is the same as segmentColorsForHour(h)
        assertEquals(
            wheel.segmentColorsForHour(5),
            wheel.rotatedForHour(5).segmentColorsForHour(0)
        )
    }

    @Test
    fun fallbackWheelIsValid() {
        assertEquals(ColorWheel.SEGMENTS, ColorWheel.FALLBACK.values.size)
        assertEquals(12, ColorWheel.FALLBACK.segmentColors.size)
    }

    @Test
    fun fallbackWheelIsSolidBlue() {
        assertEquals(
            List(ColorWheel.SEGMENTS) { ColorWheel.FALLBACK_COLOR },
            ColorWheel.FALLBACK.segmentColors
        )
    }
}
