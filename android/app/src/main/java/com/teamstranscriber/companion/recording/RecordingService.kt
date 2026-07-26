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
import com.teamstranscriber.companion.MainActivity
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
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

class RecordingService : Service() {
    private lateinit var recorder: AudioRecorder
    private lateinit var tempFile: File
    private var source: RecordingSource = RecordingSource.MEMO
    private var startedAt: Long = 0L
    private var finishing = false
    private var armed = false
    private var tickerJob: Job? = null
    private var commandJob: Job? = null
    private val scope = CoroutineScope(Dispatchers.Main + SupervisorJob())

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_ARM -> arm()
            ACTION_DISARM -> disarm()
            ACTION_START -> {
                val src = intent.getStringExtra(EXTRA_SOURCE)
                    ?.let { runCatching { RecordingSource.valueOf(it) }.getOrNull() }
                if (src != null) beginCapture(src) else stopSelf()
            }
            ACTION_STOP -> finishCapture()
        }
        // A recorder can't resume a process-killed capture, and a sticky restart
        // redelivers a null intent (no action branch) that would leave the promised
        // startForeground uncalled — so don't auto-restart.
        return START_NOT_STICKY
    }

    private fun arm() {
        createChannel()
        // startForegroundService() imposes a "call startForeground() within ~5s" obligation on
        // EVERY call, including the idempotent re-arm from MainActivity.onStart while already armed.
        // Always (re)assert foreground; pick the notification by state so an in-progress recording
        // isn't clobbered.
        val notif = if (RecordingBus.state.value.active) {
            buildNotification(System.currentTimeMillis() - startedAt)
        } else {
            buildArmedNotification()
        }
        try {
            startForeground(NOTIF_ID, notif)
        } catch (e: Exception) {
            // Same Android 14+ background-mic-FGS restriction as beginCapture(); arm() is
            // only ever called from the foreground (UI), but guard defensively anyway.
            if (!armed) {
                notifyError("Couldn't arm auto-record — open the app to enable it")
                stopSelf()
            }
            return
        }
        if (armed) return // already armed; command collector already running
        armed = true
        commandJob = scope.launch {
            AutoRecordControl.commands.collect { runCatching { onCommand(it) } }
        }
    }

    private fun disarm() {
        // Stop listening for Teams calls first, so nothing can start a capture mid-teardown.
        commandJob?.cancel()
        commandJob = null
        armed = false // so finishCapture()'s not-armed branch fully tears down

        val status = RecordingBus.state.value
        when {
            // An auto-started Teams capture belongs to auto-record: end (and save) it.
            status.active && status.source == RecordingSource.TEAMS_CALL -> finishCapture()
            // A MANUAL recording is the user's, not auto-record's — turning the toggle off
            // must not cut it short. Keep capturing; the service stays foreground for it and
            // tears down via finishCapture()'s not-armed branch when the user hits Stop.
            status.active -> Unit
            else -> {
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
            }
        }
    }

    private suspend fun onCommand(command: AutoRecordCommand) {
        when (command) {
            is AutoRecordCommand.BeginCapture -> {
                if (armed && !RecordingBus.state.value.active) beginCapture(command.source)
            }
            AutoRecordCommand.StopCapture -> {
                val status = RecordingBus.state.value
                if (status.active && status.source == RecordingSource.TEAMS_CALL) finishCapture()
            }
        }
    }

    private fun beginCapture(src: RecordingSource) {
        if (RecordingBus.state.value.active) return // already recording; ignore
        createChannel()
        if (!RecordingGuards.hasEnoughFreeSpace(Storage.outboxDir().usableSpace)) {
            notifyError("Not enough free storage to record")
            if (!armed) stopSelf()
            return
        }
        source = src
        startedAt = System.currentTimeMillis()
        tempFile = File(cacheDir, "capture_${newUid()}.m4a")
        recorder = AudioRecorder(tempFile)
        if (!armed) {
            try {
                startForeground(NOTIF_ID, buildNotification(0L))
            } catch (e: Exception) {
                // Android 14+ forbids starting a microphone foreground service from the
                // background — e.g. auto-record fired from the NotificationListenerService
                // while the app isn't on screen. Fail gracefully with a notification
                // instead of crashing (see docs/.../2026-07-25-phase-3-android-recorder.md).
                notifyError("Couldn't start recording — open the app to record this call")
                tempFile.delete()
                stopSelf()
                return
            }
        } else {
            notificationManager().notify(NOTIF_ID, buildNotification(0L))
        }
        try {
            recorder.start(this)
        } catch (e: Exception) {
            notifyError("Couldn't start recording: ${e.message ?: "microphone unavailable"}")
            tempFile.delete()
            RecordingBus.set(RecordingStatus(active = false))
            if (armed) {
                notificationManager().notify(NOTIF_ID, buildArmedNotification())
            } else {
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
            }
            return
        }
        finishing = false
        RecordingBus.set(RecordingStatus(active = true, source = src, startedAtEpochMillis = startedAt))
        tickerJob = scope.launch {
            while (RecordingBus.state.value.active) {
                delay(1_000)
                val elapsed = System.currentTimeMillis() - startedAt
                if (RecordingGuards.shouldAutoStop(elapsed)) { finishCapture(); break } // max-duration cap
                notificationManager().notify(NOTIF_ID, buildNotification(elapsed))
            }
        }
    }

    private fun finishCapture() {
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
        if (armed) {
            notificationManager().notify(NOTIF_ID, buildArmedNotification())
        } else {
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
        }
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

    private fun buildArmedNotification(): Notification {
        val contentIntent = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return Notification.Builder(this, CHANNEL_ID)
            .setContentTitle("Watching for Teams calls")
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setOngoing(true)
            .setContentIntent(contentIntent)
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
        const val ACTION_ARM = "com.teamstranscriber.companion.ARM"
        const val ACTION_DISARM = "com.teamstranscriber.companion.DISARM"
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

        fun arm(context: Context) = context.startForegroundService(
            Intent(context, RecordingService::class.java).setAction(ACTION_ARM),
        )

        fun disarm(context: Context) = context.startService(
            Intent(context, RecordingService::class.java).setAction(ACTION_DISARM),
        )
    }
}

private fun RecordingSource.label(): String = when (this) {
    RecordingSource.TEAMS_CALL -> "Teams call"
    RecordingSource.IN_PERSON -> "In-person"
    RecordingSource.MEMO -> "Voice memo"
}
