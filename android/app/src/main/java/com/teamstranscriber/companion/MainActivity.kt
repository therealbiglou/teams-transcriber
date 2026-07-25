package com.teamstranscriber.companion

import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.core.content.ContextCompat
import com.teamstranscriber.companion.permissions.Permissions
import com.teamstranscriber.companion.recording.RecordingService
import com.teamstranscriber.companion.settings.AppPrefs
import com.teamstranscriber.companion.ui.RecorderScreen

class MainActivity : ComponentActivity() {
    private val requestPermissions =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Surface {
                    RecorderScreen(
                        onRequestPermissions = ::requestRuntimePermissions,
                        onOpenAllFilesAccess = ::openAllFilesAccess,
                        onArm = { RecordingService.arm(this) },
                        onDisarm = { RecordingService.disarm(this) },
                    )
                }
            }
        }
    }

    override fun onStart() {
        super.onStart()
        // Re-arm after process death (or first launch) from the foreground — eligible here,
        // and idempotent via the `if (armed) return` guard on a fresh RecordingService.
        if (AppPrefs(this).autoRecordEnabled) RecordingService.arm(this)
    }

    private fun requestRuntimePermissions() {
        val granted = buildSet {
            listOf(
                android.Manifest.permission.RECORD_AUDIO,
                android.Manifest.permission.POST_NOTIFICATIONS,
            ).forEach {
                if (ContextCompat.checkSelfPermission(this@MainActivity, it) ==
                    PackageManager.PERMISSION_GRANTED
                ) add(it)
            }
        }
        val missing = Permissions.missingRuntimePermissions(granted, Build.VERSION.SDK_INT)
        if (missing.isNotEmpty()) requestPermissions.launch(missing.toTypedArray())
    }

    private fun openAllFilesAccess() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            startActivity(
                Intent(
                    Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                    Uri.parse("package:$packageName"),
                ),
            )
        }
    }
}
