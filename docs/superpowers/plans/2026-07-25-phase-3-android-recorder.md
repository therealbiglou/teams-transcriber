# Phase 3: Android Recorder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Android companion's recorder — one-tap manual recording (in-person / voice memo) and automatic Teams-call recording — writing each finished recording plus its sidecar into the phone's `Documents/TeamsTranscriber/outbox/` for the desktop to pull over USB.

**Architecture:** A Kotlin + Jetpack Compose app (already scaffolded in `android/`). A pure, JVM-unit-tested "sync contract" layer (source enum, timestamps, uid, sidecar JSON, default titles, outbox writer) is the foundation. On top sit Android-framework components — a foreground `RecordingService` wrapping `MediaRecorder`, a `TeamsCallWatcher` `NotificationListenerService` for auto-record, and a Compose recorder screen. Framework code is kept thin; all decision logic is extracted into pure functions so it is unit-testable. Files are written to shared storage via the plain `File` API under **All-files access** (`MANAGE_EXTERNAL_STORAGE`).

**Tech Stack:** Kotlin 2.0.21, Jetpack Compose (BOM 2024.09.00, material3), AGP 8.6.1, Gradle 8.9 (wrapper committed), min SDK 29 / target+compile SDK 34, JUnit 4 for unit tests, `org.json` (bundled in Android) for sidecar JSON, `java.time` for timestamps.

## Global Constraints

- **Package:** `com.teamstranscriber.companion`. Outbox lives at `File(Environment.getExternalStorageDirectory(), "Documents/TeamsTranscriber/outbox")` — the exact path the desktop MTP watcher reads.
- **Sync contract is frozen at `schema_version = 1`** (see `docs/superpowers/specs/2026-07-14-android-companion-design.md`). Do not invent new fields or rename existing ones.
- **Recording files:** `outbox/rec_<uid>.m4a` (AAC mono, ~64 kbps) paired with `outbox/rec_<uid>.json`.
- **Sidecar JSON keys, exactly:** `uid`, `title`, `source`, `started_at`, `ended_at`, `duration_ms`, `app_version`.
- **`source` wire values, exactly:** `teams_call` | `in_person` | `memo`.
- **Every timestamp** (`started_at`, `ended_at`) is ISO-8601 UTC with an explicit `+00:00` offset — **never `Z`** — and no fractional seconds: `2026-07-25T14:30:00+00:00`. The desktop rejects naive/unparseable timestamps.
- **Write audio fully before the sidecar; write each via a temp file + atomic rename.** The sidecar's arrival signals a complete pair; a mid-sync cable pull must never expose a half-written file.
- **All Android-framework decision logic must be extracted into pure functions** so it is JVM-unit-testable without Robolectric or a device.
- **Build/test on this machine:** from `android/`, `./gradlew testDebugUnitTest` (JUnit) and `./gradlew assembleDebug` (APK). Always run gradle with the proxy scrubbed (`$env:HTTPS_PROXY=$null; …` in PowerShell) — Claude Code's injected `HTTPS_PROXY` breaks Gradle downloads. JDK 17 + local SDK are already configured via `gradle.properties` / `local.properties`.
- **On-device verification** (recording, services, notification-listener) is done by the human against a checklist — those paths have no automated test. Every framework task ends with build-green + a concrete checklist addition.

---

## File Structure

Pure / JVM-unit-tested (`android/app/src/main/java/com/teamstranscriber/companion/sync/`):

| File | Responsibility |
|---|---|
| `SyncContract.kt` (exists) | Contract constants + filename helpers; extend with the outbox `File` resolver. |
| `RecordingSource.kt` | `enum RecordingSource(val wire: String)` → `TEAMS_CALL("teams_call")`, `IN_PERSON("in_person")`, `MEMO("memo")`. |
| `Timestamps.kt` | `fun isoUtc(epochMillis: Long): String` → `+00:00`, never `Z`, no fractional seconds. |
| `Uid.kt` | `fun newUid(): String` → 12 lowercase hex chars; `fun isValidUid(String): Boolean`. |
| `MeetingTitles.kt` | `fun defaultTitle(source, startedAtEpochMillis, zone): String` → e.g. `"In-person meeting — Jul 25, 2:30 PM"`. |
| `Sidecar.kt` | `data class Sidecar(...)` + `fun toJson(): String` producing the exact contract JSON. |
| `OutboxWriter.kt` | `class OutboxWriter(private val outboxDir: File)` with `fun write(finishedAudio: File, sidecar: Sidecar): OutboxFiles` — atomic move of audio + atomic write of sidecar. JVM-testable via a temp `outboxDir`. |

Android-framework (`android/app/src/main/java/com/teamstranscriber/companion/`):

| File | Responsibility |
|---|---|
| `recording/TeamsCallDetector.kt` | Pure predicate `fun isOngoingTeamsCall(pkg, category, isOngoing, title, text): Boolean`. Unit-tested. |
| `recording/ElapsedFormatter.kt` | Pure `fun formatElapsed(millis: Long): String` → `M:SS` / `H:MM:SS`. Unit-tested. |
| `recording/AudioRecorder.kt` | Thin `MediaRecorder` wrapper (AAC mono 16 kHz 64 kbps → temp `.m4a`). Framework. |
| `recording/RecordingService.kt` | Foreground service (`microphone` type): owns `AudioRecorder`, shows the elapsed+Stop notification, on stop assembles the `Sidecar` and calls `OutboxWriter`, publishes state. Framework. |
| `recording/RecordingState.kt` | `sealed`/`data` state + a process-wide `object RecordingBus` (`MutableStateFlow`) the UI observes. Mostly pure. |
| `recording/TeamsCallWatcher.kt` | `NotificationListenerService`; on post/remove, consult `TeamsCallDetector` + the auto-record pref, start/stop the service with `TEAMS_CALL`. Framework. |
| `settings/AppPrefs.kt` | `SharedPreferences` wrapper: `autoRecordEnabled`. Thin. |
| `storage/Storage.kt` | `fun outboxDir(): File` (creates it) + `fun hasAllFilesAccess(): Boolean`. Framework. |
| `permissions/Permissions.kt` | Pure `fun missingRuntimePermissions(granted: Set<String>, sdkInt: Int): List<String>` + intent helpers for the special grants. |
| `ui/RecorderScreen.kt` | Compose UI: record button, source picker, elapsed timer, auto-record toggle, permission call-to-action. Framework/Compose. |
| `MainActivity.kt` (exists) | Hosts `RecorderScreen`, drives runtime-permission requests. |
| `AndroidManifest.xml` (exists) | Permissions + `<service>` declarations. |

Tests mirror under `android/app/src/test/java/com/teamstranscriber/companion/…`.

---

### Task 1: Sync-contract pure layer (source, timestamps, uid, titles, sidecar)

**Files:**
- Create: `android/app/src/main/java/com/teamstranscriber/companion/sync/RecordingSource.kt`
- Create: `android/app/src/main/java/com/teamstranscriber/companion/sync/Timestamps.kt`
- Create: `android/app/src/main/java/com/teamstranscriber/companion/sync/Uid.kt`
- Create: `android/app/src/main/java/com/teamstranscriber/companion/sync/MeetingTitles.kt`
- Create: `android/app/src/main/java/com/teamstranscriber/companion/sync/Sidecar.kt`
- Modify: `android/app/src/main/java/com/teamstranscriber/companion/sync/SyncContract.kt` (add outbox `File` resolver import-free helper is in Task 3's `Storage`; here only add a `dir` constant if needed — leave as-is)
- Test: `android/app/src/test/java/com/teamstranscriber/companion/sync/TimestampsTest.kt`, `UidTest.kt`, `RecordingSourceTest.kt`, `MeetingTitlesTest.kt`, `SidecarTest.kt`

**Interfaces:**
- Produces:
  - `enum class RecordingSource(val wire: String) { TEAMS_CALL("teams_call"), IN_PERSON("in_person"), MEMO("memo") }`
  - `fun isoUtc(epochMillis: Long): String`
  - `fun newUid(): String`, `fun isValidUid(value: String): Boolean`
  - `fun defaultTitle(source: RecordingSource, startedAtEpochMillis: Long, zone: java.time.ZoneId): String`
  - `data class Sidecar(uid, title, source: RecordingSource, startedAtEpochMillis: Long, endedAtEpochMillis: Long?, appVersion: String)` with `fun durationMs(): Long?` and `fun toJson(): String`

- [ ] **Step 1: Write `RecordingSourceTest.kt`**

```kotlin
package com.teamstranscriber.companion.sync

import org.junit.Assert.assertEquals
import org.junit.Test

class RecordingSourceTest {
    @Test fun wireValues_matchContract() {
        assertEquals("teams_call", RecordingSource.TEAMS_CALL.wire)
        assertEquals("in_person", RecordingSource.IN_PERSON.wire)
        assertEquals("memo", RecordingSource.MEMO.wire)
    }
}
```

- [ ] **Step 2: Run it, expect FAIL** — `./gradlew testDebugUnitTest` → unresolved reference `RecordingSource`.

- [ ] **Step 3: Create `RecordingSource.kt`**

```kotlin
package com.teamstranscriber.companion.sync

/** Recording origin; [wire] is the exact value written to the sidecar `source` field. */
enum class RecordingSource(val wire: String) {
    TEAMS_CALL("teams_call"),
    IN_PERSON("in_person"),
    MEMO("memo"),
}
```

- [ ] **Step 4: Write `TimestampsTest.kt`**

```kotlin
package com.teamstranscriber.companion.sync

import org.junit.Assert.assertEquals
import org.junit.Test

class TimestampsTest {
    // 2026-07-25T14:30:00Z == 1785076200000 ms
    @Test fun isoUtc_usesPlusZeroOffsetNotZ_andNoFractionalSeconds() {
        assertEquals("2026-07-25T14:30:00+00:00", isoUtc(1785076200000L))
    }

    @Test fun isoUtc_isLexicographicallyOrderedWithChronology() {
        assertEquals(true, isoUtc(1000L) < isoUtc(2000L))
    }
}
```

- [ ] **Step 5: Run it, expect FAIL** — unresolved reference `isoUtc`.

- [ ] **Step 6: Create `Timestamps.kt`**

```kotlin
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
```

- [ ] **Step 7: Run the two test classes, expect PASS.**

- [ ] **Step 8: Write `UidTest.kt`**

```kotlin
package com.teamstranscriber.companion.sync

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class UidTest {
    @Test fun newUid_is12LowercaseHexChars() {
        val uid = newUid()
        assertEquals(12, uid.length)
        assertTrue(uid.matches(Regex("[0-9a-f]{12}")))
    }

    @Test fun newUid_isReasonablyUnique() {
        assertEquals(500, (1..500).map { newUid() }.toSet().size)
    }

    @Test fun isValidUid_acceptsGoodRejectsBad() {
        assertTrue(isValidUid("0123456789ab"))
        assertFalse(isValidUid("XYZ"))
        assertFalse(isValidUid("0123456789ABCD"))
    }
}
```

- [ ] **Step 9: Run it, expect FAIL.**

- [ ] **Step 10: Create `Uid.kt`**

```kotlin
package com.teamstranscriber.companion.sync

import java.util.UUID

private val UID_RE = Regex("[0-9a-f]{12}")

/** 12 lowercase hex chars — compact, collision-safe for one phone's recordings. */
fun newUid(): String = UUID.randomUUID().toString().replace("-", "").substring(0, 12)

fun isValidUid(value: String): Boolean = UID_RE.matches(value)
```

- [ ] **Step 11: Run it, expect PASS.**

- [ ] **Step 12: Write `MeetingTitlesTest.kt`**

```kotlin
package com.teamstranscriber.companion.sync

import java.time.ZoneId
import org.junit.Assert.assertEquals
import org.junit.Test

class MeetingTitlesTest {
    private val ny = ZoneId.of("America/New_York")

    // 2026-07-25T14:30:00Z == 10:30 AM in New York (EDT)
    @Test fun defaultTitle_inPerson_hasLabelAndLocalTime() {
        assertEquals(
            "In-person meeting — Jul 25, 10:30 AM",
            defaultTitle(RecordingSource.IN_PERSON, 1785076200000L, ny),
        )
    }

    @Test fun defaultTitle_memo_andTeamsCall_haveTheirLabels() {
        assertEquals(
            "Voice memo — Jul 25, 10:30 AM",
            defaultTitle(RecordingSource.MEMO, 1785076200000L, ny),
        )
        assertEquals(
            "Teams call — Jul 25, 10:30 AM",
            defaultTitle(RecordingSource.TEAMS_CALL, 1785076200000L, ny),
        )
    }
}
```

- [ ] **Step 13: Run it, expect FAIL.**

- [ ] **Step 14: Create `MeetingTitles.kt`**

```kotlin
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
```

- [ ] **Step 15: Run it, expect PASS.**

- [ ] **Step 16: Write `SidecarTest.kt`** (replace the placeholder from the scaffold spike; keep the `SyncContractTest.kt` filename-helper tests as they are)

```kotlin
package com.teamstranscriber.companion.sync

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SidecarTest {
    private fun sample(ended: Long? = 1785076260000L) = Sidecar(
        uid = "0123456789ab",
        title = "In-person meeting — Jul 25, 10:30 AM",
        source = RecordingSource.IN_PERSON,
        startedAtEpochMillis = 1785076200000L, // 2026-07-25T14:30:00Z
        endedAtEpochMillis = ended,            // +60s
        appVersion = "0.1.0",
    )

    @Test fun toJson_hasExactlyTheContractKeys() {
        val o = JSONObject(sample().toJson())
        assertEquals(
            setOf("uid", "title", "source", "started_at", "ended_at", "duration_ms", "app_version"),
            o.keys().asSequence().toSet(),
        )
    }

    @Test fun toJson_serializesValuesPerContract() {
        val o = JSONObject(sample().toJson())
        assertEquals("0123456789ab", o.getString("uid"))
        assertEquals("in_person", o.getString("source"))
        assertEquals("2026-07-25T14:30:00+00:00", o.getString("started_at"))
        assertEquals("2026-07-25T14:31:00+00:00", o.getString("ended_at"))
        assertEquals(60000L, o.getLong("duration_ms"))
        assertEquals("0.1.0", o.getString("app_version"))
    }

    @Test fun toJson_nullEndedGivesJsonNullDurationAndEnded() {
        val o = JSONObject(sample(ended = null).toJson())
        assertTrue(o.isNull("ended_at"))
        assertTrue(o.isNull("duration_ms"))
    }
}
```

- [ ] **Step 17: Run it, expect FAIL.**

- [ ] **Step 18: Create `Sidecar.kt`**

```kotlin
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
```

- [ ] **Step 19: Run the full unit-test task, expect PASS**

Run (PowerShell, proxy scrubbed): `$env:HTTPS_PROXY=$null; $env:HTTP_PROXY=$null; & .\gradlew.bat -p . testDebugUnitTest --console=plain`
Expected: all sync tests green (RecordingSource, Timestamps, Uid, MeetingTitles, Sidecar, plus the pre-existing SyncContract).

- [ ] **Step 20: Commit**

```bash
git add android/app/src/main/java/com/teamstranscriber/companion/sync android/app/src/test/java/com/teamstranscriber/companion/sync
git commit -m "feat(android): sync-contract pure layer (source, timestamp, uid, title, sidecar)"
```

---

### Task 2: OutboxWriter — atomic pair writing

**Files:**
- Create: `android/app/src/main/java/com/teamstranscriber/companion/sync/OutboxWriter.kt`
- Test: `android/app/src/test/java/com/teamstranscriber/companion/sync/OutboxWriterTest.kt`

**Interfaces:**
- Consumes: `Sidecar`, `SyncContract.recordingFileName/sidecarFileName`.
- Produces:
  - `data class OutboxFiles(val audio: File, val sidecar: File)`
  - `class OutboxWriter(private val outboxDir: File) { fun write(finishedAudio: File, sidecar: Sidecar): OutboxFiles }`
  - Contract: creates `outboxDir` if missing; moves `finishedAudio` → `rec_<uid>.m4a`; writes sidecar to `rec_<uid>.json.tmp` then renames to `rec_<uid>.json` (audio in place first, sidecar last, so the sidecar's appearance means the pair is complete); leaves no `.tmp` behind on success.

- [ ] **Step 1: Write `OutboxWriterTest.kt`**

```kotlin
package com.teamstranscriber.companion.sync

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class OutboxWriterTest {
    @get:Rule val tmp = TemporaryFolder()

    private fun sidecar(uid: String) = Sidecar(
        uid = uid, title = "t", source = RecordingSource.MEMO,
        startedAtEpochMillis = 1785076200000L, endedAtEpochMillis = 1785076260000L,
        appVersion = "0.1.0",
    )

    @Test fun write_producesNamedPair_andRemovesSourceAndTemp() {
        val outbox = File(tmp.root, "outbox")
        val audio = tmp.newFile("capture.m4a").apply { writeBytes(byteArrayOf(1, 2, 3)) }

        val files = OutboxWriter(outbox).write(audio, sidecar("0123456789ab"))

        assertEquals("rec_0123456789ab.m4a", files.audio.name)
        assertEquals("rec_0123456789ab.json", files.sidecar.name)
        assertTrue(files.audio.exists())
        assertTrue(files.sidecar.exists())
        assertArrayEquals(byteArrayOf(1, 2, 3), files.audio.readBytes())
        assertFalse("source temp audio should be moved, not copied", audio.exists())
        assertFalse("no .tmp left behind", File(outbox, "rec_0123456789ab.json.tmp").exists())
    }

    @Test fun write_sidecarContentMatchesSidecarToJson() {
        val outbox = File(tmp.root, "outbox")
        val audio = tmp.newFile("c.m4a")
        val sc = sidecar("aaaaaaaaaaaa")
        val files = OutboxWriter(outbox).write(audio, sc)
        assertEquals(sc.toJson(), files.sidecar.readText())
    }

    @Test fun write_createsOutboxDirIfMissing() {
        val outbox = File(tmp.root, "nested/outbox")
        assertFalse(outbox.exists())
        OutboxWriter(outbox).write(tmp.newFile("c.m4a"), sidecar("bbbbbbbbbbbb"))
        assertTrue(outbox.isDirectory)
    }
}
```
(Add `import org.junit.Assert.assertArrayEquals`.)

- [ ] **Step 2: Run it, expect FAIL** — unresolved reference `OutboxWriter`.

- [ ] **Step 3: Create `OutboxWriter.kt`**

```kotlin
package com.teamstranscriber.companion.sync

import java.io.File

data class OutboxFiles(val audio: File, val sidecar: File)

/**
 * Writes a finished recording + its sidecar into [outboxDir] as an atomic pair.
 * Audio is moved into place first; the sidecar is written to a `.tmp` then
 * renamed, so the desktop only ever sees a complete `.json` next to its `.m4a`.
 */
class OutboxWriter(private val outboxDir: File) {

    fun write(finishedAudio: File, sidecar: Sidecar): OutboxFiles {
        require(isValidUid(sidecar.uid)) { "invalid uid: ${sidecar.uid}" }
        outboxDir.mkdirs()

        val audioDest = File(outboxDir, SyncContract.recordingFileName(sidecar.uid))
        if (audioDest.exists()) audioDest.delete()
        if (!finishedAudio.renameTo(audioDest)) {
            // renameTo can fail across mount points; fall back to copy + delete.
            finishedAudio.copyTo(audioDest, overwrite = true)
            finishedAudio.delete()
        }

        val sidecarDest = File(outboxDir, SyncContract.sidecarFileName(sidecar.uid))
        val sidecarTmp = File(outboxDir, "${SyncContract.sidecarFileName(sidecar.uid)}.tmp")
        sidecarTmp.writeText(sidecar.toJson())
        if (sidecarDest.exists()) sidecarDest.delete()
        check(sidecarTmp.renameTo(sidecarDest)) { "could not finalize sidecar for ${sidecar.uid}" }

        return OutboxFiles(audio = audioDest, sidecar = sidecarDest)
    }
}
```

- [ ] **Step 4: Run it, expect PASS.**

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/teamstranscriber/companion/sync/OutboxWriter.kt android/app/src/test/java/com/teamstranscriber/companion/sync/OutboxWriterTest.kt
git commit -m "feat(android): OutboxWriter atomic recording+sidecar pair writing"
```

---

### Task 3: Manifest, permissions plumbing, storage resolver

**Files:**
- Modify: `android/app/src/main/AndroidManifest.xml`
- Modify: `android/app/build.gradle.kts` (enable `buildConfig` so `BuildConfig.VERSION_NAME` exists)
- Create: `android/app/src/main/java/com/teamstranscriber/companion/permissions/Permissions.kt`
- Create: `android/app/src/main/java/com/teamstranscriber/companion/storage/Storage.kt`
- Test: `android/app/src/test/java/com/teamstranscriber/companion/permissions/PermissionsTest.kt`

**Interfaces:**
- Produces:
  - `object Permissions { fun missingRuntimePermissions(granted: Set<String>, sdkInt: Int): List<String>; val RUNTIME_FOR_RECORDING: List<String> }`
  - `object Storage { fun outboxDir(): File; fun hasAllFilesAccess(): Boolean }`
  - `BuildConfig.VERSION_NAME` (from `buildConfig = true`).

**Notes for the implementer:** `MANAGE_EXTERNAL_STORAGE` and notification-listener access are *special* grants — they cannot be requested via the runtime-permission dialog; the app deep-links the user to the relevant Settings screen (done in the UI task). `missingRuntimePermissions` covers only the dialog-grantable ones (`RECORD_AUDIO`; `POST_NOTIFICATIONS` on API ≥ 33).

- [ ] **Step 1: Enable `buildConfig` in `android/app/build.gradle.kts`** — add to the `buildFeatures` block:

```kotlin
    buildFeatures {
        compose = true
        buildConfig = true
    }
```

- [ ] **Step 2: Write `PermissionsTest.kt`**

```kotlin
package com.teamstranscriber.companion.permissions

import android.Manifest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PermissionsTest {
    @Test fun onApi33Plus_recordAudioAndPostNotifications_whenNoneGranted() {
        val missing = Permissions.missingRuntimePermissions(granted = emptySet(), sdkInt = 33)
        assertEquals(
            listOf(Manifest.permission.RECORD_AUDIO, Manifest.permission.POST_NOTIFICATIONS),
            missing,
        )
    }

    @Test fun belowApi33_postNotificationsNotRequired() {
        val missing = Permissions.missingRuntimePermissions(granted = emptySet(), sdkInt = 29)
        assertEquals(listOf(Manifest.permission.RECORD_AUDIO), missing)
    }

    @Test fun grantedPermissionsAreExcluded() {
        val missing = Permissions.missingRuntimePermissions(
            granted = setOf(Manifest.permission.RECORD_AUDIO), sdkInt = 29,
        )
        assertTrue(missing.isEmpty())
    }
}
```

- [ ] **Step 3: Run it, expect FAIL.**

- [ ] **Step 4: Create `Permissions.kt`**

```kotlin
package com.teamstranscriber.companion.permissions

import android.Manifest
import android.os.Build

object Permissions {
    /** Dialog-grantable permissions the recorder needs (special grants handled elsewhere). */
    fun missingRuntimePermissions(granted: Set<String>, sdkInt: Int): List<String> {
        val needed = buildList {
            add(Manifest.permission.RECORD_AUDIO)
            if (sdkInt >= Build.VERSION_CODES.TIRAMISU) add(Manifest.permission.POST_NOTIFICATIONS)
        }
        return needed.filter { it !in granted }
    }
}
```

- [ ] **Step 5: Run it, expect PASS.**

- [ ] **Step 6: Create `Storage.kt`** (no unit test — pure Android `Environment`; verified on-device)

```kotlin
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
```

- [ ] **Step 7: Replace `AndroidManifest.xml`** with the full permission + component set

```xml
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    xmlns:tools="http://schemas.android.com/tools">

    <uses-permission android:name="android.permission.RECORD_AUDIO" />
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE" />
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE_MICROPHONE" />
    <uses-permission android:name="android.permission.POST_NOTIFICATIONS" />
    <uses-permission android:name="android.permission.MANAGE_EXTERNAL_STORAGE"
        tools:ignore="ScopedStorage" />

    <application
        android:allowBackup="true"
        android:label="@string/app_name"
        android:supportsRtl="true"
        android:theme="@style/Theme.TTCompanion">

        <activity
            android:name=".MainActivity"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>

        <service
            android:name=".recording.RecordingService"
            android:exported="false"
            android:foregroundServiceType="microphone" />

        <service
            android:name=".recording.TeamsCallWatcher"
            android:exported="false"
            android:permission="android.permission.BIND_NOTIFICATION_LISTENER_SERVICE">
            <intent-filter>
                <action android:name="android.service.notification.NotificationListenerService" />
            </intent-filter>
        </service>
    </application>
</manifest>
```

- [ ] **Step 8: Build to verify manifest + BuildConfig compile**

Run: `& .\gradlew.bat -p . assembleDebug --console=plain` → `BUILD SUCCESSFUL`, and `./gradlew testDebugUnitTest` still green.

- [ ] **Step 9: Commit**

```bash
git add android/app/src/main/AndroidManifest.xml android/app/build.gradle.kts android/app/src/main/java/com/teamstranscriber/companion/permissions android/app/src/main/java/com/teamstranscriber/companion/storage android/app/src/test/java/com/teamstranscriber/companion/permissions
git commit -m "feat(android): manifest permissions, runtime-permission logic, storage resolver"
```

---

### Task 4: AudioRecorder + RecordingService (foreground mic capture → outbox)

**Files:**
- Create: `android/app/src/main/java/com/teamstranscriber/companion/recording/ElapsedFormatter.kt`
- Create: `android/app/src/main/java/com/teamstranscriber/companion/recording/RecordingGuards.kt`
- Create: `android/app/src/main/java/com/teamstranscriber/companion/recording/RecordingState.kt`
- Create: `android/app/src/main/java/com/teamstranscriber/companion/recording/AudioRecorder.kt`
- Create: `android/app/src/main/java/com/teamstranscriber/companion/recording/RecordingService.kt`
- Test: `android/app/src/test/java/com/teamstranscriber/companion/recording/ElapsedFormatterTest.kt`, `RecordingGuardsTest.kt`

**Interfaces:**
- Consumes: `Sidecar`, `OutboxWriter`, `Storage.outboxDir`, `RecordingSource`, `newUid`, `defaultTitle`, `BuildConfig.VERSION_NAME`.
- Produces:
  - `fun formatElapsed(millis: Long): String` (`0:00`, `1:05`, `1:02:03`)
  - `object RecordingGuards { const val MAX_DURATION_MS: Long; const val MIN_FREE_BYTES: Long; fun shouldAutoStop(elapsedMillis, capMillis): Boolean; fun hasEnoughFreeSpace(freeBytes, minBytes): Boolean }`
  - `data class RecordingStatus(val active: Boolean, val source: RecordingSource?, val startedAtEpochMillis: Long?)` and `object RecordingBus { val state: StateFlow<RecordingStatus> }`
  - `RecordingService` with companion `fun start(context, source: RecordingSource)` and `fun stop(context)` (sent as service intents with action extras).

- [ ] **Step 1: Write `ElapsedFormatterTest.kt`**

```kotlin
package com.teamstranscriber.companion.recording

import org.junit.Assert.assertEquals
import org.junit.Test

class ElapsedFormatterTest {
    @Test fun underAnHour_isMinuteColonSeconds() {
        assertEquals("0:00", formatElapsed(0))
        assertEquals("0:09", formatElapsed(9_000))
        assertEquals("1:05", formatElapsed(65_000))
    }
    @Test fun overAnHour_isHourMinuteSeconds() {
        assertEquals("1:02:03", formatElapsed(3_723_000))
    }
    @Test fun negativeClampsToZero() {
        assertEquals("0:00", formatElapsed(-5_000))
    }
}
```

- [ ] **Step 2: Run it, expect FAIL.**

- [ ] **Step 3: Create `ElapsedFormatter.kt`**

```kotlin
package com.teamstranscriber.companion.recording

/** `M:SS` under an hour, `H:MM:SS` at/above; negatives clamp to `0:00`. */
fun formatElapsed(millis: Long): String {
    val total = (millis.coerceAtLeast(0)) / 1000
    val h = total / 3600
    val m = (total % 3600) / 60
    val s = total % 60
    return if (h > 0) "%d:%02d:%02d".format(h, m, s) else "%d:%02d".format(m, s)
}
```

- [ ] **Step 4: Run it, expect PASS.**

- [ ] **Step 4a: Write `RecordingGuardsTest.kt`** (spec: auto-record needs a max-duration cap + free-storage check)

```kotlin
package com.teamstranscriber.companion.recording

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RecordingGuardsTest {
    @Test fun shouldAutoStop_trueAtOrPastCap() {
        assertFalse(RecordingGuards.shouldAutoStop(elapsedMillis = 10, capMillis = 100))
        assertTrue(RecordingGuards.shouldAutoStop(elapsedMillis = 100, capMillis = 100))
        assertTrue(RecordingGuards.shouldAutoStop(elapsedMillis = 101, capMillis = 100))
    }

    @Test fun hasEnoughFreeSpace_falseBelowMinimum() {
        assertTrue(RecordingGuards.hasEnoughFreeSpace(freeBytes = 100, minBytes = 100))
        assertFalse(RecordingGuards.hasEnoughFreeSpace(freeBytes = 99, minBytes = 100))
    }
}
```

- [ ] **Step 4b: Run it, expect FAIL.**

- [ ] **Step 4c: Create `RecordingGuards.kt`**

```kotlin
package com.teamstranscriber.companion.recording

/** Guards against runaway auto-recording (spec: max-duration cap + free-storage check). */
object RecordingGuards {
    /** A stuck Teams-call notification must not record forever. */
    const val MAX_DURATION_MS: Long = 4L * 60 * 60 * 1000 // 4 hours

    /** Refuse to start a recording with less than this much free space. */
    const val MIN_FREE_BYTES: Long = 200L * 1024 * 1024 // 200 MB

    fun shouldAutoStop(elapsedMillis: Long, capMillis: Long = MAX_DURATION_MS): Boolean =
        elapsedMillis >= capMillis

    fun hasEnoughFreeSpace(freeBytes: Long, minBytes: Long = MIN_FREE_BYTES): Boolean =
        freeBytes >= minBytes
}
```

- [ ] **Step 4d: Run it, expect PASS.**

- [ ] **Step 5: Create `RecordingState.kt`**

```kotlin
package com.teamstranscriber.companion.recording

import com.teamstranscriber.companion.sync.RecordingSource
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

data class RecordingStatus(
    val active: Boolean = false,
    val source: RecordingSource? = null,
    val startedAtEpochMillis: Long? = null,
)

/** Process-wide recording state the UI observes; the service is the only writer. */
object RecordingBus {
    private val _state = MutableStateFlow(RecordingStatus())
    val state: StateFlow<RecordingStatus> = _state.asStateFlow()
    internal fun set(status: RecordingStatus) { _state.value = status }
}
```

Requires the coroutines dependency. Add to `gradle/libs.versions.toml` (`[versions]` `kotlinxCoroutines = "1.8.1"`; `[libraries]` `kotlinx-coroutines-android = { group = "org.jetbrains.kotlinx", name = "kotlinx-coroutines-android", version.ref = "kotlinxCoroutines" }`) and `implementation(libs.kotlinx.coroutines.android)` in `app/build.gradle.kts`.

- [ ] **Step 6: Create `AudioRecorder.kt`** (framework; no unit test)

```kotlin
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
        r.prepare()
        r.start()
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
```

- [ ] **Step 7: Create `RecordingService.kt`** (framework; no unit test — behavior on the on-device checklist)

```kotlin
package com.teamstranscriber.companion.recording

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import com.teamstranscriber.companion.BuildConfig
import com.teamstranscriber.companion.R
import com.teamstranscriber.companion.storage.Storage
import com.teamstranscriber.companion.sync.OutboxWriter
import com.teamstranscriber.companion.sync.RecordingSource
import com.teamstranscriber.companion.sync.Sidecar
import com.teamstranscriber.companion.sync.defaultTitle
import com.teamstranscriber.companion.sync.newUid
import java.io.File
import java.time.ZoneId
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

class RecordingService : Service() {
    private lateinit var recorder: AudioRecorder
    private lateinit var tempFile: File
    private var source: RecordingSource = RecordingSource.MEMO
    private var startedAt: Long = 0L
    private val scope = CoroutineScope(Dispatchers.Main + Job())

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> begin(RecordingSource.valueOf(intent.getStringExtra(EXTRA_SOURCE)!!))
            ACTION_STOP -> finish()
        }
        return START_STICKY
    }

    private fun begin(src: RecordingSource) {
        if (RecordingBus.state.value.active) return // already recording; ignore
        createChannel()
        if (!RecordingGuards.hasEnoughFreeSpace(Storage.outboxDir().usableSpace)) {
            notifyError("Not enough free storage to record")
            stopSelf()
            return
        }
        source = src
        startedAt = System.currentTimeMillis()
        tempFile = File(cacheDir, "capture_${newUid()}.m4a")
        recorder = AudioRecorder(tempFile)
        startForeground(NOTIF_ID, buildNotification(0L))
        recorder.start(this)
        RecordingBus.set(RecordingStatus(active = true, source = src, startedAtEpochMillis = startedAt))
        scope.launch {
            while (RecordingBus.state.value.active) {
                delay(1_000)
                val elapsed = System.currentTimeMillis() - startedAt
                if (RecordingGuards.shouldAutoStop(elapsed)) { finish(); break } // max-duration cap
                notificationManager().notify(NOTIF_ID, buildNotification(elapsed))
            }
        }
    }

    private fun finish() {
        val endedAt = System.currentTimeMillis()
        val produced = if (::recorder.isInitialized) recorder.stop() else false
        RecordingBus.set(RecordingStatus(active = false))
        if (produced && tempFile.exists()) {
            val uid = newUid()
            val sidecar = Sidecar(
                uid = uid,
                title = defaultTitle(source, startedAt, ZoneId.systemDefault()),
                source = source,
                startedAtEpochMillis = startedAt,
                endedAtEpochMillis = endedAt,
                appVersion = BuildConfig.VERSION_NAME,
            )
            runCatching { OutboxWriter(Storage.outboxDir()).write(tempFile, sidecar) }
                .onFailure { notifyError(it.message ?: "save failed") }
        }
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    private fun buildNotification(elapsedMillis: Long): Notification {
        val stopIntent = PendingIntent.getService(
            this, 0, Intent(this, RecordingService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return Notification.Builder(this, CHANNEL_ID)
            .setContentTitle("Recording — ${source.label()}")
            .setContentText(formatElapsed(elapsedMillis))
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setOngoing(true)
            .setUsesChronometer(false)
            .addAction(Notification.Action.Builder(null, "Stop", stopIntent).build())
            .build()
    }

    private fun notifyError(msg: String) {
        createChannel()
        notificationManager().notify(
            NOTIF_ID + 1,
            Notification.Builder(this, CHANNEL_ID)
                .setContentTitle("Recording not saved")
                .setContentText(msg)
                .setSmallIcon(android.R.drawable.stat_notify_error)
                .build(),
        )
    }

    private fun createChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val ch = NotificationChannel(CHANNEL_ID, "Recording", NotificationManager.IMPORTANCE_LOW)
            notificationManager().createNotificationChannel(ch)
        }
    }

    private fun notificationManager() =
        getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

    companion object {
        private const val CHANNEL_ID = "recording"
        private const val NOTIF_ID = 42
        const val ACTION_START = "com.teamstranscriber.companion.START"
        const val ACTION_STOP = "com.teamstranscriber.companion.STOP"
        const val EXTRA_SOURCE = "source"

        fun start(context: Context, source: RecordingSource) {
            val i = Intent(context, RecordingService::class.java)
                .setAction(ACTION_START).putExtra(EXTRA_SOURCE, source.name)
            context.startForegroundService(i)
        }

        fun stop(context: Context) {
            context.startService(
                Intent(context, RecordingService::class.java).setAction(ACTION_STOP),
            )
        }
    }
}

private fun RecordingSource.label(): String = when (this) {
    RecordingSource.TEAMS_CALL -> "Teams call"
    RecordingSource.IN_PERSON -> "In-person"
    RecordingSource.MEMO -> "Voice memo"
}
```

- [ ] **Step 8: Build + unit tests**

Run: `& .\gradlew.bat -p . testDebugUnitTest assembleDebug --console=plain` → `BUILD SUCCESSFUL`; ElapsedFormatter tests green.

- [ ] **Step 9: Commit**

```bash
git add android/app/src/main/java/com/teamstranscriber/companion/recording android/app/src/test/java/com/teamstranscriber/companion/recording android/app/build.gradle.kts android/gradle/libs.versions.toml
git commit -m "feat(android): foreground RecordingService + AudioRecorder, elapsed formatter"
```

---

### Task 5: Recorder UI (Compose) + runtime permissions

**Files:**
- Create: `android/app/src/main/java/com/teamstranscriber/companion/ui/RecorderScreen.kt`
- Modify: `android/app/src/main/java/com/teamstranscriber/companion/MainActivity.kt`
- Create: `android/app/src/main/java/com/teamstranscriber/companion/settings/AppPrefs.kt`

**Interfaces:**
- Consumes: `RecordingBus`, `RecordingService.start/stop`, `Permissions`, `Storage.hasAllFilesAccess`, `AppPrefs`.
- Produces: `class AppPrefs(context) { var autoRecordEnabled: Boolean }`; `@Composable fun RecorderScreen()`.

**Notes:** This task has no unit test — Compose UI is verified on-device. The deliverable is a building APK plus a manual checklist item. Keep logic minimal; the record button reads `RecordingBus.state` and calls the service.

- [ ] **Step 1: Create `AppPrefs.kt`**

```kotlin
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
```

- [ ] **Step 2: Create `RecorderScreen.kt`**

```kotlin
package com.teamstranscriber.companion.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.teamstranscriber.companion.recording.RecordingBus
import com.teamstranscriber.companion.recording.RecordingService
import com.teamstranscriber.companion.recording.formatElapsed
import com.teamstranscriber.companion.settings.AppPrefs
import com.teamstranscriber.companion.sync.RecordingSource

@Composable
fun RecorderScreen(onRequestPermissions: () -> Unit, onOpenAllFilesAccess: () -> Unit) {
    val context = LocalContext.current
    val status by RecordingBus.state.collectAsState()
    val prefs = remember { AppPrefs(context) }
    var autoRecord by remember { mutableStateOf(prefs.autoRecordEnabled) }
    var pickingSource by remember { mutableStateOf(false) }

    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(16.dp, Alignment.CenterVertically),
    ) {
        if (status.active) {
            val elapsed = (System.currentTimeMillis() - (status.startedAtEpochMillis ?: 0L))
            Text("Recording ${status.source?.name ?: ""}")
            Text(formatElapsed(elapsed))
            Button(onClick = { RecordingService.stop(context) }) { Text("Stop") }
        } else if (pickingSource) {
            Text("What are you recording?")
            Button(onClick = {
                pickingSource = false
                RecordingService.start(context, RecordingSource.IN_PERSON)
            }) { Text("In-person meeting") }
            Button(onClick = {
                pickingSource = false
                RecordingService.start(context, RecordingSource.MEMO)
            }) { Text("Voice memo") }
            OutlinedButton(onClick = { pickingSource = false }) { Text("Cancel") }
        } else {
            Button(onClick = { pickingSource = true }) { Text("Record") }
        }

        Text("Auto-record Teams calls")
        Switch(checked = autoRecord, onCheckedChange = {
            autoRecord = it; prefs.autoRecordEnabled = it
        })
        OutlinedButton(onClick = onOpenAllFilesAccess) { Text("Grant file access") }
        OutlinedButton(onClick = onRequestPermissions) { Text("Grant mic / notifications") }
    }
}
```

- [ ] **Step 3: Replace `MainActivity.kt`** to host the screen and drive permission requests

```kotlin
package com.teamstranscriber.companion

import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.core.content.ContextCompat
import com.teamstranscriber.companion.permissions.Permissions
import com.teamstranscriber.companion.ui.RecorderScreen

class MainActivity : ComponentActivity() {
    private val requestPermissions =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Surface {
                    RecorderScreen(
                        onRequestPermissions = ::requestRuntimePermissions,
                        onOpenAllFilesAccess = ::openAllFilesAccess,
                    )
                }
            }
        }
    }

    private fun requestRuntimePermissions() {
        val granted = buildSet {
            listOf(
                android.Manifest.permission.RECORD_AUDIO,
                android.Manifest.permission.POST_NOTIFICATIONS,
            ).forEach {
                if (ContextCompat.checkSelfPermission(this@MainActivity, it) ==
                    PackageManager.PERMISSION_GRANTED
                ) add(it)
            }
        }
        val missing = Permissions.missingRuntimePermissions(granted, Build.VERSION.SDK_INT)
        if (missing.isNotEmpty()) requestPermissions.launch(missing.toTypedArray())
    }

    private fun openAllFilesAccess() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            startActivity(
                Intent(
                    Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                    Uri.parse("package:$packageName"),
                ),
            )
        }
    }
}
```

- [ ] **Step 4: Build**

Run: `& .\gradlew.bat -p . assembleDebug --console=plain` → `BUILD SUCCESSFUL`.

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/teamstranscriber/companion/ui android/app/src/main/java/com/teamstranscriber/companion/settings android/app/src/main/java/com/teamstranscriber/companion/MainActivity.kt
git commit -m "feat(android): recorder Compose UI, source picker, auto-record toggle, permission flows"
```

---

### Task 6: TeamsCallWatcher — auto-record on Teams call

**Files:**
- Create: `android/app/src/main/java/com/teamstranscriber/companion/recording/TeamsCallDetector.kt`
- Create: `android/app/src/main/java/com/teamstranscriber/companion/recording/TeamsCallWatcher.kt`
- Test: `android/app/src/test/java/com/teamstranscriber/companion/recording/TeamsCallDetectorTest.kt`

**Interfaces:**
- Consumes: `TeamsCallDetector`, `AppPrefs`, `RecordingService.start/stop`, `RecordingSource.TEAMS_CALL`, `RecordingBus`.
- Produces: `object TeamsCallDetector { const val TEAMS_PACKAGE = "com.microsoft.teams"; fun isOngoingTeamsCall(pkg, category, isOngoing, title, text): Boolean }`.

**Notes:** The detector is a pure predicate so it is unit-testable; the listener service itself is verified on-device. The heuristic (package + `CATEGORY_CALL` + ongoing) is intentionally conservative and may need on-device tuning — the persistent recording notification is the guardrail against mis-triggers.

- [ ] **Step 1: Write `TeamsCallDetectorTest.kt`**

```kotlin
package com.teamstranscriber.companion.recording

import android.app.Notification
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class TeamsCallDetectorTest {
    @Test fun matches_teamsOngoingCallCategory() {
        assertTrue(
            TeamsCallDetector.isOngoingTeamsCall(
                pkg = "com.microsoft.teams",
                category = Notification.CATEGORY_CALL,
                isOngoing = true, title = "Ongoing call", text = "00:12",
            ),
        )
    }

    @Test fun rejects_otherApps() {
        assertFalse(
            TeamsCallDetector.isOngoingTeamsCall(
                pkg = "com.whatsapp", category = Notification.CATEGORY_CALL,
                isOngoing = true, title = "Call", text = "",
            ),
        )
    }

    @Test fun rejects_teamsNonCallOrNonOngoing() {
        assertFalse(
            TeamsCallDetector.isOngoingTeamsCall(
                pkg = "com.microsoft.teams", category = Notification.CATEGORY_MESSAGE,
                isOngoing = true, title = "New message", text = "",
            ),
        )
        assertFalse(
            TeamsCallDetector.isOngoingTeamsCall(
                pkg = "com.microsoft.teams", category = Notification.CATEGORY_CALL,
                isOngoing = false, title = "Missed call", text = "",
            ),
        )
    }
}
```

- [ ] **Step 2: Run it, expect FAIL.**

- [ ] **Step 3: Create `TeamsCallDetector.kt`**

```kotlin
package com.teamstranscriber.companion.recording

import android.app.Notification

/** Pure heuristic: does this notification represent an ongoing Teams call? */
object TeamsCallDetector {
    const val TEAMS_PACKAGE = "com.microsoft.teams"

    fun isOngoingTeamsCall(
        pkg: String,
        category: String?,
        isOngoing: Boolean,
        title: String?,
        text: String?,
    ): Boolean = pkg == TEAMS_PACKAGE && isOngoing && category == Notification.CATEGORY_CALL
}
```

- [ ] **Step 4: Run it, expect PASS.**

- [ ] **Step 5: Create `TeamsCallWatcher.kt`** (framework; on-device checklist)

```kotlin
package com.teamstranscriber.companion.recording

import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import com.teamstranscriber.companion.settings.AppPrefs
import com.teamstranscriber.companion.sync.RecordingSource

class TeamsCallWatcher : NotificationListenerService() {
    private val prefs by lazy { AppPrefs(applicationContext) }
    private var callKey: String? = null

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        if (!prefs.autoRecordEnabled) return
        val n = sbn.notification ?: return
        val matches = TeamsCallDetector.isOngoingTeamsCall(
            pkg = sbn.packageName,
            category = n.category,
            isOngoing = (n.flags and android.app.Notification.FLAG_ONGOING_EVENT) != 0,
            title = n.extras?.getCharSequence(android.app.Notification.EXTRA_TITLE)?.toString(),
            text = n.extras?.getCharSequence(android.app.Notification.EXTRA_TEXT)?.toString(),
        )
        if (matches && !RecordingBus.state.value.active) {
            callKey = sbn.key
            RecordingService.start(this, RecordingSource.TEAMS_CALL)
        }
    }

    override fun onNotificationRemoved(sbn: StatusBarNotification) {
        // Stop only the recording that this call notification started.
        if (sbn.key == callKey && RecordingBus.state.value.source == RecordingSource.TEAMS_CALL) {
            callKey = null
            RecordingService.stop(this)
        }
    }
}
```

- [ ] **Step 6: Build + unit tests**

Run: `& .\gradlew.bat -p . testDebugUnitTest assembleDebug --console=plain` → `BUILD SUCCESSFUL`; detector tests green.

- [ ] **Step 7: Commit**

```bash
git add android/app/src/main/java/com/teamstranscriber/companion/recording/TeamsCallDetector.kt android/app/src/main/java/com/teamstranscriber/companion/recording/TeamsCallWatcher.kt android/app/src/test/java/com/teamstranscriber/companion/recording/TeamsCallDetectorTest.kt
git commit -m "feat(android): Teams-call auto-record via NotificationListenerService"
```

---

### Task 7: On-device verification checklist + full USB round-trip

**Files:**
- Create: `docs/superpowers/checklists/2026-07-25-phase-3-android-recorder.md`

**Notes:** No code — this task assembles and (with the human) executes the manual verification that the framework code (recording, notification, auto-record, storage) actually works on Brian's Pixel, and that a phone recording round-trips through the already-built desktop USB sync (Phases 1–2). The controller installs the APK via `adb` and hands Brian the checklist.

- [ ] **Step 1: Write the checklist** with these items, each with an explicit expected result:

```markdown
# Phase 3 — Android recorder on-device checklist

Install: from `android/`, `& .\gradlew.bat -p . installDebug` with the Pixel
connected and USB debugging on (proxy scrubbed). Grant on first run:
- App → "Grant mic / notifications" → allow both.
- App → "Grant file access" → toggle All-files access ON in Settings.
- Android Settings → Notifications → Device & app notifications → Special app
  access → Notification access → enable "TT Companion" (for auto-record).

1. Manual record (voice memo): tap Record → Voice memo. Expect a persistent
   notification titled "Recording — Voice memo" with a ticking timer + Stop.
2. Stop from the notification → notification clears. Expect
   `Documents/TeamsTranscriber/outbox/rec_<uid>.m4a` + `rec_<uid>.json` on the
   phone; the sidecar `source` = `memo`, `started_at`/`ended_at` end in
   `+00:00`, `duration_ms` ≈ recording length.
3. Manual record (in-person): repeat; sidecar `source` = `in_person`.
4. Screen-off survival: start a recording, turn the screen off for 30s, stop.
   Expect a complete, playable file (no truncation).
5. Auto-record Teams call: enable the toggle, place a Teams call on the phone.
   Expect recording to start automatically (Teams-call notification) with
   source `teams_call`, and to stop when the call ends.
6. Error surfacing: revoke All-files access, record + stop. Expect a
   "Recording not saved" notification, not a silent failure.
7. Desktop USB round-trip: with recordings in the outbox, plug the phone into
   the desktop (phone-sync enabled). Expect the desktop to import them as
   meetings (source-tagged), transcribe + summarize, and clear the outbox —
   the full Phase 1–2 sync, now fed by the phone.
```

- [ ] **Step 2: Controller installs the APK** — `& .\gradlew.bat -p . installDebug --console=plain` (Pixel connected, proxy scrubbed) — and hands Brian the checklist. Record pass/fail + any heuristic tuning (esp. item 5) back into this file and the phase notes.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/checklists/2026-07-25-phase-3-android-recorder.md
git commit -m "docs(android): Phase 3 on-device verification checklist"
```

---

## Notes for execution

- **Reviewers:** pure-logic tasks (1, 2, and the pure predicates in 4/6) get normal spec+quality review against the JUnit tests. Framework tasks (3, 4, 5, 6 service/UI) cannot be unit-verified — review manifest correctness, permission/version guards, service lifecycle, and that the on-device checklist item is concrete; do not demand JUnit tests for `MediaRecorder`/`Service`/`NotificationListenerService`/Compose.
- **First `installDebug`** requires the Pixel in USB-debug mode (it was used for the MTP spike). Auto-record heuristic (Task 6) is explicitly expected to need on-device tuning; capture findings in the checklist.
- **Do not** add a phone-side database, library UI, or `changes.json` handling — those are Phase 4.
