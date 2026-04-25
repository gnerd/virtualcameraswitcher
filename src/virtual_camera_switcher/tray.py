import logging
import os
import subprocess
import sys
import threading
from functools import partial
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

from .config import AppConfig, CONFIG_DIR

logger = logging.getLogger(__name__)


def _create_icon_image(color: str = "green") -> Image.Image:
    """Create a simple colored circle icon."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    fill = {
        "green": (0, 200, 0),
        "red": (200, 0, 0),
        "yellow": (220, 180, 0),
        "gray": (130, 130, 130),
    }.get(color, (0, 200, 0))
    draw.ellipse([8, 8, 56, 56], fill=fill)
    return img


def _icon_for_state(starting: bool, running: bool) -> str:
    if starting:
        return "yellow"
    if running:
        return "green"
    return "red"


class TrayApp:
    """System tray application for Virtual Camera Switcher."""

    def __init__(self, config: AppConfig, app, on_quit: callable):
        self._config = config
        self._app = app
        self._on_quit = on_quit
        self._icon: pystray.Icon | None = None
        self._active_camera: int | None = app.active_camera

        # Register for app notifications
        app._on_camera_change = self._on_camera_changed
        app._on_state_change = self._on_state_changed

    def _refresh(self):
        if not self._icon:
            return
        self._icon.icon = _create_icon_image(
            _icon_for_state(self._app.starting, self._app.running)
        )
        self._icon.title = self._status_text()
        self._icon.update_menu()

    def _on_camera_changed(self, camera_index: int):
        self._active_camera = camera_index
        self._refresh()

    def _on_state_changed(self):
        self._active_camera = self._app.active_camera
        self._refresh()

    def _status_text(self) -> str:
        if self._app.starting:
            return "Virtual Camera Switcher — Starting…"
        if self._app.running:
            return "Virtual Camera Switcher — Running"
        return "Virtual Camera Switcher — Stopped"

    def _build_menu(self):
        # All `text` and `enabled` values are callables so pystray re-evaluates
        # them every time the menu is opened. Without this, the menu would
        # freeze in whatever state existed when the icon was first created.
        camera_items = [
            pystray.MenuItem(
                partial(self._camera_label, idx),
                None,
                enabled=False,
            )
            for idx in self._config.cameras
        ]

        return pystray.Menu(
            pystray.MenuItem(lambda _i: self._status_text(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            *camera_items,
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda _i: self._toggle_label(),
                lambda _i: self._toggle(),
                enabled=lambda _i: not self._app.starting,
            ),
            pystray.MenuItem(
                "Calibrate camera",
                pystray.Menu(*self._calibration_items()),
                enabled=lambda _i: self._app.running,
            ),
            pystray.MenuItem(
                "Save snapshots",
                lambda _i: self._save_snapshots(),
                enabled=lambda _i: self._app.running,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Log to file",
                lambda _i: self._toggle_logging(),
                checked=lambda _i: self._is_logging_enabled(),
            ),
            pystray.MenuItem(
                "Open logs folder",
                lambda _i: self._open_logs_folder(),
            ),
            pystray.MenuItem("Quit", lambda _i: self._on_quit()),
        )

    def _camera_label(self, idx: int, _item=None) -> str:
        active = idx == self._active_camera
        prefix = "● " if active else "  "
        suffix = " (active)" if active else ""
        return f"{prefix}Camera {idx}{suffix}"

    def _toggle_label(self) -> str:
        if self._app.starting:
            return "Starting…"
        return "Pause" if self._app.running else "Resume"

    def _calibration_items(self):
        items = []
        for idx in self._config.cameras:
            items.append(pystray.MenuItem(
                f"Camera {idx}: look at it then click",
                partial(self._calibrate_menu_action, idx),
            ))
        if not items:
            items.append(pystray.MenuItem("(no cameras)", None, enabled=False))
        return items

    def _calibrate_menu_action(self, camera_index: int, _item=None):
        self._calibrate(camera_index)

    def _calibrate(self, camera_index: int):
        # Calibration takes ~2s and should not block the pystray UI thread.
        threading.Thread(
            target=self._calibrate_worker, args=(camera_index,), daemon=True,
        ).start()

    def _calibrate_worker(self, camera_index: int):
        try:
            offset = self._app.calibrate_camera(
                camera_index,
                on_countdown_tick=self._calibration_beep,
            )
            if offset is None:
                logger.warning("Calibration of cam%d failed (no face detected)", camera_index)
                self._calibration_beep(-1)  # error tone
            else:
                logger.info("Calibration of cam%d done: yaw_offset=%+.2f", camera_index, offset)
                self._calibration_beep(-2)  # success tone
        except Exception:
            logger.exception("Calibration error")

    @staticmethod
    def _calibration_beep(remaining: int) -> None:
        """Audible cue during calibration. Negative codes are special:
        -1 = error (low tone), -2 = success (rising chime)."""
        try:
            if sys.platform.startswith("win"):
                import winsound
                if remaining > 0:
                    winsound.Beep(700, 120)         # tick
                elif remaining == 0:
                    winsound.Beep(1200, 250)        # "GO — hold still"
                elif remaining == -1:
                    winsound.Beep(300, 400)         # error
                else:  # -2 success
                    winsound.Beep(1000, 100)
                    winsound.Beep(1400, 180)
            else:
                # Cross-platform fallback: terminal bell.
                sys.stdout.write("\a")
                sys.stdout.flush()
        except Exception:
            pass

    def _save_snapshots(self):
        threading.Thread(target=self._save_snapshots_worker, daemon=True).start()

    def _save_snapshots_worker(self):
        try:
            paths = self._app.save_snapshots()
            if paths and sys.platform.startswith("win"):
                try:
                    os.startfile(str(paths[0].parent))  # type: ignore[attr-defined]
                except Exception:
                    pass
        except Exception:
            logger.exception("Snapshot error")

    def _is_logging_enabled(self) -> bool:
        # Imported lazily to avoid a circular import at module load time.
        from .main import session_log_path
        return session_log_path() is not None

    def _toggle_logging(self):
        from .main import enable_session_logging, disable_session_logging
        if self._is_logging_enabled():
            disable_session_logging()
        else:
            path = enable_session_logging()
            if path is not None:
                logger.info("Logging to %s", path)
        self._refresh()

    def _open_logs_folder(self):
        logs_dir = CONFIG_DIR / "logs"
        try:
            logs_dir.mkdir(parents=True, exist_ok=True)
            if sys.platform.startswith("win"):
                os.startfile(str(logs_dir))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(logs_dir)])
            else:
                subprocess.Popen(["xdg-open", str(logs_dir)])
        except Exception:
            logger.exception("Failed to open logs folder %s", logs_dir)

    def _toggle(self):
        if self._app.starting:
            return
        self._app.toggle()
        self._refresh()

    def run(self):
        self._icon = pystray.Icon(
            "VirtualCameraSwitcher",
            icon=_create_icon_image(
                _icon_for_state(self._app.starting, self._app.running)
            ),
            title=self._status_text(),
            menu=self._build_menu(),
        )
        self._icon.run()

    def stop(self):
        if self._icon:
            self._icon.stop()
