package com.teamstranscriber.companion.sync

import org.json.JSONObject

/**
 * The `rec_<uid>.json` sidecar paired with the recording. [toJson] emits exactly
 * the frozen contract keys; timestamps go through [isoUtc]; a still-open recording
 * (null [endedAtEpochMillis]) serializes `ended_at`/`duration_ms` as JSON null.
 */
data class Sidecar(
    val uid: String,
    val title: String,
    val source: RecordingSource,
    val startedAtEpochMillis: Long,
    val endedAtEpochMillis: Long?,
    val appVersion: String,
) {
    fun durationMs(): Long? = endedAtEpochMillis?.let { it - startedAtEpochMillis }

    fun toJson(): String {
        val o = JSONObject()
        o.put("uid", uid)
        o.put("title", title)
        o.put("source", source.wire)
        o.put("started_at", isoUtc(startedAtEpochMillis))
        o.put("ended_at", endedAtEpochMillis?.let { isoUtc(it) } ?: JSONObject.NULL)
        o.put("duration_ms", durationMs() ?: JSONObject.NULL)
        o.put("app_version", appVersion)
        return o.toString()
    }
}
