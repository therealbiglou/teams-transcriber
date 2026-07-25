package com.teamstranscriber.companion.sync

import java.io.File
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class OutboxWriterTest {
    @get:Rule val tmp = TemporaryFolder()

    private fun sidecar(uid: String) = Sidecar(
        uid = uid, title = "t", source = RecordingSource.MEMO,
        startedAtEpochMillis = 1785076200000L, endedAtEpochMillis = 1785076260000L,
        appVersion = "0.1.0",
    )

    @Test fun write_producesNamedPair_andRemovesSourceAndTemp() {
        val outbox = File(tmp.root, "outbox")
        val audio = tmp.newFile("capture.m4a").apply { writeBytes(byteArrayOf(1, 2, 3)) }

        val files = OutboxWriter(outbox).write(audio, sidecar("0123456789ab"))

        assertEquals("rec_0123456789ab.m4a", files.audio.name)
        assertEquals("rec_0123456789ab.json", files.sidecar.name)
        assertTrue(files.audio.exists())
        assertTrue(files.sidecar.exists())
        assertArrayEquals(byteArrayOf(1, 2, 3), files.audio.readBytes())
        assertFalse("source temp audio should be moved, not copied", audio.exists())
        assertFalse("no .tmp left behind", File(outbox, "rec_0123456789ab.json.tmp").exists())
    }

    @Test fun write_sidecarContentMatchesSidecarToJson() {
        val outbox = File(tmp.root, "outbox")
        val audio = tmp.newFile("c.m4a")
        val sc = sidecar("aaaaaaaaaaaa")
        val files = OutboxWriter(outbox).write(audio, sc)
        assertEquals(sc.toJson(), files.sidecar.readText())
    }

    @Test fun write_createsOutboxDirIfMissing() {
        val outbox = File(tmp.root, "nested/outbox")
        assertFalse(outbox.exists())
        OutboxWriter(outbox).write(tmp.newFile("c.m4a"), sidecar("bbbbbbbbbbbb"))
        assertTrue(outbox.isDirectory)
    }
}
