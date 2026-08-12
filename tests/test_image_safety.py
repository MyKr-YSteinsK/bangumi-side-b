"""Streaming download and decoded-image safety boundaries."""

from __future__ import annotations

from io import BytesIO

import httpx
import pytest
from PIL import Image

from bgm_side_b import __version__
from bgm_side_b.api import DEFAULT_USER_AGENT, BangumiApiClient, BangumiApiError
from bgm_side_b.media import CoverValidationError, _webp_bytes


class CountingStream(httpx.SyncByteStream):
    def __init__(self, *chunks: bytes) -> None:
        self.chunks = chunks
        self.reads = 0
        self.closed = False

    def __iter__(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk

    def close(self) -> None:
        self.closed = True


class FailingStream(httpx.SyncByteStream):
    def __init__(self) -> None:
        self.closed = False

    def __iter__(self):
        raise httpx.ReadError("fixture read failure")

    def close(self) -> None:
        self.closed = True


def _streaming_client(
    stream: CountingStream, *, headers: dict[str, str] | None = None
) -> BangumiApiClient:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers=headers, stream=stream)

    return BangumiApiClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=0,
        sleeper=lambda _: None,
        jitter=lambda: 0,
    )


def _png(width: int, height: int) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), color=(1, 2, 3)).save(output, "PNG")
    return output.getvalue()


def test_declared_oversized_image_is_rejected_without_reading_body() -> None:
    stream = CountingStream(b"unread")
    client = _streaming_client(stream, headers={"content-length": "100"})

    with pytest.raises(BangumiApiError, match="size limit") as caught:
        client.fetch_image("https://example.invalid/cover", max_bytes=8)

    assert caught.value.code == "image_too_large"
    assert stream.reads == 0
    assert stream.closed


def test_chunked_oversized_image_aborts_as_soon_as_cap_is_exceeded() -> None:
    stream = CountingStream(b"1234", b"5678", b"must-not-be-read")
    client = _streaming_client(stream)

    with pytest.raises(BangumiApiError, match="size limit"):
        client.fetch_image("https://example.invalid/cover", max_bytes=7)

    assert stream.reads == 2
    assert stream.closed


def test_exact_image_cap_preserves_content_type_and_final_url() -> None:
    stream = CountingStream(b"1234", b"5678")
    client = _streaming_client(stream, headers={"content-type": "image/png"})

    response = client.fetch_image("https://example.invalid/cover", max_bytes=8)

    assert response.content == b"12345678"
    assert response.content_type == "image/png"
    assert response.final_url == "https://example.invalid/cover"
    assert stream.reads == 2
    assert stream.closed


def test_stream_read_failure_keeps_image_retry_semantics() -> None:
    failing = FailingStream()
    accepted = CountingStream(b"image")
    requests = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        stream = failing if requests == 1 else accepted
        return httpx.Response(200, stream=stream)

    client = BangumiApiClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=1,
        sleeper=lambda _: None,
        jitter=lambda: 0,
    )
    response = client.fetch_image("https://example.invalid/cover", max_bytes=5)

    assert response.content == b"image"
    assert requests == 2
    assert client.metrics.image_retries == 1
    assert failing.closed


def test_default_user_agent_tracks_package_version() -> None:
    client = BangumiApiClient(max_retries=0)
    try:
        assert DEFAULT_USER_AGENT == f"Bangumi-Side-B/{__version__}"
        assert client._client.headers["user-agent"] == DEFAULT_USER_AGENT
    finally:
        client.close()


def test_huge_dimensions_and_pillow_bombs_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(CoverValidationError) as dimensions:
        _webp_bytes(_png(12_001, 1))
    assert dimensions.value.code == "cover_dimensions"

    def bomb(*_args: object, **_kwargs: object):
        raise Image.DecompressionBombError("fixture")

    monkeypatch.setattr(Image, "open", bomb)
    with pytest.raises(CoverValidationError) as decompression:
        _webp_bytes(b"image")
    assert decompression.value.code == "cover_dimensions"


def test_normal_image_conversion_remains_bounded_webp() -> None:
    content, width, height = _webp_bytes(_png(400, 600))

    assert (width, height) == (400, 600)
    with Image.open(BytesIO(content)) as image:
        assert image.format == "WEBP"
        assert image.size == (400, 600)
