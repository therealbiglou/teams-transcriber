package com.teamstranscriber.companion.recording

import org.junit.Assert.assertEquals
import org.junit.Test

class ElapsedFormatterTest {
    @Test fun underAnHour_isMinuteColonSeconds() {
        assertEquals("0:00", formatElapsed(0))
        assertEquals("0:09", formatElapsed(9_000))
        assertEquals("1:05", formatElapsed(65_000))
    }
    @Test fun overAnHour_isHourMinuteSeconds() {
        assertEquals("1:02:03", formatElapsed(3_723_000))
    }
    @Test fun negativeClampsToZero() {
        assertEquals("0:00", formatElapsed(-5_000))
    }
}
