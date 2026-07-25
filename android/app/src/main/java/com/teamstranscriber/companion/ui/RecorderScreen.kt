package com.teamstranscriber.companion.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.teamstranscriber.companion.recording.RecordingBus
import com.teamstranscriber.companion.recording.RecordingService
import com.teamstranscriber.companion.recording.formatElapsed
import com.teamstranscriber.companion.settings.AppPrefs
import com.teamstranscriber.companion.sync.RecordingSource

@Composable
fun RecorderScreen(onRequestPermissions: () -> Unit, onOpenAllFilesAccess: () -> Unit) {
    val context = LocalContext.current
    val status by RecordingBus.state.collectAsState()
    val prefs = remember { AppPrefs(context) }
    var autoRecord by remember { mutableStateOf(prefs.autoRecordEnabled) }
    var pickingSource by remember { mutableStateOf(false) }

    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(16.dp, Alignment.CenterVertically),
    ) {
        if (status.active) {
            val elapsed = (System.currentTimeMillis() - (status.startedAtEpochMillis ?: 0L))
            Text("Recording ${status.source?.name ?: ""}")
            Text(formatElapsed(elapsed))
            Button(onClick = { RecordingService.stop(context) }) { Text("Stop") }
        } else if (pickingSource) {
            Text("What are you recording?")
            Button(onClick = {
                pickingSource = false
                RecordingService.start(context, RecordingSource.IN_PERSON)
            }) { Text("In-person meeting") }
            Button(onClick = {
                pickingSource = false
                RecordingService.start(context, RecordingSource.MEMO)
            }) { Text("Voice memo") }
            OutlinedButton(onClick = { pickingSource = false }) { Text("Cancel") }
        } else {
            Button(onClick = { pickingSource = true }) { Text("Record") }
        }

        Text("Auto-record Teams calls")
        Switch(checked = autoRecord, onCheckedChange = {
            autoRecord = it; prefs.autoRecordEnabled = it
        })
        OutlinedButton(onClick = onOpenAllFilesAccess) { Text("Grant file access") }
        OutlinedButton(onClick = onRequestPermissions) { Text("Grant mic / notifications") }
    }
}
