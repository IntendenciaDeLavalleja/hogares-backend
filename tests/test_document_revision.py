from datetime import datetime

from app.models import ApplicationDocument


def test_document_revision_changes_for_same_metadata_replacement_without_exposing_object_key():
    document = ApplicationDocument(
        id="stable-document-id",
        application_id="draft-id",
        document_type="applicant_id",
        object_key="private/draft-id/first-random-object-key",
        original_name="cedula.pdf",
        stored_name="cedula.pdf",
        media_type="application/pdf",
        size_bytes=100,
        checksum_sha256="a" * 64,
        updated_at=datetime(2026, 11, 3, 12, 0, 0, 123456),
    )
    previous = document.to_dict()
    assert previous["updatedAt"] == "2026-11-03T12:00:00.123456Z"
    assert len(previous["revision"]) == 64
    assert document.object_key not in str(previous)
    assert previous == document.to_dict()

    # A replacement reuses the database ID and may share a timestamp truncated
    # to a second, filename and size. The unique object key still changes.
    document.object_key = "private/draft-id/second-random-object-key"
    replacement = document.to_dict()
    assert replacement["id"] == previous["id"]
    assert replacement["revision"] != previous["revision"]
    assert {key: value for key, value in replacement.items() if key != "revision"} == {
        key: value for key, value in previous.items() if key != "revision"
    }


def test_document_revision_is_available_before_timestamp_has_been_refreshed():
    document = ApplicationDocument(object_key="private/new-object")
    metadata = document.to_dict()
    assert metadata["updatedAt"] is None
    assert len(metadata["revision"]) == 64
