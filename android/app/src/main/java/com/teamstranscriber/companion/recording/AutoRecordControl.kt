package com.teamstranscriber.companion.recording

import com.teamstranscriber.companion.sync.RecordingSource
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow

/**
 * In-process command channel from the background NotificationListenerService to the
 * already-running (foreground, armed) RecordingService. The listener MUST NOT start the
 * service from the background (Android 14+ blocks a background mic-FGS start); it emits a
 * command that the armed service — collecting this flow in its own scope — acts on.
 * Commands emitted while no armed service is collecting are dropped (no replay): correct,
 * since only an armed, foreground service should ever capture.
 */
sealed interface AutoRecordCommand {
    data class BeginCapture(val source: RecordingSource) : AutoRecordCommand
    data object StopCapture : AutoRecordCommand
}

object AutoRecordControl {
    private val _commands = MutableSharedFlow<AutoRecordCommand>(extraBufferCapacity = 8)
    val commands: SharedFlow<AutoRecordCommand> = _commands

    fun requestBeginCapture(source: RecordingSource) {
        _commands.tryEmit(AutoRecordCommand.BeginCapture(source))
    }
    fun requestStopCapture() {
        _commands.tryEmit(AutoRecordCommand.StopCapture)
    }
}
