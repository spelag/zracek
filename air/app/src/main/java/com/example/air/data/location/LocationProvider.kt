package com.example.air.data.location

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.util.Log
import androidx.core.content.ContextCompat
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority
import com.google.android.gms.tasks.CancellationTokenSource
import com.google.android.gms.tasks.Tasks
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.util.concurrent.TimeUnit

/** Simple latitude/longitude pair. */
data class Coordinates(val latitude: Double, val longitude: Double)

/**
 * Provides the device coordinates for the API request.
 *
 * Uses the fused location provider when the app holds a location permission, falling back to the
 * last coordinates saved by a previous lookup (so background refreshes can still include
 * coordinates after a permission has been revoked or before a fix is available).
 */
class LocationProvider(context: Context) {

    private val appContext = context.applicationContext
    private val fusedLocationClient = LocationServices.getFusedLocationProviderClient(appContext)
    private val prefs = appContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun hasLocationPermission(): Boolean {
        return ContextCompat.checkSelfPermission(
            appContext, Manifest.permission.ACCESS_FINE_LOCATION
        ) == PackageManager.PERMISSION_GRANTED || ContextCompat.checkSelfPermission(
            appContext, Manifest.permission.ACCESS_COARSE_LOCATION
        ) == PackageManager.PERMISSION_GRANTED
    }

    /**
     * Returns the best available coordinates, or null if there is neither permission nor a
     * previously cached fix. Never throws.
     */
    suspend fun getCoordinates(): Coordinates? = withContext(Dispatchers.IO) {
        if (!hasLocationPermission()) {
            Log.w(TAG, "No location permission; using cached coordinates if available")
            return@withContext cachedCoordinates()
        }

        try {
            val lastLocation = Tasks.await(
                fusedLocationClient.lastLocation,
                LAST_LOCATION_TIMEOUT_SECONDS,
                TimeUnit.SECONDS
            )
            val location = lastLocation ?: Tasks.await(
                fusedLocationClient.getCurrentLocation(
                    Priority.PRIORITY_BALANCED_POWER_ACCURACY,
                    CancellationTokenSource().token
                ),
                CURRENT_LOCATION_TIMEOUT_SECONDS,
                TimeUnit.SECONDS
            )

            if (location != null) {
                cache(location)
                return@withContext Coordinates(location.latitude, location.longitude)
            }
            Log.w(TAG, "No location fix available; using cached coordinates if available")
            cachedCoordinates()
        } catch (e: SecurityException) {
            Log.w(TAG, "Location permission lost while reading coordinates", e)
            cachedCoordinates()
        } catch (e: Exception) {
            Log.w(TAG, "Failed to obtain location; using cached coordinates if available", e)
            cachedCoordinates()
        }
    }

    private fun cache(location: Location) {
        prefs.edit()
            .putString(KEY_LATITUDE, location.latitude.toString())
            .putString(KEY_LONGITUDE, location.longitude.toString())
            .apply()
    }

    private fun cachedCoordinates(): Coordinates? {
        val latitude = prefs.getString(KEY_LATITUDE, null)?.toDoubleOrNull() ?: return null
        val longitude = prefs.getString(KEY_LONGITUDE, null)?.toDoubleOrNull() ?: return null
        return Coordinates(latitude, longitude)
    }

    private companion object {
        const val TAG = "LocationProvider"
        const val PREFS_NAME = "location_cache"
        const val KEY_LATITUDE = "latitude"
        const val KEY_LONGITUDE = "longitude"
        const val LAST_LOCATION_TIMEOUT_SECONDS = 5L
        const val CURRENT_LOCATION_TIMEOUT_SECONDS = 10L
    }
}
