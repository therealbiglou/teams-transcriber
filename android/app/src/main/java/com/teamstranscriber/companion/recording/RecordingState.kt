package com.teamstranscriber.companion.recording

import com.teamstranscriber.companion.sync.RecordingSource
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

data class RecordingStatus(
    val active: Boolean = false,
    val source: RecordingSource? = null,
    val startedAtEpochMillis: Long? = null,
)

/** Process-wide recording state the UI observes; the service is the only writer. */
object RecordingBus {
    private val _state = MutableStateFlow(RecordingStatus())
    val state: StateFlow<RecordingStatus> = _state.asStateFlow()
    internal fun set(status: RecordingStatus) { _state.value = status }
}
