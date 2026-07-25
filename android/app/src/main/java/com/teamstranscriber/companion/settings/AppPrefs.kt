package com.teamstranscriber.companion.settings

import android.content.Context

/** One-key preference store: whether Teams calls auto-record. */
class AppPrefs(context: Context) {
    private val prefs = context.getSharedPreferences("tt_companion", Context.MODE_PRIVATE)

    var autoRecordEnabled: Boolean
        get() = prefs.getBoolean(KEY_AUTO_RECORD, false)
        set(value) { prefs.edit().putBoolean(KEY_AUTO_RECORD, value).apply() }

    private companion object { const val KEY_AUTO_RECORD = "auto_record_enabled" }
}
