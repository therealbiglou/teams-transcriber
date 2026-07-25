package com.teamstranscriber.companion.recording

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import com.teamstranscriber.companion.BuildConfig
import com.teamstranscriber.companion.storage.Storage
import com.teamstranscriber.companion.sync.OutboxWriter
import com.teamstranscriber.companion.sync.RecordingSource
import com.teamstranscriber.companion.sync.Sidecar
import com.teamstranscriber.companion.sync.defaultTitle
import com.teamstranscriber.companion.sync.newUid
import java.io.File
import java.time.ZoneId
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

class RecordingService : Service() {
    private lateinit var recorder: AudioRecorder
    private lateinit var tempFile: File
    private var source: RecordingSource = RecordingSource.MEMO
    private var startedAt: Long = 0L
    private var finishing = false
    private var tickerJob: Job? = null
    private val scope = CoroutineScope(Dispatchers.Main + Job())

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> {
                val src = intent.getStringExtra(EXTRA_SOURCE)
                    ?.let { runCatching { RecordingSource.valueOf(it) }.getOrNull() }
                if (src != null) begin(src) else stopSelf()
            }
            ACTION_STOP -> finish()
        }
        return START_STICKY
    }

    private fun begin(src: RecordingSource) {
        if (RecordingBus.state.value.active) return // already recording; ignore
        createChannel()
        if (!RecordingGuards.hasEnoughFreeSpace(Storage.outboxDir().usableSpace)) {
            notifyError("Not enough free storage to record")
            stopSelf()
            return
        }
        source = src
        startedAt = System.currentTimeMillis()
        tempFile = File(cacheDir, "capture_${newUid()}.m4a")
        recorder = AudioRecorder(tempFile)
        startForeground(NOTIF_ID, buildNotification(0L))
        try {
            recorder.start(this)
        } catch (e: Exception) {
            notifyError("Couldn't start recording: ${e.message ?: "microphone unavailable"}")
            tempFile.delete()
            RecordingBus.set(RecordingStatus(active = false))
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            return
        }
        finishing = false
        RecordingBus.set(RecordingStatus(active = true, source = src, startedAtEpochMillis = startedAt))
        tickerJob = scope.launch {
            while (RecordingBus.state.value.active) {
                delay(1_000)
                val elapsed = System.currentTimeMillis() - startedAt
                if (RecordingGuards.shouldAutoStop(elapsed)) { finish(); break } // max-duration cap
                notificationManager().notify(NOTIF_ID, buildNotification(elapsed))
            }
        }
    }

    private fun finish() {
        if (finishing) return
        finishing = true
        tickerJob?.cancel()
        val endedAt = System.currentTimeMillis()
        val produced = if (::recorder.isInitialized) recorder.stop() else false
        RecordingBus.set(RecordingStatus(active = false))
        if (produced && tempFile.exists()) {
            val uid = newUid()
            val sidecar = Sidecar(
                uid = uid,
                title = defaultTitle(source, startedAt, ZoneId.systemDefault()),
                source = source,
                startedAtEpochMillis = startedAt,
                endedAtEpochMillis = endedAt,
                appVersion = BuildConfig.VERSION_NAME,
            )
            runCatching { OutboxWriter(Storage.outboxDir()).write(tempFile, sidecar) }
                .onFailure { notifyError(it.message ?: "save failed") }
        } else if (::recorder.isInitialized) {
            notifyError("Recording too short — nothing saved")
        }
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    private fun buildNotification(elapsedMillis: Long): Notification {
        val stopIntent = PendingIntent.getService(
            this, 0, Intent(this, RecordingService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return Notification.Builder(this, CHANNEL_ID)
            .setContentTitle("Recording — ${source.label()}")
            .setContentText(formatElapsed(elapsedMillis))
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setOngoing(true)
            .setUsesChronometer(false)
            .addAction(Notification.Action.Builder(null, "Stop", stopIntent).build())
            .build()
    }

    private fun notifyError(msg: String) {
        createChannel()
        notificationManager().notify(
            NOTIF_ID + 1,
            Notification.Builder(this, CHANNEL_ID)
                .setContentTitle("Recording not saved")
                .setContentText(msg)
                .setSmallIcon(android.R.drawable.stat_notify_error)
                .build(),
        )
    }

    private fun createChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val ch = NotificationChannel(CHANNEL_ID, "Recording", NotificationManager.IMPORTANCE_LOW)
            notificationManager().createNotificationChannel(ch)
        }
    }

    private fun notificationManager() =
        getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

    companion object {
        private const val CHANNEL_ID = "recording"
        private const val NOTIF_ID = 42
        const val ACTION_START = "com.teamstranscriber.companion.START"
        const val ACTION_STOP = "com.teamstranscriber.companion.STOP"
        const val EXTRA_SOURCE = "source"

        fun start(context: Context, source: RecordingSource) {
            val i = Intent(context, RecordingService::class.java)
                .setAction(ACTION_START).putExtra(EXTRA_SOURCE, source.name)
            context.startForegroundService(i)
        }

        fun stop(context: Context) {
            context.startService(
                Intent(context, RecordingService::class.java).setAction(ACTION_STOP),
            )
        }
    }
}

private fun RecordingSource.label(): String = when (this) {
    RecordingSource.TEAMS_CALL -> "Teams call"
    RecordingSource.IN_PERSON -> "In-person"
    RecordingSource.MEMO -> "Voice memo"
}
