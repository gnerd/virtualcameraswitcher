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

    def __init__(self, config: AppConfig, app, on_quit: callable):
        self._config = config
        self._app = app
        self._on_quit = on_quit
        self._icon: pystray.Icon | None = None
        self._active_camera: int | None = app.active_camera

        # Register for camera change notifications
        app._on_camera_change = self._on_camera_changed

    def _on_camera_changed(self, camera_index: int):
        self._active_camera = camera_index
        if self._icon:
            self._icon.update_menu()

    def _build_menu(self):
        camera_items = []
        for idx in self._config.cameras:
            is_active = idx == self._active_camera
            label = f"Camera {idx}"
            if is_active:
                label = f"● Camera {idx} (active)"
            camera_items.append(
                pystray.MenuItem(label, None, enabled=False)
            )

        status = "Running" if self._app.running else "Stopped"

        return pystray.Menu(
            pystray.MenuItem(f"Virtual Camera Switcher — {status}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            *camera_items,
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Pause" if self._app.running else "Resume",
                lambda: self._toggle(),
            ),
            pystray.MenuItem("Quit", lambda: self._on_quit()),
        )

    def _toggle(self):
        self._app.toggle()
        if self._icon:
            self._icon.icon = _create_icon_image("green" if self._app.running else "red")
            self._icon.update_menu()

    def run(self):
        self._icon = pystray.Icon(
            "VirtualCameraSwitcher",
            icon=_create_icon_image("green"),
            title="Virtual Camera Switcher",
            menu=self._build_menu(),
        )
        self._icon.run()

    def stop(self):
        if self._icon:
            self._icon.stop()
