package com.teamstranscriber.companion.recording

import com.teamstranscriber.companion.sync.RecordingSource
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class AutoRecordControlTest {
    @Test
    fun commands_deliveredInOrder_toActiveCollector() = runTest {
        val received = mutableListOf<AutoRecordCommand>()
        val collector = launch {
            AutoRecordControl.commands.collect { received.add(it) }
        }
        runCurrent() // let the collector reach its suspension point before we emit

        AutoRecordControl.requestBeginCapture(RecordingSource.TEAMS_CALL)
        AutoRecordControl.requestStopCapture()
        runCurrent() // let the (now-resumed) collector process both emissions

        collector.cancel()

        assertEquals(
            listOf(
                AutoRecordCommand.BeginCapture(RecordingSource.TEAMS_CALL),
                AutoRecordCommand.StopCapture,
            ),
            received,
        )
    }
}
