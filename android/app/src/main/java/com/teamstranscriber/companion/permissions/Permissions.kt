package com.teamstranscriber.companion.permissions

import android.Manifest
import android.os.Build

object Permissions {
    /** Dialog-grantable permissions the recorder needs (special grants handled elsewhere). */
    fun missingRuntimePermissions(granted: Set<String>, sdkInt: Int): List<String> {
        val needed = buildList {
            add(Manifest.permission.RECORD_AUDIO)
            if (sdkInt >= Build.VERSION_CODES.TIRAMISU) add(Manifest.permission.POST_NOTIFICATIONS)
        }
        return needed.filter { it !in granted }
    }
}
