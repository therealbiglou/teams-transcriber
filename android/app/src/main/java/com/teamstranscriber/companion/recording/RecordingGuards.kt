package com.teamstranscriber.companion.recording

/** Guards against runaway auto-recording (spec: max-duration cap + free-storage check). */
object RecordingGuards {
    /** A stuck Teams-call notification must not record forever. */
    const val MAX_DURATION_MS: Long = 4L * 60 * 60 * 1000 // 4 hours

    /** Refuse to start a recording with less than this much free space. */
    const val MIN_FREE_BYTES: Long = 200L * 1024 * 1024 // 200 MB

    fun shouldAutoStop(elapsedMillis: Long, capMillis: Long = MAX_DURATION_MS): Boolean =
        elapsedMillis >= capMillis

    fun hasEnoughFreeSpace(freeBytes: Long, minBytes: Long = MIN_FREE_BYTES): Boolean =
        freeBytes >= minBytes
}
