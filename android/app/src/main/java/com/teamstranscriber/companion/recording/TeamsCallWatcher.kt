package com.teamstranscriber.companion.recording

import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import com.teamstranscriber.companion.settings.AppPrefs
import com.teamstranscriber.companion.sync.RecordingSource

class TeamsCallWatcher : NotificationListenerService() {
    private val prefs by lazy { AppPrefs(applicationContext) }
    private var callKey: String? = null

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        if (!prefs.autoRecordEnabled) return
        val n = sbn.notification ?: return
        val matches = TeamsCallDetector.isOngoingTeamsCall(
            pkg = sbn.packageName,
            category = n.category,
            isOngoing = (n.flags and android.app.Notification.FLAG_ONGOING_EVENT) != 0,
            title = n.extras?.getCharSequence(android.app.Notification.EXTRA_TITLE)?.toString(),
            text = n.extras?.getCharSequence(android.app.Notification.EXTRA_TEXT)?.toString(),
        )
        if (matches && !RecordingBus.state.value.active) {
            callKey = sbn.key
            // Never start the service from here — Android 14+ forbids starting a mic
            // foreground service from the background. The already-armed, already-foreground
            // RecordingService picks this command up on its own collector.
            AutoRecordControl.requestBeginCapture(RecordingSource.TEAMS_CALL)
        }
    }

    override fun onNotificationRemoved(sbn: StatusBarNotification) {
        // Stop only the recording that this call notification started.
        if (sbn.key == callKey && RecordingBus.state.value.source == RecordingSource.TEAMS_CALL) {
            callKey = null
            AutoRecordControl.requestStopCapture()
        }
    }
}
