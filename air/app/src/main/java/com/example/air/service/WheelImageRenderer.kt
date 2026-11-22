package com.example.air.service

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.drawable.Icon
import com.example.air.data.model.ColorWheel

/**
 * Renders the 12-segment color wheel as a transparent bitmap ring.
 *
 * The ring geometry matches the watch face's static border/arc (442px diameter, 8px stroke in a
 * 450x450 coordinate space) so the rendered image lines up exactly when the watch face draws it
 * inside the complication slot.
 */
object WheelImageRenderer {

    const val SIZE = 450
    private const val RING_STROKE = 8f
    private const val RING_INSET = 4f

    /** Small overlap so anti-aliased segment edges tile without visible seams. */
    private const val SEGMENT_OVERLAP = 0.25f

    fun renderIcon(wheel: ColorWheel, size: Int = SIZE): Icon =
        Icon.createWithBitmap(renderBitmap(wheel, size))

    fun renderBitmap(wheel: ColorWheel, size: Int = SIZE): Bitmap {
        val scale = size / SIZE.toFloat()
        val bitmap = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bitmap)

        val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
            strokeWidth = RING_STROKE * scale
            strokeCap = Paint.Cap.BUTT
        }

        val inset = RING_INSET * scale
        val bounds = RectF(inset, inset, size - inset, size - inset)
        val sweep = 360f / ColorWheel.SEGMENTS

        wheel.segmentColors.forEachIndexed { index, color ->
            paint.color = color
            // Segment 0 starts at 12 o'clock and runs clockwise, one twelfth of the circle each.
            canvas.drawArc(
                bounds,
                -90f + index * sweep,
                sweep + SEGMENT_OVERLAP,
                false,
                paint
            )
        }

        return bitmap
    }
}
