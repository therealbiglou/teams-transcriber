package com.teamstranscriber.companion.sync

import org.junit.Assert.assertEquals
import org.junit.Test

class TimestampsTest {
    // 2026-07-25T14:30:00Z == 1784989800000 ms
    @Test fun isoUtc_usesPlusZeroOffsetNotZ_andNoFractionalSeconds() {
        assertEquals("2026-07-25T14:30:00+00:00", isoUtc(1784989800000L))
    }

    @Test fun isoUtc_isLexicographicallyOrderedWithChronology() {
        assertEquals(true, isoUtc(1000L) < isoUtc(2000L))
    }
}
