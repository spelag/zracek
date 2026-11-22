package com.example.air.service

import android.content.Context
import android.graphics.drawable.Icon
import android.util.Log
import androidx.wear.watchface.complications.data.ComplicationData
import androidx.wear.watchface.complications.data.ComplicationType
import androidx.wear.watchface.complications.data.LongTextComplicationData
import androidx.wear.watchface.complications.data.MonochromaticImage
import androidx.wear.watchface.complications.data.MonochromaticImageComplicationData
import androidx.wear.watchface.complications.data.PhotoImageComplicationData
import androidx.wear.watchface.complications.data.PlainComplicationText
import androidx.wear.watchface.complications.data.RangedValueComplicationData
import androidx.wear.watchface.complications.data.ShortTextComplicationData
import androidx.wear.watchface.complications.datasource.ComplicationDataSourceService
import androidx.wear.watchface.complications.datasource.ComplicationRequest
import com.example.air.R
import com.example.air.data.local.WheelCache
import com.example.air.data.model.ColorWheel
import com.example.air.data.repository.ColorRepository
import com.example.air.sync.ColorSyncWorker
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

/**
 * Service that fetches the 12-segment color wheel remotely and serves it via the Wear OS
 * Complications API.
 *
 * The watch face border slot uses `PHOTO_IMAGE` data: the provider renders the 12 colored
 * segments itself (each exactly one twelfth of the ring) because Wear OS color-list complication
 * types (WEIGHTED_ELEMENTS, color ramps) are capped at 7 colors.
 *
 * Requests are answered from the local cache when possible so the watch face updates instantly.
 * A background sync then refreshes the cached value and asks the system to re-request the data.
 */
class ColorComplicationDataSourceService(
    private val repository: ColorRepository = ColorRepository(),
    private val scope: CoroutineScope = CoroutineScope(Dispatchers.IO + SupervisorJob())
) : ComplicationDataSourceService() {

    override fun onComplicationRequest(
        request: ComplicationRequest,
        listener: ComplicationRequestListener
    ) {
        Log.d(
            TAG,
            "onComplicationRequest id=${request.complicationInstanceId} " +
                "type=${request.complicationType}"
        )
        val cachedWheel = repository.loadCachedWheel(this)
        if (cachedWheel != null) {
            // Answer immediately with the last known value.
            val data = createComplicationData(this, cachedWheel, request.complicationType)
            Log.d(
                TAG,
                "Responding from cache: wheel=${cachedWheel.toCompactString()} " +
                    "uniformColor=${cachedWheel.uniformColor} " +
                    "data=${data?.javaClass?.simpleName ?: "null"} type=${data?.type}"
            )
            listener.onComplicationData(data)
            // Refresh only when the cached value is getting old. Enqueueing a sync on every
            // request would feed the update -> request -> sync -> update cycle indefinitely.
            val cache = WheelCache(this)
            if (System.currentTimeMillis() - cache.lastUpdated() >= MIN_REFRESH_INTERVAL_MS) {
                ColorSyncWorker.requestImmediateSync(this)
            }
            return
        }

        // First request and nothing cached yet: fetch once (bounded by the HTTP timeouts).
        scope.launch {
            val wheel = repository.refreshWheel(this@ColorComplicationDataSourceService)
            val data = createComplicationData(
                this@ColorComplicationDataSourceService,
                wheel,
                request.complicationType
            )
            Log.d(
                TAG,
                "Responding after fetch: wheel=${wheel.toCompactString()} " +
                    "uniformColor=${wheel.uniformColor} " +
                    "data=${data?.javaClass?.simpleName ?: "null"} type=${data?.type}"
            )
            listener.onComplicationData(data)
        }
    }

    override fun getPreviewData(type: ComplicationType): ComplicationData? {
        Log.d(TAG, "getPreviewData type=$type")
        return createComplicationData(this, PREVIEW_WHEEL, type)
    }

    override fun onDestroy() {
        super.onDestroy()
        scope.cancel()
    }

    companion object {
        private const val TAG = "ColorComplicationSource"

        /** Minimum age of the cached wheel before a complication request triggers a refresh. */
        private const val MIN_REFRESH_INTERVAL_MS = 5 * 60 * 1000L

        /** Sample wheel shown in the watch face editor before real data arrives. */
        private val PREVIEW_WHEEL = ColorWheel(
            listOf(1, 2, 3, 4, 5, 1, 2, 3, 4, 5, 1, 2)
        )

        /**
         * Creates ComplicationData for a given color wheel and ComplicationType.
         */
        fun createComplicationData(
            context: Context,
            wheel: ColorWheel,
            type: ComplicationType
        ): ComplicationData? {
            val icon = Icon.createWithResource(context, R.drawable.ic_color_lens)
            val monochromaticImage = MonochromaticImage.Builder(icon).build()
            val values = wheel.toCompactString()
            val contentDescription = PlainComplicationText.Builder(
                "Color wheel ${wheel.toSpacedString()}"
            ).build()

            return when (type) {
                ComplicationType.PHOTO_IMAGE -> {
                    // The provider renders the actual 12-segment ring.
                    PhotoImageComplicationData.Builder(
                        photoImage = WheelImageRenderer.renderIcon(wheel),
                        contentDescription = contentDescription
                    ).build()
                }
                ComplicationType.SHORT_TEXT -> {
                    ShortTextComplicationData.Builder(
                        text = PlainComplicationText.Builder(values).build(),
                        contentDescription = contentDescription
                    )
                        .setTitle(PlainComplicationText.Builder("Wheel").build())
                        .setMonochromaticImage(monochromaticImage)
                        .build()
                }
                ComplicationType.RANGED_VALUE -> {
                    val average = wheel.values.average().toFloat()
                    RangedValueComplicationData.Builder(
                        value = average,
                        min = ColorWheel.MIN_VALUE.toFloat(),
                        max = ColorWheel.MAX_VALUE.toFloat(),
                        contentDescription = contentDescription
                    )
                        .setText(PlainComplicationText.Builder(values).build())
                        .setTitle(PlainComplicationText.Builder("Wheel").build())
                        .setMonochromaticImage(monochromaticImage)
                        .build()
                }
                ComplicationType.MONOCHROMATIC_IMAGE -> {
                    MonochromaticImageComplicationData.Builder(
                        monochromaticImage = monochromaticImage,
                        contentDescription = contentDescription
                    ).build()
                }
                ComplicationType.LONG_TEXT -> {
                    LongTextComplicationData.Builder(
                        text = PlainComplicationText.Builder(
                            "Wheel: ${wheel.toSpacedString()}"
                        ).build(),
                        contentDescription = contentDescription
                    )
                        .setTitle(PlainComplicationText.Builder("Color wheel").build())
                        .setMonochromaticImage(monochromaticImage)
                        .build()
                }
                else -> null
            }
        }
    }
}
