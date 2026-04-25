import logging
import threading
from functools import partial

import pystray
from PIL import Image, ImageDraw

from .config import AppConfig

logger = logging.getLogger(__name__)


def _create_icon_image(color: str = "green") -> Image.Image:
    """Create a simple colored circle icon."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    fill = {"green": (0, 200, 0), "red": (200, 0, 0), "yellow": (200, 200, 0)}.get(color, (0, 200, 0))
    draw.ellipse([8, 8, 56, 56], fill=fill)
    return img


class TrayApp:
    """System tray application for Virtual Camera Switcher."""

    def __init__(
        self,
        config: AppConfig,
        on_calibrate: callable,
        on_toggle: callable,
        on_quit: callable,
    ):
        self._config = config
        self._on_calibrate = on_calibrate
        self._on_toggle = on_toggle
        self._on_quit = on_quit
        self._running = True
        self._icon: pystray.Icon | None = None

    def _build_menu(self):
        return pystray.Menu(
            pystray.MenuItem("Virtual Camera Switcher", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Calibrate", lambda: self._on_calibrate()),
            pystray.MenuItem("Toggle Active", lambda: self._on_toggle()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda: self._on_quit()),
        )

    def run(self):
        self._icon = pystray.Icon(
            "VirtualCameraSwitcher",
            icon=_create_icon_image("green"),
            title="Virtual Camera Switcher",
            menu=self._build_menu(),
        )
        self._icon.run()

    def set_status(self, color: str):
        if self._icon:
            self._icon.icon = _create_icon_image(color)

    def stop(self):
        if self._icon:
            self._icon.stop()
