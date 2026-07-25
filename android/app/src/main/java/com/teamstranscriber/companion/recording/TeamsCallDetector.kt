package com.teamstranscriber.companion.recording

import android.app.Notification

/** Pure heuristic: does this notification represent an ongoing Teams call? */
object TeamsCallDetector {
    const val TEAMS_PACKAGE = "com.microsoft.teams"

    fun isOngoingTeamsCall(
        pkg: String,
        category: String?,
        isOngoing: Boolean,
        title: String?,
        text: String?,
    ): Boolean = pkg == TEAMS_PACKAGE && isOngoing && category == Notification.CATEGORY_CALL
}
