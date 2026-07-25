package com.teamstranscriber.companion.permissions

import android.Manifest
import android.os.Build

object Permissions {
    /** Dialog-grantable permissions the recorder needs (special grants handled elsewhere). */
    val RUNTIME_FOR_RECORDING: List<String> = buildList {
        add(Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) add(Manifest.permission.POST_NOTIFICATIONS)
    }

    /** Dialog-grantable permissions the recorder needs (special grants handled elsewhere). */
    fun missingRuntimePermissions(granted: Set<String>, sdkInt: Int): List<String> {
        val needed = buildList {
            add(Manifest.permission.RECORD_AUDIO)
            if (sdkInt >= Build.VERSION_CODES.TIRAMISU) add(Manifest.permission.POST_NOTIFICATIONS)
        }
        return needed.filter { it !in granted }
    }
}
