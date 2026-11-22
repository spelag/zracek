package com.example.air.data.repository

import android.content.Context
import android.util.Log
import com.example.air.data.local.WheelCache
import com.example.air.data.location.LocationProvider
import com.example.air.data.model.ColorWheel
import com.example.air.data.remote.ColorApiClient
import kotlinx.coroutines.CancellationException

/**
 * Repository responsible for providing the 12-segment color wheel.
 * Wraps remote API calls, persists the last successful value and handles network
 * exceptions with the cached (or fallback) wheel.
 */
class ColorRepository(
    private val apiClient: ColorApiClient = ColorApiClient()
) {

    /**
     * Returns the last known wheel without touching the network, or null if nothing is cached.
     */
    fun loadCachedWheel(context: Context): ColorWheel? = WheelCache(context).load()

    /**
     * Returns the last known wheel without touching the network. This is what the complication
     * data source serves while a background refresh is in flight.
     */
    fun getCachedWheel(context: Context): ColorWheel {
        return loadCachedWheel(context) ?: DEFAULT_FALLBACK_WHEEL
    }

    /**
     * Fetches the wheel from the remote endpoint and caches it.
     * Without location data the arc falls back to a solid blue ring. If network/parsing fails,
     * returns the cached wheel if present, otherwise the blue fallback wheel. Never throws
     * (except for cancellation).
     */
    suspend fun refreshWheel(context: Context): ColorWheel {
        val coordinates = try {
            LocationProvider(context).getCoordinates()
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Log.w(TAG, "Failed to read location; using blue fallback wheel", e)
            null
        }

        if (coordinates == null) {
            Log.w(TAG, "No location data available; using blue fallback wheel")
            WheelCache(context).save(DEFAULT_FALLBACK_WHEEL)
            return DEFAULT_FALLBACK_WHEEL
        }

        return try {
            Log.d(TAG, "Fetching wheel for coordinates: $coordinates")
            val wheel = apiClient.fetchWheel(
                latitude = coordinates.latitude,
                longitude = coordinates.longitude
            )
            WheelCache(context).save(wheel)
            wheel
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Log.e(TAG, "Network exception fetching color wheel, returning cached/fallback wheel", e)
            val cached = loadCachedWheel(context)
            if (cached != null) {
                cached
            } else {
                WheelCache(context).save(DEFAULT_FALLBACK_WHEEL)
                DEFAULT_FALLBACK_WHEEL
            }
        }
    }

    companion object {
        private const val TAG = "ColorRepository"

        /**
         * Fallback wheel used when network is offline, the request fails, or no data has been
         * fetched yet.
         */
        val DEFAULT_FALLBACK_WHEEL: ColorWheel = ColorWheel.FALLBACK
    }
}
