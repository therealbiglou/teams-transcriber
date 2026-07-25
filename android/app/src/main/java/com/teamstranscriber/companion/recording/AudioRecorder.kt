package com.teamstranscriber.companion.recording

import android.media.MediaRecorder
import android.os.Build
import java.io.File

/** Thin MediaRecorder wrapper: AAC mono 16 kHz ~64 kbps into an .m4a temp file. */
class AudioRecorder(private val outputFile: File) {
    private var recorder: MediaRecorder? = null

    fun start(context: android.content.Context) {
        val r = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) MediaRecorder(context)
        else @Suppress("DEPRECATION") MediaRecorder()
        r.setAudioSource(MediaRecorder.AudioSource.MIC)
        r.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
        r.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
        r.setAudioChannels(1)
        r.setAudioSamplingRate(16_000)
        r.setAudioEncodingBitRate(64_000)
        r.setOutputFile(outputFile.absolutePath)
        try {
            r.prepare()
            r.start()
        } catch (e: Exception) {
            r.release()
            throw e
        }
        recorder = r
    }

    /** Stops and releases; returns true if a valid file was produced. */
    fun stop(): Boolean {
        val r = recorder ?: return false
        recorder = null
        return try {
            r.stop(); true
        } catch (e: RuntimeException) {
            // stop() throws if stopped too quickly (no frames) — treat as no-recording.
            outputFile.delete(); false
        } finally {
            r.release()
        }
    }
}
