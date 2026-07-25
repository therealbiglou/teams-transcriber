package com.teamstranscriber.companion.sync

/** Recording origin; [wire] is the exact value written to the sidecar `source` field. */
enum class RecordingSource(val wire: String) {
    TEAMS_CALL("teams_call"),
    IN_PERSON("in_person"),
    MEMO("memo"),
}
