package com.teamstranscriber.companion.storage

import android.os.Build
import android.os.Environment
import com.teamstranscriber.companion.sync.SyncContract
import java.io.File

object Storage {
    /** `…/Documents/TeamsTranscriber/outbox`, created if missing. */
    fun outboxDir(): File {
        val root = File(Environment.getExternalStorageDirectory(), SyncContract.ROOT)
        return File(root, SyncContract.OUTBOX).apply { mkdirs() }
    }

    /** True once the user has granted All-files access (or on pre-R where it's implicit). */
    fun hasAllFilesAccess(): Boolean =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) Environment.isExternalStorageManager()
        else true
}
