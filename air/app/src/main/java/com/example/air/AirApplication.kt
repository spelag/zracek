package com.example.air

import android.app.Application
import com.example.air.sync.ColorSyncWorker

class AirApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        // Periodic background sync keeps the RGB border in step with the remote API,
        // while the one-shot sync warms the cache as soon as the app is used.
        ColorSyncWorker.schedulePeriodic(this)
        ColorSyncWorker.requestImmediateSync(this)
    }
}
