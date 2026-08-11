"""Screen Capture Module — region-limited screen grab.

Uses ``mss`` (fast, native) to capture only the configured screen region, never
the full desktop (plan §2.1). Returns a PIL Image plus a cheap digest for future
frame-diffing (Phase 3).
"""

from __future__ import annotations

import base64
import hashlib
import io
import threading

from PIL import Image

from ai_trader.config import Config, Region


class CaptureError(RuntimeError):
    """Screen capture failed (bad region, no display, driver issue)."""


def grab_region(region: Region) -> Image.Image:
    """Capture the configured screen region as a PIL RGB image.

    Raises:
        CaptureError: if the capture backend fails for any reason.
    """
    if not region.nonzero:
        raise CaptureError(f"invalid capture region (must have positive size): {region}")
    try:
        import mss
    except ImportError as exc:  # pragma: no cover - dependency missing
        raise CaptureError("mss is not installed (pip install -r requirements.txt)") from exc

    try:
        with mss.mss() as sct:
            shot = sct.grab(region.as_mss())
            image = Image.frombytes("RGB", shot.size, shot.rgb)
    except Exception as exc:
        raise CaptureError(f"screen capture failed: {exc}") from exc
    return image


def image_to_base64_png(image: Image.Image) -> str:
    """Encode a PIL image as a base64 PNG string for vision-API payloads."""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def frame_digest(image: Image.Image) -> str:
    """Cheap, stable digest for frame-diffing.

    Downscale to a 16x16 grayscale thumbnail and hash its bytes. Good enough to
    skip unchanged frames before spending an API call (plan §3 / §6).
    """
    thumb = image.convert("L").resize((16, 16))
    return hashlib.sha256(thumb.tobytes()).hexdigest()[:16]


class FrameGate:
    """Tracks the last successfully analyzed frame for polling deduplication.

    An unchanged frame is skipped before it is encoded or sent to a provider.
    Failed reads are deliberately not recorded, so the next polling cycle retries
    rather than silently preserving an error state.
    """

    def __init__(self) -> None:
        self._last_processed: str | None = None
        self._lock = threading.Lock()

    def is_new(self, digest: str) -> bool:
        """Return whether ``digest`` differs from the last successful read."""
        with self._lock:
            return digest != self._last_processed

    def mark_processed(self, digest: str) -> None:
        """Record a frame only after its signal was parsed successfully."""
        with self._lock:
            self._last_processed = digest
