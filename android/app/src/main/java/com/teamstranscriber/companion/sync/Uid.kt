package com.teamstranscriber.companion.sync

import java.util.UUID

private val UID_RE = Regex("[0-9a-f]{12}")

/** 12 lowercase hex chars — compact, collision-safe for one phone's recordings. */
fun newUid(): String = UUID.randomUUID().toString().replace("-", "").substring(0, 12)

fun isValidUid(value: String): Boolean = UID_RE.matches(value)
