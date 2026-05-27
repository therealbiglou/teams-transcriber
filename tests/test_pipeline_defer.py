from teams_transcriber.storage.models import RecordingStatus


def test_waiting_for_notes_status_exists():
    assert RecordingStatus.WAITING_FOR_NOTES.value == "waiting_for_notes"
