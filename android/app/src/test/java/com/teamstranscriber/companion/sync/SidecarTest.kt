package com.teamstranscriber.companion.sync

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SidecarTest {
    private fun sample(ended: Long? = 1784989860000L) = Sidecar(
        uid = "0123456789ab",
        title = "In-person meeting — Jul 25, 10:30 AM",
        source = RecordingSource.IN_PERSON,
        startedAtEpochMillis = 1784989800000L, // 2026-07-25T14:30:00Z
        endedAtEpochMillis = ended,            // +60s
        appVersion = "0.1.0",
    )

    @Test fun toJson_hasExactlyTheContractKeys() {
        val o = JSONObject(sample().toJson())
        assertEquals(
            setOf("uid", "title", "source", "started_at", "ended_at", "duration_ms", "app_version"),
            o.keys().asSequence().toSet(),
        )
    }

    @Test fun toJson_serializesValuesPerContract() {
        val o = JSONObject(sample().toJson())
        assertEquals("0123456789ab", o.getString("uid"))
        assertEquals("in_person", o.getString("source"))
        assertEquals("2026-07-25T14:30:00+00:00", o.getString("started_at"))
        assertEquals("2026-07-25T14:31:00+00:00", o.getString("ended_at"))
        assertEquals(60000L, o.getLong("duration_ms"))
        assertEquals("0.1.0", o.getString("app_version"))
    }

    @Test fun toJson_nullEndedGivesJsonNullDurationAndEnded() {
        val o = JSONObject(sample(ended = null).toJson())
        assertTrue(o.isNull("ended_at"))
        assertTrue(o.isNull("duration_ms"))
    }
}
