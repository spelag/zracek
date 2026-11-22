package com.example.air.sync

import android.content.ComponentName
import android.content.Context
import android.util.Log
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.wear.watchface.complications.datasource.ComplicationDataSourceUpdateRequester
import com.example.air.data.repository.ColorRepository
import com.example.air.service.ColorComplicationDataSourceService
import java.util.concurrent.TimeUnit

/**
 * Background worker that refreshes the RGB color from the remote API, persists it, and then asks
 * the system to re-request the complication data so the watch face border picks up the new color.
 *
 * Runs as periodic work (network constrained) and as on-demand one-shot work whenever a
 * complication request arrives or the app starts.
 */
class ColorSyncWorker(
    appContext: Context,
    params: WorkerParameters
) : CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val repository = ColorRepository()
        val previous = repository.loadCachedWheel(applicationContext)
        val wheel = repository.refreshWheel(applicationContext)
        Log.d(TAG, "Color wheel synced: ${wheel.toCompactString()}")

        if (previous == wheel) {
            Log.d(TAG, "Wheel values unchanged, still requesting a complication refresh")
        }
        // Always ask the complication system to re-read the provider. Skipping this when the
        // values match the cache leaves a stale/EMPTY watch face slot stuck, because nothing
        // else nudges it until the system's own (15 minute) update period elapses. The loop is
        // avoided on the provider side, which only enqueues a sync when the cache is old.
        requestComplicationUpdate(applicationContext)
        return Result.success()
    }

    companion object {
        private const val TAG = "ColorSyncWorker"

        private const val UNIQUE_PERIODIC_WORK = "rgb_color_periodic_sync"
        private const val UNIQUE_ONE_SHOT_WORK = "rgb_color_one_shot_sync"

        /** Minimum period allowed by WorkManager / Wear OS power guidelines. */
        const val PERIOD_MINUTES = 15L

        /** Schedules the battery-friendly periodic refresh. Safe to call repeatedly. */
        fun schedulePeriodic(context: Context) {
            val request = PeriodicWorkRequestBuilder<ColorSyncWorker>(
                PERIOD_MINUTES, TimeUnit.MINUTES
            )
                .setConstraints(networkConstraints())
                .build()

            WorkManager.getInstance(context.applicationContext).enqueueUniquePeriodicWork(
                UNIQUE_PERIODIC_WORK,
                ExistingPeriodicWorkPolicy.KEEP,
                request
            )
        }

        /** Requests an immediate (network constrained) refresh. Safe to call repeatedly. */
        fun requestImmediateSync(context: Context) {
            val request = OneTimeWorkRequestBuilder<ColorSyncWorker>()
                .setConstraints(networkConstraints())
                .build()

            WorkManager.getInstance(context.applicationContext).enqueueUniqueWork(
                UNIQUE_ONE_SHOT_WORK,
                ExistingWorkPolicy.KEEP,
                request
            )
        }

        private fun networkConstraints() = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build()

        private fun requestComplicationUpdate(context: Context) {
            val component = ComponentName(
                context.applicationContext,
                ColorComplicationDataSourceService::class.java
            )
            ComplicationDataSourceUpdateRequester.create(context.applicationContext, component)
                .requestUpdateAll()
        }
    }
}
