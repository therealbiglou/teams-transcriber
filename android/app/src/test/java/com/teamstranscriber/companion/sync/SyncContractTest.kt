package com.teamstranscriber.companion.sync

import org.junit.Assert.assertEquals
import org.junit.Test

class SyncContractTest {
    @Test
    fun recordingFileName_usesRecPrefixAndM4aExtension() {
        assertEquals("rec_abc123.m4a", SyncContract.recordingFileName("abc123"))
    }

    @Test
    fun sidecarFileName_sharesStemWithRecording() {
        assertEquals("rec_abc123.json", SyncContract.sidecarFileName("abc123"))
    }
}
