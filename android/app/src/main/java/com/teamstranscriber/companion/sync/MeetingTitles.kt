package com.teamstranscriber.companion.sync

import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

private val TITLE_TIME: DateTimeFormatter =
    DateTimeFormatter.ofPattern("MMM d, h:mm a", Locale.US)

/** A human default title (the phone offers no title entry); desktop uses it as display_title. */
fun defaultTitle(source: RecordingSource, startedAtEpochMillis: Long, zone: ZoneId): String {
    val label = when (source) {
        RecordingSource.TEAMS_CALL -> "Teams call"
        RecordingSource.IN_PERSON -> "In-person meeting"
        RecordingSource.MEMO -> "Voice memo"
    }
    val whenLocal = TITLE_TIME.format(Instant.ofEpochMilli(startedAtEpochMillis).atZone(zone))
    return "$label — $whenLocal"
}
