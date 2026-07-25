package com.teamstranscriber.companion.permissions

import android.Manifest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PermissionsTest {
    @Test fun onApi33Plus_recordAudioAndPostNotifications_whenNoneGranted() {
        val missing = Permissions.missingRuntimePermissions(granted = emptySet(), sdkInt = 33)
        assertEquals(
            listOf(Manifest.permission.RECORD_AUDIO, Manifest.permission.POST_NOTIFICATIONS),
            missing,
        )
    }

    @Test fun belowApi33_postNotificationsNotRequired() {
        val missing = Permissions.missingRuntimePermissions(granted = emptySet(), sdkInt = 29)
        assertEquals(listOf(Manifest.permission.RECORD_AUDIO), missing)
    }

    @Test fun grantedPermissionsAreExcluded() {
        val missing = Permissions.missingRuntimePermissions(
            granted = setOf(Manifest.permission.RECORD_AUDIO), sdkInt = 29,
        )
        assertTrue(missing.isEmpty())
    }
}
