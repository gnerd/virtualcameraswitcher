import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".virtual-camera-switcher"
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class CameraOverride:
    """Per-camera override of capture format and sensor controls.

    `capture_*` fields, when set, override the global defaults for this camera.
    `capture_backend` forces a specific OpenCV capture backend for this camera
    ("DSHOW", "MSMF", or "ANY"). Some cameras (e.g. AI cameras like the OBSBOT
    series) only honor MJPG/H264 under MSMF.
    `controls` is a free-form dict of OpenCV CAP_PROP_* property names (without
    the prefix, case-insensitive) mapped to numeric values. Examples:
        {"autofocus": 0, "focus": 30, "auto_exposure": 1, "exposure": -6,
         "gain": 50, "sharpness": 160, "saturation": 140, "contrast": 140,
         "brightness": 128}
    """
    capture_width: int | None = None
    capture_height: int | None = None
    capture_fps: int | None = None
    capture_fourcc: str | None = None
    capture_backend: str | None = None
    controls: dict[str, float] = field(default_factory=dict)


@dataclass
class AppConfig:
    cameras: list[int] = field(default_factory=list)
    output_width: int = 1280
    output_height: int = 720
    output_fps: int = 30
    hysteresis_frames: int = 10
    virtual_camera_name: str = ""
    virtual_camera_backend: str = ""
    # Per-camera capture settings. These are applied to every input camera.
    # MJPG is strongly recommended on Windows when using multiple USB cameras
    # to avoid USB 2.0 bandwidth starvation.
    capture_width: int = 1280
    capture_height: int = 720
    capture_fps: int = 30
    capture_fourcc: str = "MJPG"
    # Per-camera overrides keyed by camera index (as int). In JSON the keys
    # may be either ints or strings; both are accepted.
    camera_overrides: dict[int, CameraOverride] = field(default_factory=dict)
    # Detection (gaze tracking) tuning. Detection is gated by a state machine:
    # while LOCKED on a camera we only check that camera at `detect_fps_locked`.
    # If the user looks away for `look_away_grace_s` seconds, we switch to
    # SEARCHING and scan the other cameras at `detect_fps_searching`.
    detect_fps_locked: float = 4.0
    detect_fps_searching: float = 10.0
    detect_max_width: int = 480
    look_away_yaw_deg: float = 25.0
    look_away_grace_s: float = 1.0
    search_timeout_s: float = 3.0

    def override_for(self, camera_index: int) -> CameraOverride:
        return self.camera_overrides.get(camera_index, CameraOverride())

    def save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        # Re-key camera_overrides as strings so the JSON is portable.
        data["camera_overrides"] = {
            str(k): asdict(v) if not isinstance(v, dict) else v
            for k, v in self.camera_overrides.items()
        }
        with open(CONFIG_FILE, "w") as f:
            json.dump(data, f, indent=2)
        logger.info("Config saved to %s", CONFIG_FILE)

    @classmethod
    def load(cls) -> "AppConfig":
        if not CONFIG_FILE.exists():
            return cls()
        try:
            with open(CONFIG_FILE) as f:
                data = json.load(f)
            known = {f.name for f in cls.__dataclass_fields__.values()}
            data = {k: v for k, v in data.items() if k in known}
            data["camera_overrides"] = _parse_overrides(data.get("camera_overrides", {}))
            return cls(**data)
        except Exception:
            logger.exception("Failed to load config, using defaults")
            return cls()


def _parse_overrides(raw: Any) -> dict[int, CameraOverride]:
    if not isinstance(raw, dict):
        return {}
    out: dict[int, CameraOverride] = {}
    known = {f.name for f in CameraOverride.__dataclass_fields__.values()}
    for key, value in raw.items():
        try:
            idx = int(key)
        except (TypeError, ValueError):
            logger.warning("Ignoring camera_overrides key %r (not an int)", key)
            continue
        if not isinstance(value, dict):
            logger.warning("Ignoring camera_overrides[%s] (not an object)", key)
            continue
        filtered = {k: v for k, v in value.items() if k in known}
        controls = filtered.get("controls", {}) or {}
        if not isinstance(controls, dict):
            logger.warning("Ignoring camera_overrides[%s].controls (not an object)", key)
            controls = {}
        filtered["controls"] = {str(k): float(v) for k, v in controls.items()}
        out[idx] = CameraOverride(**filtered)
    return out
