package com.example.air

import com.example.air.data.remote.ColorApiClient
import org.junit.Assert.assertEquals
import org.junit.Test

class ColorApiClientUrlTest {

    @Test
    fun appendsLatitudeAndLongitude() {
        assertEquals(
            "https://ada.juje.si/air-quality?lat=46.0497&lon=14.5069",
            ColorApiClient.buildWheelUrl(
                "https://ada.juje.si/air-quality/", 46.0497, 14.5069
            )
        )
    }

    @Test
    fun keepsBaseUrlWhenCoordinatesAreMissing() {
        assertEquals(
            "https://ada.juje.si/air-quality",
            ColorApiClient.buildWheelUrl("https://ada.juje.si/air-quality", null, null)
        )
    }
}
