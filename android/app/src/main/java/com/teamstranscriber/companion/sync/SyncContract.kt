package com.teamstranscriber.companion.sync

/**
 * Shared sync-contract constants and filename helpers, mirroring the desktop
 * `phone_sync` contract (see docs/superpowers/specs/2026-07-14-android-companion-design.md).
 * The phone writes recordings + sidecars into [OUTBOX] under [ROOT] on shared storage.
 */
object SyncContract {
    /** Folder on the phone's shared storage the desktop syncs against. */
    const val ROOT: String = "Documents/TeamsTranscriber"

    /** Phone -> desktop: finished recordings + sidecars land here. */
    const val OUTBOX: String = "outbox"

    /** Recording audio filename for a given recording uid (AAC mono `.m4a`). */
    fun recordingFileName(uid: String): String = "rec_$uid.m4a"

    /** Sidecar JSON filename paired with [recordingFileName] for the same uid. */
    fun sidecarFileName(uid: String): String = "rec_$uid.json"
}
