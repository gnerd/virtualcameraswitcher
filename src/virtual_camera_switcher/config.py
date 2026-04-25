import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".virtual-camera-switcher"
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class AppConfig:
    cameras: list[int] = field(default_factory=list)
    output_width: int = 1280
    output_height: int = 720
    output_fps: int = 30
    hysteresis_frames: int = 10
    virtual_camera_name: str = "Virtual Camera Switcher"

    def save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
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
            return cls(**data)
        except Exception:
            logger.exception("Failed to load config, using defaults")
            return cls()
