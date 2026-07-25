package com.teamstranscriber.companion.sync

import java.io.File

data class OutboxFiles(val audio: File, val sidecar: File)

/**
 * Writes a finished recording + its sidecar into [outboxDir] as an atomic pair.
 * Audio is moved into place first; the sidecar is written to a `.tmp` then
 * renamed, so the desktop only ever sees a complete `.json` next to its `.m4a`.
 */
class OutboxWriter(private val outboxDir: File) {

    fun write(finishedAudio: File, sidecar: Sidecar): OutboxFiles {
        require(isValidUid(sidecar.uid)) { "invalid uid: ${sidecar.uid}" }
        outboxDir.mkdirs()

        val audioDest = File(outboxDir, SyncContract.recordingFileName(sidecar.uid))
        if (audioDest.exists()) audioDest.delete()
        if (!finishedAudio.renameTo(audioDest)) {
            // renameTo can fail across mount points; fall back to copy + delete.
            finishedAudio.copyTo(audioDest, overwrite = true)
            finishedAudio.delete()
        }

        val sidecarDest = File(outboxDir, SyncContract.sidecarFileName(sidecar.uid))
        val sidecarTmp = File(outboxDir, "${SyncContract.sidecarFileName(sidecar.uid)}.tmp")
        sidecarTmp.writeText(sidecar.toJson())
        if (sidecarDest.exists()) sidecarDest.delete()
        check(sidecarTmp.renameTo(sidecarDest)) { "could not finalize sidecar for ${sidecar.uid}" }

        return OutboxFiles(audio = audioDest, sidecar = sidecarDest)
    }
}
