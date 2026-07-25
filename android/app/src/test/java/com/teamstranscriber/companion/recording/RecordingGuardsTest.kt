package com.teamstranscriber.companion.recording

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RecordingGuardsTest {
    @Test fun shouldAutoStop_trueAtOrPastCap() {
        assertFalse(RecordingGuards.shouldAutoStop(elapsedMillis = 10, capMillis = 100))
        assertTrue(RecordingGuards.shouldAutoStop(elapsedMillis = 100, capMillis = 100))
        assertTrue(RecordingGuards.shouldAutoStop(elapsedMillis = 101, capMillis = 100))
    }

    @Test fun hasEnoughFreeSpace_falseBelowMinimum() {
        assertTrue(RecordingGuards.hasEnoughFreeSpace(freeBytes = 100, minBytes = 100))
        assertFalse(RecordingGuards.hasEnoughFreeSpace(freeBytes = 99, minBytes = 100))
    }
}
