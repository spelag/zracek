package com.example.air.data.remote

import com.example.air.data.model.ColorWheel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import org.json.JSONTokener
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * Remote API client for the 12-segment color wheel.
 *
 * The endpoint is expected to answer with a JSON array of exactly 12 integers, each between
 * 1 and 5, e.g. `[3,1,5,2,4,4,2,5,1,3,2,4]`. For convenience an object wrapping the array
 * (`{"values": [...]}`, `{"colors": [...]}`, `{"wheel": [...]}`, ...) is also accepted.
 */
class ColorApiClient(
    private val okHttpClient: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(5, TimeUnit.SECONDS)
        .build(),
    private val baseUrl: String = "https://ada.juje.si/air-quality"
) {

    /**
     * Fetches the current 12-value wheel from the remote API, passing the device coordinates
     * as `lat` and `lon` query parameters when they are available.
     * Throws [IOException] on network or parsing failure.
     */
    suspend fun fetchWheel(
        latitude: Double? = null,
        longitude: Double? = null
    ): ColorWheel = withContext(Dispatchers.IO) {
        val endpoint = buildWheelUrl(baseUrl, latitude, longitude)

        val request = Request.Builder()
            .url(endpoint)
            .header("Accept", "application/json")
            .get()
            .build()

        okHttpClient.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                throw IOException("HTTP request failed with status code: ${response.code}")
            }
            parseWheel(response.body.string())
        }
    }

    /**
     * Parses the JSON response into a [ColorWheel], enforcing exactly [ColorWheel.SEGMENTS]
     * integers in the range [ColorWheel.MIN_VALUE]..[ColorWheel.MAX_VALUE].
     */
    fun parseWheel(jsonStr: String): ColorWheel {
        val root = try {
            JSONTokener(jsonStr).nextValue()
        } catch (e: JSONException) {
            throw IOException("Malformed JSON response", e)
        }

        val array = when (root) {
            is JSONArray -> root
            is JSONObject -> findArray(root)
                ?: throw IOException("JSON response does not contain a color wheel array")
            else -> throw IOException("JSON response is not an array or object")
        }

        if (array.length() != ColorWheel.SEGMENTS) {
            throw IOException(
                "Expected ${ColorWheel.SEGMENTS} wheel values, got ${array.length()}"
            )
        }

        val values = (0 until ColorWheel.SEGMENTS).map { index ->
            val value = when (val element = array.opt(index)) {
                is Number -> element.toInt()
                is String -> element.toIntOrNull()
                else -> null
            } ?: throw IOException("Wheel value at index $index is not an integer")

            if (value !in ColorWheel.MIN_VALUE..ColorWheel.MAX_VALUE) {
                throw IOException(
                    "Wheel value at index $index ($value) is outside " +
                        "${ColorWheel.MIN_VALUE}..${ColorWheel.MAX_VALUE}"
                )
            }
            value
        }

        return ColorWheel(values)
    }

    private fun findArray(jsonObject: JSONObject): JSONArray? {
        for (key in ARRAY_KEYS) {
            jsonObject.optJSONArray(key)?.let { return it }
        }
        // Fall back to the first array found on the object.
        val keys = jsonObject.keys()
        while (keys.hasNext()) {
            jsonObject.optJSONArray(keys.next())?.let { return it }
        }
        return null
    }

    companion object {
        private val ARRAY_KEYS = listOf("values", "colors", "wheel", "segments", "data", "result")

        /**
         * Builds the wheel endpoint URL, appending `lat`/`lon` query parameters when both
         * coordinates are known.
         */
        fun buildWheelUrl(baseUrl: String, latitude: Double?, longitude: Double?): String {
            val base = baseUrl.trimEnd('/')
            if (latitude == null || longitude == null) return base
            return "$base?lat=$latitude&lon=$longitude"
        }
    }
}
