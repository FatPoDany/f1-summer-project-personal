"""The weights file: only the pinned bytes ever count as a usable model."""

import hashlib
import io
import os
from pathlib import Path

import pytest

from racecoach.granite import model as gm


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("GRANITE_MODEL_CACHE", str(tmp_path / "models"))
    monkeypatch.delenv("GRANITE_MODEL_PATH", raising=False)
    return tmp_path / "models"


def pin_payload(monkeypatch, payload: bytes) -> None:
    """Shrink the pins to a test-sized file so the real ones stay untouched."""
    monkeypatch.setattr(gm, "MODEL_SIZE", len(payload))
    monkeypatch.setattr(gm, "MODEL_SHA256", hashlib.sha256(payload).hexdigest())


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes, status: int = 200) -> None:
        super().__init__(payload)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def test_the_model_lives_in_the_workspace_not_the_temp_directory(tmp_path, monkeypatch):
    """A participant would lose a 2.1 GB download to the next reboot."""
    monkeypatch.delenv("GRANITE_MODEL_CACHE", raising=False)
    monkeypatch.delenv("GRANITE_MODEL_PATH", raising=False)
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "Apex"))

    assert gm.model_path() == tmp_path / "Apex" / "models" / gm.MODEL_FILE


def test_an_environment_override_still_wins(tmp_path, monkeypatch):
    """So a researcher's shell and the app can share one copy of the weights."""
    monkeypatch.setenv("GRANITE_MODEL_PATH", str(tmp_path / "elsewhere.gguf"))
    assert gm.model_path() == tmp_path / "elsewhere.gguf"


def test_a_file_of_the_right_length_but_wrong_content_is_not_the_model(cache, monkeypatch):
    payload = b"granite weights" * 40
    pin_payload(monkeypatch, payload)
    cache.mkdir(parents=True)
    (cache / gm.MODEL_FILE).write_bytes(b"x" * len(payload))

    assert not gm.available()


def test_a_verified_file_needs_no_download(cache, monkeypatch):
    payload = b"granite weights" * 40
    pin_payload(monkeypatch, payload)
    cache.mkdir(parents=True)
    (cache / gm.MODEL_FILE).write_bytes(payload)

    def refuse(_request):  # pragma: no cover - proves it is never called
        raise AssertionError("downloaded a model that was already present")

    assert gm.ensure_model(opener=refuse) == cache / gm.MODEL_FILE


def test_a_download_is_verified_before_it_is_named_as_the_model(cache, monkeypatch):
    payload = b"granite weights" * 40
    pin_payload(monkeypatch, payload)
    seen = []

    path = gm.ensure_model(
        opener=lambda _r: FakeResponse(payload),
        on_progress=seen.append,
    )

    assert path.read_bytes() == payload
    assert not path.with_suffix(path.suffix + ".part").exists()
    assert seen[-1].received == len(payload) and seen[-1].fraction == 1.0


def test_a_truncated_download_is_kept_so_the_next_attempt_resumes(cache, monkeypatch):
    """On a link that drops during 2.1 GB, discarding progress means never finishing."""
    payload = b"granite weights" * 40
    pin_payload(monkeypatch, payload)

    with pytest.raises(gm.IncompleteDownload) as caught:
        gm.ensure_model(opener=lambda _r: FakeResponse(payload[:-10]))

    assert "picks up from there" in str(caught.value)
    # Not a model yet, but the bytes that did arrive are still on disk.
    assert not (cache / gm.MODEL_FILE).exists()
    assert (cache / (gm.MODEL_FILE + ".part")).stat().st_size == len(payload) - 10


def test_a_connection_that_drops_partway_is_resumed_until_it_completes(cache, monkeypatch):
    """The case that made this fail on a real network: one drop, then success."""
    payload = b"granite weights" * 40
    pin_payload(monkeypatch, payload)
    attempts = []

    def opener(request):
        attempts.append(request.get_header("Range"))
        if len(attempts) == 1:
            return FakeResponse(payload[:100])  # dies partway through
        start = int(request.get_header("Range").split("=")[1].rstrip("-"))
        return FakeResponse(payload[start:], status=206)

    path = gm.ensure_model(opener=opener)

    assert path.read_bytes() == payload
    assert attempts[0] is None and attempts[1] == "bytes=100-"


def test_an_unrecognised_file_already_in_place_stops_rather_than_overwrites(cache, monkeypatch):
    """Usually a hand-placed model someone still believes in. Say so, delete nothing."""
    payload = b"granite weights" * 40
    pin_payload(monkeypatch, payload)
    cache.mkdir(parents=True)
    intruder = cache / gm.MODEL_FILE
    intruder.write_bytes(b"someone else's model")

    with pytest.raises(gm.ModelError, match="not the pinned Granite model"):
        gm.ensure_model(opener=lambda _r: FakeResponse(payload))
    assert intruder.read_bytes() == b"someone else's model"


def test_a_resumed_download_continues_from_the_part_file(cache, monkeypatch):
    payload = b"granite weights" * 40
    pin_payload(monkeypatch, payload)
    cache.mkdir(parents=True)
    (cache / (gm.MODEL_FILE + ".part")).write_bytes(payload[:100])
    ranges = []

    def opener(request):
        ranges.append(request.get_header("Range"))
        return FakeResponse(payload[100:], status=206)

    path = gm.ensure_model(opener=opener)

    assert ranges == ["bytes=100-"]
    assert path.read_bytes() == payload


def test_a_server_that_ignores_the_range_header_restarts_instead_of_corrupting(
    cache, monkeypatch
):
    """Answering 200 to a ranged request means the body is the whole file again."""
    payload = b"granite weights" * 40
    pin_payload(monkeypatch, payload)
    cache.mkdir(parents=True)
    (cache / (gm.MODEL_FILE + ".part")).write_bytes(payload[:100])

    path = gm.ensure_model(opener=lambda _r: FakeResponse(payload, status=200))

    assert path.read_bytes() == payload


def test_cancelling_leaves_the_partial_download_for_next_time(cache, monkeypatch):
    payload = b"granite weights" * 400
    pin_payload(monkeypatch, payload)

    with pytest.raises(gm.ModelError, match="cancelled"):
        gm.ensure_model(
            opener=lambda _r: FakeResponse(payload),
            should_cancel=lambda: True,
        )

    assert not (cache / gm.MODEL_FILE).exists()


def test_a_full_disk_is_reported_before_the_download_starts(cache, monkeypatch):
    payload = b"granite weights" * 40
    pin_payload(monkeypatch, payload)
    monkeypatch.setattr(gm, "free_space_bytes", lambda _d: 10)

    with pytest.raises(gm.ModelError, match="Not enough free space"):
        gm.ensure_model(opener=lambda _r: FakeResponse(payload))


def test_the_shipped_pins_match_the_shell_script(monkeypatch):
    """Both paths must produce byte-identical weights or the study is not comparable."""
    script = Path("integrations/granite-4.1/download-model.sh").read_text("utf-8")
    assert f'MODEL_SIZE="{gm.MODEL_SIZE}"' in script
    assert f'MODEL_SHA256="{gm.MODEL_SHA256}"' in script
    assert f'MODEL_FILE="{gm.MODEL_FILE}"' in script
    assert f'MODEL_REVISION="{gm.MODEL_REVISION}"' in script
    assert os.path.basename(gm.MODEL_URL) == gm.MODEL_FILE


def test_a_second_source_is_tried_when_the_first_cannot_be_reached(cache, monkeypatch):
    """huggingface.co is unreachable on some participants' networks."""
    import urllib.error

    payload = b"granite weights" * 40
    pin_payload(monkeypatch, payload)
    tried = []

    def opener(request):
        tried.append(request.full_url)
        if "huggingface.co" in request.full_url:
            raise urllib.error.URLError("timed out")
        return FakeResponse(payload)

    path = gm.ensure_model(opener=opener)

    assert len(tried) == 2 and "hf-mirror.com" in tried[1]
    assert path.read_bytes() == payload


def test_every_source_failing_says_how_to_supply_the_file_by_hand(cache, monkeypatch):
    import urllib.error

    payload = b"granite weights" * 40
    pin_payload(monkeypatch, payload)

    def opener(_request):
        raise urllib.error.URLError("timed out")

    with pytest.raises(gm.ModelError) as caught:
        gm.ensure_model(opener=opener)
    message = str(caught.value)
    assert "any known source" in message
    assert "install-model" in message
    assert gm.MODEL_FILE in message


def test_an_explicit_url_replaces_the_built_in_sources(cache, monkeypatch):
    monkeypatch.setenv("GRANITE_MODEL_URL", "https://example.invalid/model.gguf")
    assert gm.model_urls() == ("https://example.invalid/model.gguf",)


def test_a_source_serving_the_wrong_bytes_is_refused_even_at_the_right_length(
    cache, monkeypatch
):
    """The digest is what makes any source, mirror included, safe to use."""
    import urllib.error

    payload = b"granite weights" * 40
    pin_payload(monkeypatch, payload)
    impostor = b"x" * len(payload)  # right length, wrong contents

    def opener(request):
        if "huggingface.co" in request.full_url:
            raise urllib.error.URLError("timed out")
        return FakeResponse(impostor)

    with pytest.raises(gm.ModelError, match="not the Granite model"):
        gm.ensure_model(opener=opener)
    # Wrong bytes cannot be resumed into right ones, so they go.
    assert not (cache / gm.MODEL_FILE).exists()
    assert not (cache / (gm.MODEL_FILE + ".part")).exists()


def test_a_file_delivered_by_hand_is_accepted_once_verified(cache, monkeypatch, tmp_path):
    payload = b"granite weights" * 40
    pin_payload(monkeypatch, payload)
    handed_over = tmp_path / "from-a-memory-stick.gguf"
    handed_over.write_bytes(payload)

    path = gm.import_model(handed_over)

    assert path == cache / gm.MODEL_FILE
    assert path.read_bytes() == payload
    assert gm.available()


def test_a_file_delivered_by_hand_that_is_not_the_model_is_refused(cache, monkeypatch, tmp_path):
    payload = b"granite weights" * 40
    pin_payload(monkeypatch, payload)
    wrong = tmp_path / "something-else.gguf"
    wrong.write_bytes(b"not the model")

    with pytest.raises(gm.ModelError, match="not the pinned Granite model"):
        gm.import_model(wrong)
    assert not gm.available()


def test_the_interface_check_does_not_digest_two_gigabytes(cache, monkeypatch):
    """looks_present runs on the GUI thread every time a lap is clicked."""
    payload = b"granite weights" * 40
    pin_payload(monkeypatch, payload)
    cache.mkdir(parents=True)
    (cache / gm.MODEL_FILE).write_bytes(b"x" * len(payload))

    real_digest = gm.digest_of

    def refuse(_path):  # pragma: no cover - proves the digest is skipped
        raise AssertionError("hashed the whole model just to draw a button")

    monkeypatch.setattr(gm, "digest_of", refuse)
    assert gm.looks_present() is True  # right length is all the interface asks

    # The real check still rejects it where correctness matters.
    # Restore only the digest function: undoing the whole fixture also discarded
    # the isolated cache and accidentally inspected a participant's real model.
    monkeypatch.setattr(gm, "digest_of", real_digest)
    assert gm.available() is False
