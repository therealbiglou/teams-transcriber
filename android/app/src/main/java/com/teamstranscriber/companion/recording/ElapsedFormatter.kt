package com.teamstranscriber.companion.recording

/** `M:SS` under an hour, `H:MM:SS` at/above; negatives clamp to `0:00`. */
fun formatElapsed(millis: Long): String {
    val total = (millis.coerceAtLeast(0)) / 1000
    val h = total / 3600
    val m = (total % 3600) / 60
    val s = total % 60
    return if (h > 0) "%d:%02d:%02d".format(h, m, s) else "%d:%02d".format(m, s)
}
