package com.teamstranscriber.companion.sync

import org.junit.Assert.assertEquals
import org.junit.Test

class RecordingSourceTest {
    @Test fun wireValues_matchContract() {
        assertEquals("teams_call", RecordingSource.TEAMS_CALL.wire)
        assertEquals("in_person", RecordingSource.IN_PERSON.wire)
        assertEquals("memo", RecordingSource.MEMO.wire)
    }
}
