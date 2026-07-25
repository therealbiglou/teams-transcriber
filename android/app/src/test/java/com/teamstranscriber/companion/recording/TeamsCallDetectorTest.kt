package com.teamstranscriber.companion.recording

import android.app.Notification
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class TeamsCallDetectorTest {
    @Test fun matches_teamsOngoingCallCategory() {
        assertTrue(
            TeamsCallDetector.isOngoingTeamsCall(
                pkg = "com.microsoft.teams",
                category = Notification.CATEGORY_CALL,
                isOngoing = true, title = "Ongoing call", text = "00:12",
            ),
        )
    }

    @Test fun rejects_otherApps() {
        assertFalse(
            TeamsCallDetector.isOngoingTeamsCall(
                pkg = "com.whatsapp", category = Notification.CATEGORY_CALL,
                isOngoing = true, title = "Call", text = "",
            ),
        )
    }

    @Test fun rejects_teamsNonCallOrNonOngoing() {
        assertFalse(
            TeamsCallDetector.isOngoingTeamsCall(
                pkg = "com.microsoft.teams", category = Notification.CATEGORY_MESSAGE,
                isOngoing = true, title = "New message", text = "",
            ),
        )
        assertFalse(
            TeamsCallDetector.isOngoingTeamsCall(
                pkg = "com.microsoft.teams", category = Notification.CATEGORY_CALL,
                isOngoing = false, title = "Missed call", text = "",
            ),
        )
    }
}
