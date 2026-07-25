package com.teamstranscriber.companion.sync

import java.time.Instant
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

// Lowercase 'xxx' always renders the offset as "+00:00" (uppercase 'XXX' would
// render UTC as "Z", which the desktop rejects). No fractional seconds.
private val ISO_UTC: DateTimeFormatter =
    DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ssxxx")

/** ISO-8601 UTC timestamp with an explicit `+00:00` offset (never `Z`). */
fun isoUtc(epochMillis: Long): String =
    ISO_UTC.format(Instant.ofEpochMilli(epochMillis).atOffset(ZoneOffset.UTC))
