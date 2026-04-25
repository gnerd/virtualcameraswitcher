import logging
import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Backends to try, in order, when opening a camera on Windows.
_CAPTURE_BACKENDS = [
    ("DSHOW", cv2.CAP_DSHOW),
    ("MSMF", cv2.CAP_MSMF),
    ("ANY", cv2.CAP_ANY),
]


@dataclass
class CameraInfo:
    index: int
    name: str
    width: int
    height: int


def enumerate_cameras(max_index: int = 10) -> list[CameraInfo]:
    """Probe camera indices and return available cameras."""
    cameras = []
    for i in range(max_index):
        cap = None
        for _, backend in _CAPTURE_BACKENDS:
            cap = cv2.VideoCapture(i, backend)
            if cap.isOpened():
                break
            cap.release()
            cap = None
        if cap is not None and cap.isOpened():
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            cameras.append(CameraInfo(index=i, name=f"Camera {i}", width=w, height=h))
            cap.release()
    return cameras


class CameraReader:
    """Continuously reads frames from a single camera in a background thread."""

    # Resolutions to try (largest first) when negotiating capture format.
    _FALLBACK_RESOLUTIONS = [(960, 540), (848, 480), (640, 480), (640, 360), (424, 240)]

    # Friendly control names accepted in CameraOverride.controls. Mapped to
    # OpenCV CAP_PROP_* integers. Keys are case-insensitive on input.
    _CONTROL_PROPS: dict[str, int] = {
        "brightness": cv2.CAP_PROP_BRIGHTNESS,
        "contrast": cv2.CAP_PROP_CONTRAST,
        "saturation": cv2.CAP_PROP_SATURATION,
        "hue": cv2.CAP_PROP_HUE,
        "gain": cv2.CAP_PROP_GAIN,
        "exposure": cv2.CAP_PROP_EXPOSURE,
        # OpenCV/DSHOW: 0.25 = manual, 0.75 = auto. Many drivers also accept
        # 0/1/3 — we pass the raw value through and let the driver interpret.
        "auto_exposure": cv2.CAP_PROP_AUTO_EXPOSURE,
        "autoexposure": cv2.CAP_PROP_AUTO_EXPOSURE,
        "sharpness": cv2.CAP_PROP_SHARPNESS,
        "gamma": cv2.CAP_PROP_GAMMA,
        "backlight": cv2.CAP_PROP_BACKLIGHT,
        "white_balance": cv2.CAP_PROP_WHITE_BALANCE_BLUE_U,
        "wb_temperature": cv2.CAP_PROP_WHITE_BALANCE_BLUE_U,
        "auto_wb": cv2.CAP_PROP_AUTO_WB,
        "autofocus": cv2.CAP_PROP_AUTOFOCUS,
        "focus": cv2.CAP_PROP_FOCUS,
        "zoom": cv2.CAP_PROP_ZOOM,
        "pan": cv2.CAP_PROP_PAN,
        "tilt": cv2.CAP_PROP_TILT,
    }

    def __init__(
        self,
        index: int,
        capture_width: int | None = None,
        capture_height: int | None = None,
        capture_fps: int | None = None,
        capture_fourcc: str | None = None,
        controls: dict[str, float] | None = None,
        capture_backend: str | None = None,
    ):
        self._index = index
        self._capture_width = capture_width
        self._capture_height = capture_height
        self._capture_fps = capture_fps
        self._capture_fourcc = capture_fourcc
        self._controls = dict(controls or {})
        self._capture_backend = (capture_backend or "").upper() or None
        self._cap: cv2.VideoCapture | None = None
        self._frame: np.ndarray | None = None
        self._frame_id: int = 0
        self._frames_read: int = 0
        self._read_failures: int = 0
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None

    @property
    def index(self) -> int:
        return self._index

    @property
    def frames_read(self) -> int:
        return self._frames_read

    @property
    def read_failures(self) -> int:
        return self._read_failures

    def _apply_capture_settings(
        self,
        cap: cv2.VideoCapture,
        width: int | None,
        height: int | None,
        fourcc: str | None,
    ) -> str:
        """Apply settings to `cap` and return the negotiated FOURCC string."""
        fourcc_int = 0
        if fourcc:
            try:
                fourcc_int = cv2.VideoWriter_fourcc(*fourcc.upper())
                cap.set(cv2.CAP_PROP_FOURCC, fourcc_int)
            except Exception:
                logger.warning("Camera %d: invalid FOURCC %r", self._index, fourcc)
                fourcc_int = 0
        if width:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(width))
        if height:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(height))
        if fourcc_int:
            cap.set(cv2.CAP_PROP_FOURCC, fourcc_int)
        if self._capture_fps:
            cap.set(cv2.CAP_PROP_FPS, float(self._capture_fps))
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        actual_fourcc_int = int(cap.get(cv2.CAP_PROP_FOURCC))
        return "".join(
            chr((actual_fourcc_int >> (8 * i)) & 0xFF) for i in range(4)
        ).strip()

    @staticmethod
    def _validate_capture(cap: cv2.VideoCapture) -> bool:
        """Try to grab a real frame. Some drivers report isOpened()==True but
        deliver no frames after a bad settings negotiation."""
        for _ in range(5):
            ok, frame = cap.read()
            if ok and frame is not None and frame.size > 0:
                return True
            time.sleep(0.05)
        return False

    def _apply_controls(self, cap: cv2.VideoCapture) -> None:
        """Apply user-supplied sensor controls (brightness, exposure, etc.).

        Auto-* properties are applied first so that subsequent manual values
        (exposure, focus, white balance) actually take effect."""
        if not self._controls:
            return
        # Order matters: turn auto-* off before setting manual targets.
        ordered = sorted(
            self._controls.items(),
            key=lambda kv: 0 if kv[0].lower().startswith("auto") else 1,
        )
        for raw_name, value in ordered:
            name = raw_name.lower()
            prop = self._CONTROL_PROPS.get(name)
            if prop is None:
                logger.warning(
                    "Camera %d: unknown control %r (known: %s)",
                    self._index, raw_name, ", ".join(sorted(self._CONTROL_PROPS)),
                )
                continue
            ok = cap.set(prop, float(value))
            actual = cap.get(prop)
            if ok:
                logger.info(
                    "Camera %d: set %s=%g (actual=%g)", self._index, name, value, actual,
                )
            else:
                logger.warning(
                    "Camera %d: driver rejected %s=%g (actual=%g)",
                    self._index, name, value, actual,
                )

    def _build_attempts(self) -> list[tuple[int | None, int | None, str | None, str]]:
        """Ordered list of (width, height, fourcc, label) to try.

        We try the requested combo first, then progressively smaller MJPG
        resolutions, then uncompressed at modest sizes (low USB bandwidth),
        and finally a no-op pass that just uses whatever the driver defaults
        to. Each attempt is validated by actually reading a frame."""
        wanted_w = self._capture_width
        wanted_h = self._capture_height
        wanted_fc = (self._capture_fourcc or "").upper() or None

        attempts: list[tuple[int | None, int | None, str | None, str]] = []
        seen: set[tuple] = set()

        def add(w, h, fc, label):
            key = (w, h, fc)
            if key in seen:
                return
            seen.add(key)
            attempts.append((w, h, fc, label))

        if wanted_w and wanted_h and wanted_fc:
            add(wanted_w, wanted_h, wanted_fc, f"{wanted_fc} {wanted_w}x{wanted_h} (requested)")
        if wanted_fc:
            for w, h in self._FALLBACK_RESOLUTIONS:
                if wanted_w and w >= wanted_w:
                    continue
                add(w, h, wanted_fc, f"{wanted_fc} {w}x{h} (fallback)")
        # Uncompressed fallbacks at low res so two cameras still fit on USB 2.0.
        for w, h in [(640, 480), (424, 240), (320, 240)]:
            add(w, h, None, f"uncompressed {w}x{h}")
        # Last resort: whatever the driver defaults to.
        add(None, None, None, "driver default")
        return attempts

    def open(self) -> bool:
        attempts = self._build_attempts()
        wanted_fc = (self._capture_fourcc or "").upper()
        backends = _CAPTURE_BACKENDS
        if self._capture_backend:
            backends = [(n, b) for (n, b) in _CAPTURE_BACKENDS if n == self._capture_backend]
            if not backends:
                logger.warning(
                    "Camera %d: unknown capture_backend %r; falling back to default order",
                    self._index, self._capture_backend,
                )
                backends = _CAPTURE_BACKENDS

        for name, backend in backends:
            cap = cv2.VideoCapture(self._index, backend)
            if not cap.isOpened():
                cap.release()
                continue

            chosen_label: str | None = None
            chosen_fourcc = ""
            for w, h, fc, label in attempts:
                actual_fc = self._apply_capture_settings(cap, w, h, fc)
                if not self._validate_capture(cap):
                    logger.debug(
                        "Camera %d: attempt %s did not produce frames", self._index, label,
                    )
                    continue
                if fc and actual_fc != fc.upper():
                    # Driver substituted a different format. Accept only if
                    # this is an uncompressed-fallback attempt (fc is None) or
                    # the last "driver default" attempt.
                    logger.debug(
                        "Camera %d: attempt %s wanted %s got %s; trying next",
                        self._index, label, fc, actual_fc,
                    )
                    continue
                chosen_label = label
                chosen_fourcc = actual_fc
                break

            if chosen_label is None:
                # Nothing in our attempts worked; release and try next backend.
                cap.release()
                continue

            actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            actual_fps = cap.get(cv2.CAP_PROP_FPS)
            self._cap = cap
            self._capture_width = actual_w or self._capture_width
            self._capture_height = actual_h or self._capture_height

            wanted_match = (not wanted_fc) or (chosen_fourcc == wanted_fc)
            level = logging.INFO if wanted_match else logging.WARNING
            logger.log(
                level,
                "Opened camera %d via %s backend: %s -> %dx%d @ %.1f fps fourcc=%s",
                self._index, name, chosen_label, actual_w, actual_h, actual_fps, chosen_fourcc,
            )
            if not wanted_match:
                logger.warning(
                    "Camera %d does not support FOURCC=%s; using uncompressed at %dx%d. "
                    "Bandwidth is %.1f MB/s — keep the other camera small too.",
                    self._index, wanted_fc, actual_w, actual_h,
                    actual_w * actual_h * 2 * (actual_fps or 30) / 1e6,
                )
            self._apply_controls(cap)
            return True

        logger.warning("Failed to open camera %d (tried DSHOW, MSMF, ANY)", self._index)
        return False

    def start(self):
        if self._cap is None or not self._cap.isOpened():
            return
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def _read_loop(self):
        consecutive_failures = 0
        while self._running:
            ret, frame = self._cap.read()
            if ret and frame is not None:
                with self._lock:
                    self._frame = frame
                    self._frame_id += 1
                    self._frames_read += 1
                consecutive_failures = 0
            else:
                self._read_failures += 1
                consecutive_failures += 1
                # Log first failure and then occasionally so we don't spam.
                if consecutive_failures == 1 or consecutive_failures % 100 == 0:
                    logger.warning(
                        "Camera %d read failed (consecutive=%d, total=%d)",
                        self._index, consecutive_failures, self._read_failures,
                    )
                # Avoid pegging CPU when the camera isn't producing frames.
                time.sleep(0.05)

    def latest_frame(self) -> tuple[np.ndarray | None, int]:
        with self._lock:
            if self._frame is None:
                return None, 0
            return self._frame.copy(), self._frame_id

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        if self._cap:
            self._cap.release()


class CameraManager:
    """Manages multiple camera readers and provides the active feed."""

    def __init__(
        self,
        camera_indices: list[int],
        output_size: tuple[int, int] = (1280, 720),
        capture_width: int | None = None,
        capture_height: int | None = None,
        capture_fps: int | None = None,
        capture_fourcc: str | None = None,
        overrides: dict | None = None,
    ):
        self._readers: dict[int, CameraReader] = {}
        self._output_size = output_size
        self._active_index: int | None = None
        self._lock = threading.Lock()
        overrides = overrides or {}

        for idx in camera_indices:
            ov = overrides.get(idx)
            reader = CameraReader(
                idx,
                capture_width=getattr(ov, "capture_width", None) or capture_width,
                capture_height=getattr(ov, "capture_height", None) or capture_height,
                capture_fps=getattr(ov, "capture_fps", None) or capture_fps,
                capture_fourcc=(
                    getattr(ov, "capture_fourcc", None)
                    if getattr(ov, "capture_fourcc", None) is not None
                    else capture_fourcc
                ),
                capture_backend=getattr(ov, "capture_backend", None),
                controls=getattr(ov, "controls", None),
            )
            if reader.open():
                self._readers[idx] = reader

        opened = list(self._readers.keys())
        if not opened:
            logger.error("No cameras could be opened from configured set %s", camera_indices)
        else:
            logger.info(
                "Opened %d/%d cameras: %s (configured: %s)",
                len(opened), len(camera_indices), opened, camera_indices,
            )

        if self._readers:
            self._active_index = opened[0]

    def start_readers(self):
        for reader in self._readers.values():
            reader.start()

    @property
    def active_index(self) -> int | None:
        return self._active_index

    @active_index.setter
    def active_index(self, idx: int):
        with self._lock:
            if idx in self._readers:
                self._active_index = idx

    @property
    def available_indices(self) -> list[int]:
        return list(self._readers.keys())

    def read_active(self) -> np.ndarray | None:
        """Get the latest frame from the active camera, resized to output dimensions."""
        with self._lock:
            if self._active_index is None:
                return None
            reader = self._readers.get(self._active_index)
        if reader is None:
            return None
        frame, _ = reader.latest_frame()
        if frame is None:
            return None
        return cv2.resize(frame, self._output_size)

    def read_camera(self, idx: int) -> tuple[np.ndarray | None, int]:
        """Get the latest frame from a specific camera and its frame id."""
        reader = self._readers.get(idx)
        if reader is None:
            return None, 0
        return reader.latest_frame()

    def reader_stats(self) -> dict[int, tuple[int, int]]:
        """Return per-camera (frames_read, read_failures)."""
        return {idx: (r.frames_read, r.read_failures) for idx, r in self._readers.items()}

    def close(self):
        for reader in self._readers.values():
            reader.stop()
        self._readers.clear()
