package com.teamstranscriber.companion.sync

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class UidTest {
    @Test fun newUid_is12LowercaseHexChars() {
        val uid = newUid()
        assertEquals(12, uid.length)
        assertTrue(uid.matches(Regex("[0-9a-f]{12}")))
    }

    @Test fun newUid_isReasonablyUnique() {
        assertEquals(500, (1..500).map { newUid() }.toSet().size)
    }

    @Test fun isValidUid_acceptsGoodRejectsBad() {
        assertTrue(isValidUid("0123456789ab"))
        assertFalse(isValidUid("XYZ"))
        assertFalse(isValidUid("0123456789ABCD"))
    }
}
