package com.example.air

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.wear.watchface.complications.data.ComplicationType
import androidx.wear.watchface.complications.data.LongTextComplicationData
import androidx.wear.watchface.complications.data.MonochromaticImageComplicationData
import androidx.wear.watchface.complications.data.PhotoImageComplicationData
import androidx.wear.watchface.complications.data.RangedValueComplicationData
import androidx.wear.watchface.complications.data.ShortTextComplicationData
import com.example.air.data.local.WheelCache
import com.example.air.data.location.LocationProvider
import com.example.air.data.model.ColorWheel
import com.example.air.data.remote.ColorApiClient
import com.example.air.data.repository.ColorRepository
import com.example.air.service.ColorComplicationDataSourceService
import com.example.air.service.WheelImageRenderer
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.IOException

/**
 * Verifies the complication provider builds every supported data type, including the
 * PHOTO_IMAGE data that renders the 12-segment wheel on the watch face border, and that the
 * remote API response parser enforces the 12 x 1..5 format.
 */
@RunWith(AndroidJUnit4::class)
class ComplicationDataTest {

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val wheel = ColorRepository.DEFAULT_FALLBACK_WHEEL

    @Test
    fun shortTextDataIsCreated() {
        val data = ColorComplicationDataSourceService.createComplicationData(
            context, wheel, ComplicationType.SHORT_TEXT
        )
        assertTrue(data is ShortTextComplicationData)
    }

    @Test
    fun longTextDataIsCreated() {
        val data = ColorComplicationDataSourceService.createComplicationData(
            context, wheel, ComplicationType.LONG_TEXT
        )
        assertTrue(data is LongTextComplicationData)
    }

    @Test
    fun rangedValueDataIsCreated() {
        val data = ColorComplicationDataSourceService.createComplicationData(
            context, wheel, ComplicationType.RANGED_VALUE
        )
        assertTrue(data is RangedValueComplicationData)
    }

    @Test
    fun monochromaticImageDataIsCreated() {
        val data = ColorComplicationDataSourceService.createComplicationData(
            context, wheel, ComplicationType.MONOCHROMATIC_IMAGE
        )
        assertTrue(data is MonochromaticImageComplicationData)
    }

    @Test
    fun photoImageDataIsCreated() {
        val data = ColorComplicationDataSourceService.createComplicationData(
            context, wheel, ComplicationType.PHOTO_IMAGE
        )
        assertTrue(data is PhotoImageComplicationData)
    }

    @Test
    fun wheelBitmapIsRendered() {
        val bitmap = WheelImageRenderer.renderBitmap(wheel)
        assertEquals(WheelImageRenderer.SIZE, bitmap.width)
        assertEquals(WheelImageRenderer.SIZE, bitmap.height)
    }

    @Test
    fun locationLookupReturnsValidCoordinatesOrNull() {
        val coordinates = runBlocking { LocationProvider(context).getCoordinates() }
        if (coordinates != null) {
            assertTrue(coordinates.latitude in -90.0..90.0)
            assertTrue(coordinates.longitude in -180.0..180.0)
        }
    }

    @Test
    fun cachePersistsBlueFallback() {
        WheelCache(context).save(ColorWheel.FALLBACK)
        assertEquals(ColorWheel.FALLBACK, WheelCache(context).load())
    }

    @Test
    fun parsesBareArrayOfTwelveValues() {
        val parsed = ColorApiClient().parseWheel("[1,2,3,4,5,1,2,3,4,5,1,2]")
        assertEquals(ColorWheel(listOf(1, 2, 3, 4, 5, 1, 2, 3, 4, 5, 1, 2)), parsed)
        assertEquals(ColorWheel.colorFor(1), parsed.segmentColorsForHour(0).first())
    }

    @Test
    fun parsesObjectWrappedArray() {
        val parsed = ColorApiClient().parseWheel("""{"values":[5,5,5,5,5,5,5,5,5,5,5,5]}""")
        assertEquals(List(12) { 5 }, parsed.values)
    }

    @Test(expected = IOException::class)
    fun rejectsWrongLength() {
        ColorApiClient().parseWheel("[1,2,3]")
    }

    @Test(expected = IOException::class)
    fun rejectsOutOfRangeValues() {
        ColorApiClient().parseWheel("[1,2,3,4,6,1,2,3,4,5,1,2]")
    }
}
