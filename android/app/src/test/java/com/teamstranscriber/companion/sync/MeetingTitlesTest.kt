package com.teamstranscriber.companion.sync

import java.time.ZoneId
import org.junit.Assert.assertEquals
import org.junit.Test

class MeetingTitlesTest {
    private val ny = ZoneId.of("America/New_York")

    // 2026-07-25T14:30:00Z == 10:30 AM in New York (EDT)
    @Test fun defaultTitle_inPerson_hasLabelAndLocalTime() {
        assertEquals(
            "In-person meeting — Jul 25, 10:30 AM",
            defaultTitle(RecordingSource.IN_PERSON, 1784989800000L, ny),
        )
    }

    @Test fun defaultTitle_memo_andTeamsCall_haveTheirLabels() {
        assertEquals(
            "Voice memo — Jul 25, 10:30 AM",
            defaultTitle(RecordingSource.MEMO, 1784989800000L, ny),
        )
        assertEquals(
            "Teams call — Jul 25, 10:30 AM",
            defaultTitle(RecordingSource.TEAMS_CALL, 1784989800000L, ny),
        )
    }
}
