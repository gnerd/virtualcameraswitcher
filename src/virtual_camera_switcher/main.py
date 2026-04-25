import argparse
import logging
import os
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .cameras import CameraManager, enumerate_cameras
from .config import AppConfig, CONFIG_DIR
from .gaze import GazeDetector
from .switcher import GazeSwitcher
from .tray import TrayApp
from .virtual_cam import VirtualCameraOutput


def _configure_logging(to_file: bool = False) -> None:
    """Configure root logging. When running under pythonw.exe stderr is None,
    so we must not attach a StreamHandler to it. When `to_file` is set we
    also log to a rotating file in the config dir."""
    handlers: list[logging.Handler] = []
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    if to_file or sys.stderr is None:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            handlers.append(
                RotatingFileHandler(
                    CONFIG_DIR / "vcs.log",
                    maxBytes=512 * 1024,
                    backupCount=2,
                    encoding="utf-8",
                )
            )
        except Exception:
            pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


# Module-level state for the on-demand timestamped log file (toggled from
# the system tray). Off by default.
_LOG_DIR = CONFIG_DIR / "logs"
_session_log_handler: logging.Handler | None = None
_session_log_path: Path | None = None


def enable_session_logging() -> Path | None:
    """Start writing INFO+ logs to a timestamped file in the logs dir.

    Returns the file path, or None if logging is already enabled or setup
    failed. Safe to call from any thread."""
    global _session_log_handler, _session_log_path
    if _session_log_handler is not None:
        return _session_log_path
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%d_%H-%M-%S")
        path = _LOG_DIR / f"vcs-{ts}.log"
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))
        handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(handler)
        _session_log_handler = handler
        _session_log_path = path
        logging.getLogger(__name__).info("Session logging enabled: %s", path)
        return path
    except Exception:
        logging.getLogger(__name__).exception("Failed to enable session logging")
        return None


def disable_session_logging() -> None:
    """Stop the timestamped session log file (if any)."""
    global _session_log_handler, _session_log_path
    handler = _session_log_handler
    if handler is None:
        return
    logging.getLogger(__name__).info("Session logging disabled: %s", _session_log_path)
    logging.getLogger().removeHandler(handler)
    try:
        handler.close()
    except Exception:
        pass
    _session_log_handler = None
    _session_log_path = None


def session_log_path() -> Path | None:
    return _session_log_path


_configure_logging()
logger = logging.getLogger(__name__)


class App:
    def __init__(self, config: AppConfig):
        self.config = config
        self.running = False
        self.starting = False
        self._output_thread: threading.Thread | None = None
        self._coordinator_thread: threading.Thread | None = None
        self._start_thread: threading.Thread | None = None
        self._camera_manager: CameraManager | None = None
        self._virtual_cam: VirtualCameraOutput | None = None
        self._detector: GazeDetector | None = None
        self._switcher: GazeSwitcher | None = None
        self._active_camera: int | None = None
        self._on_camera_change: callable | None = None
        self._on_state_change: callable | None = None
        # Stats (only touched by coordinator thread + read for logging).
        self._frames_processed: dict[int, int] = {}
        self._faces_seen: dict[int, int] = {}
        self._last_yaw: dict[int, float | None] = {}

    def _notify_state(self):
        cb = self._on_state_change
        if cb:
            try:
                cb()
            except Exception:
                logger.exception("on_state_change callback failed")

    def start_pipeline_async(self):
        """Kick off pipeline startup in a background thread so the tray UI can
        appear immediately. The tray will show a yellow 'Starting...' icon
        until startup completes."""
        if self.starting or self.running:
            return
        self.starting = True
        self._notify_state()
        self._start_thread = threading.Thread(
            target=self._start_pipeline_worker, daemon=True
        )
        self._start_thread.start()

    def _start_pipeline_worker(self):
        try:
            self.start_pipeline()
        except Exception:
            logger.exception("Pipeline startup failed")
        finally:
            self.starting = False
            self._notify_state()

    def start_pipeline(self):
        """Start the camera switching pipeline in background threads."""
        if not self.config.cameras:
            logger.error("No cameras configured. Run with --setup first.")
            return

        self._camera_manager = CameraManager(
            self.config.cameras,
            (self.config.output_width, self.config.output_height),
            capture_width=self.config.capture_width,
            capture_height=self.config.capture_height,
            capture_fps=self.config.capture_fps,
            capture_fourcc=self.config.capture_fourcc,
            overrides=self.config.camera_overrides,
        )
        available = self._camera_manager.available_indices
        if not available:
            logger.error("No cameras could be opened; aborting pipeline start.")
            return
        if len(available) < len(self.config.cameras):
            logger.warning(
                "Only %d/%d configured cameras opened; switching limited to %s",
                len(available), len(self.config.cameras), available,
            )

        self._virtual_cam = VirtualCameraOutput(
            self.config.output_width,
            self.config.output_height,
            self.config.output_fps,
            device=self.config.virtual_camera_name or None,
            backend=self.config.virtual_camera_backend or None,
        )

        # One detector for the whole app. MediaPipe is not thread-safe, so the
        # coordinator thread is the only thing that ever calls it.
        self._detector = GazeDetector(max_width=self.config.detect_max_width)

        self._switcher = GazeSwitcher(
            available,
            active_index=available[0],
            locked_fps=self.config.detect_fps_locked,
            searching_fps=self.config.detect_fps_searching,
            look_away_yaw_deg=self.config.look_away_yaw_deg,
            look_away_grace_s=self.config.look_away_grace_s,
            search_timeout_s=self.config.search_timeout_s,
        )
        self._active_camera = self._switcher.active
        self._camera_manager.active_index = self._active_camera

        self._frames_processed = {idx: 0 for idx in available}
        self._faces_seen = {idx: 0 for idx in available}
        self._last_yaw = {idx: None for idx in available}

        self._camera_manager.start_readers()
        self._virtual_cam.start()
        self.running = True

        self._coordinator_thread = threading.Thread(
            target=self._coordinator_loop, daemon=True,
        )
        self._coordinator_thread.start()

        self._output_thread = threading.Thread(target=self._output_loop, daemon=True)
        self._output_thread.start()
        logger.info(
            "Pipeline started (1 coordinator + 1 output thread; locked@%.1fHz searching@%.1fHz)",
            self.config.detect_fps_locked, self.config.detect_fps_searching,
        )

    def _coordinator_loop(self):
        """Single-threaded gaze detection driven by the switcher state machine."""
        last_stats = time.monotonic()
        # Snapshot per-camera yaw offsets up front; calibration updates these
        # via reload_yaw_offsets() so we always see the latest.
        try:
            while self.running:
                targets, sleep_after = self._switcher.next_targets()

                results: dict[int, float | None] = {}
                for idx in targets:
                    frame, _ = self._camera_manager.read_camera(idx)
                    if frame is None:
                        results[idx] = None
                        continue
                    raw_yaw = self._detector.detect_yaw(frame)
                    self._frames_processed[idx] += 1
                    if raw_yaw is None:
                        results[idx] = None
                        self._last_yaw[idx] = None
                        continue
                    self._faces_seen[idx] += 1
                    offset = self.config.override_for(idx).yaw_offset
                    adjusted = raw_yaw - offset
                    results[idx] = adjusted
                    self._last_yaw[idx] = adjusted

                new_active = self._switcher.submit(results)
                if new_active is not None and new_active != self._active_camera:
                    self._active_camera = new_active
                    self._camera_manager.active_index = new_active
                    if self._on_camera_change:
                        self._on_camera_change(new_active)

                # Periodic stats so we can see the state machine working.
                now = time.monotonic()
                if now - last_stats >= 5.0:
                    last_stats = now
                    reader_stats = self._camera_manager.reader_stats()
                    parts = []
                    for idx in sorted(reader_stats):
                        frames, failures = reader_stats[idx]
                        y = self._last_yaw.get(idx)
                        y_str = f"{y:+.1f}" if y is not None else "no-face"
                        parts.append(
                            f"cam{idx}: read={frames} fail={failures} "
                            f"detect={self._frames_processed.get(idx, 0)} "
                            f"faces={self._faces_seen.get(idx, 0)} yaw={y_str}"
                        )
                    logger.info(
                        "state=%s active=%s | %s",
                        self._switcher.state.value, self._active_camera, " | ".join(parts),
                    )

                if sleep_after > 0:
                    time.sleep(sleep_after)
        except Exception:
            logger.exception("Coordinator loop error")

    def _output_loop(self):
        """Send frames to the virtual camera at the configured fps."""
        try:
            while self.running:
                frame = self._camera_manager.read_active()
                if frame is not None:
                    self._virtual_cam.send_frame(frame)
                else:
                    time.sleep(1.0 / self.config.output_fps)
        except Exception:
            logger.exception("Output loop error")

    def stop_pipeline(self):
        self.running = False
        if self._output_thread:
            self._output_thread.join(timeout=5)
        if self._coordinator_thread:
            self._coordinator_thread.join(timeout=5)
        if self._virtual_cam:
            self._virtual_cam.close()
        if self._camera_manager:
            self._camera_manager.close()
        if self._detector:
            self._detector.close()
            self._detector = None
        logger.info("Pipeline stopped")
        self._notify_state()

    def save_snapshots(self, out_dir: Path | None = None) -> list[Path]:
        """Write the latest frame from each opened camera to disk for
        debugging. Files are named `snapshot-cam{idx}-{timestamp}.png`."""
        if not self._camera_manager:
            logger.warning("save_snapshots: pipeline not running")
            return []
        # Lazy import so this module's top-level remains light.
        import cv2
        if out_dir is None:
            out_dir = CONFIG_DIR / "snapshots"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%d_%H-%M-%S")
        written: list[Path] = []
        for idx in self._camera_manager.available_indices:
            frame, _ = self._camera_manager.read_camera(idx)
            if frame is None:
                logger.warning("save_snapshots: cam%d has no frame yet", idx)
                continue
            path = out_dir / f"snapshot-cam{idx}-{ts}.png"
            try:
                cv2.imwrite(str(path), frame)
                written.append(path)
                logger.info("Saved snapshot %s (%dx%d)", path, frame.shape[1], frame.shape[0])
            except Exception:
                logger.exception("Failed to write snapshot %s", path)
        return written

    def calibrate_camera(
        self,
        camera_index: int,
        duration_s: float = 2.0,
        countdown_s: float = 2.5,
        on_countdown_tick: callable = None,
    ) -> float | None:
        """Sample yaw on `camera_index` for `duration_s` seconds (user should
        be looking straight at it) and store the median raw yaw as that
        camera's `yaw_offset`. A `countdown_s` delay runs first so the user
        can look back at the camera after clicking the menu; `on_countdown_tick`
        is called once per second of countdown with the integer seconds
        remaining (use it to beep / log)."""
        if not self.running or not self._detector or not self._camera_manager:
            logger.warning("calibrate_camera: pipeline not running")
            return None
        if countdown_s > 0:
            logger.info("Calibrating cam%d in %.1fs — look at it", camera_index, countdown_s)
            ticks = max(1, int(round(countdown_s)))
            for remaining in range(ticks, 0, -1):
                if on_countdown_tick:
                    try:
                        on_countdown_tick(remaining)
                    except Exception:
                        logger.exception("countdown tick callback failed")
                time.sleep(countdown_s / ticks)
            if on_countdown_tick:
                try:
                    on_countdown_tick(0)
                except Exception:
                    pass
        logger.info("Calibrating cam%d over %.1fs — hold still", camera_index, duration_s)
        samples: list[float] = []
        deadline = time.monotonic() + duration_s
        last_seen_id = -1
        while time.monotonic() < deadline:
            frame, frame_id = self._camera_manager.read_camera(camera_index)
            if frame is None or frame_id == last_seen_id:
                time.sleep(0.05)
                continue
            last_seen_id = frame_id
            yaw = self._detector.detect_yaw(frame)
            if yaw is not None:
                samples.append(yaw)
            time.sleep(0.05)
        if len(samples) < 3:
            logger.warning(
                "calibrate_camera: cam%d only produced %d face samples; aborting",
                camera_index, len(samples),
            )
            return None
        samples.sort()
        median = samples[len(samples) // 2]
        self.config.set_yaw_offset(camera_index, median)
        try:
            self.config.save()
        except Exception:
            logger.exception("calibrate_camera: failed to persist config")
        logger.info(
            "Calibrated cam%d: yaw_offset=%+.2f (median of %d samples)",
            camera_index, median, len(samples),
        )
        return median

    @property
    def active_camera(self) -> int | None:
        return self._active_camera

    def toggle(self):
        if self.starting:
            return
        if self.running:
            self.stop_pipeline()
        else:
            self.start_pipeline_async()


def interactive_setup() -> AppConfig:
    """Interactive first-time setup."""
    print("\n=== Virtual Camera Switcher - Setup ===\n")

    print("Scanning for cameras...")
    cameras = enumerate_cameras()
    if not cameras:
        print("No cameras found!")
        sys.exit(1)

    print(f"\nFound {len(cameras)} camera(s):")
    for cam in cameras:
        print(f"  [{cam.index}] {cam.name} ({cam.width}x{cam.height})")

    print("\nEnter camera indices to use (comma-separated), or 'all':")
    choice = input("> ").strip()
    if choice.lower() == "all":
        selected = [c.index for c in cameras]
    else:
        selected = [int(x.strip()) for x in choice.split(",")]

    print("\nVirtual camera name (press Enter for default):")
    name = input("> ").strip()

    config = AppConfig(cameras=selected)
    if name:
        config.virtual_camera_name = name
    config.save()
    print(f"\nConfig saved. {len(selected)} cameras configured.")
    print("Run 'vcs' to start the switcher.\n")
    return config


def main():
    parser = argparse.ArgumentParser(description="Virtual Camera Switcher")
    parser.add_argument("--setup", action="store_true", help="Run interactive setup")
    parser.add_argument("--no-tray", action="store_true", help="Run without system tray (console only)")
    args = parser.parse_args()

    if args.setup:
        config = interactive_setup()
    else:
        config = AppConfig.load()

    if not config.cameras:
        print("No cameras configured. Run with --setup first.")
        sys.exit(1)

    app = App(config)

    if args.no_tray:
        app.start_pipeline()
        print("Pipeline running. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            app.stop_pipeline()
    else:
        # Show tray immediately; start the (slow) pipeline in the background.
        tray = TrayApp(
            config,
            app,
            on_quit=lambda: (app.stop_pipeline(), tray.stop()),
        )
        app.start_pipeline_async()
        tray.run()


def main_gui():
    """Windowless entry point for `vcsw` (no console).

    File logging is OFF by default — enable it from the tray menu when you
    want a session log written to disk."""
    config = AppConfig.load()
    if not config.cameras:
        logger.error(
            "No cameras configured. Run `vcs --setup` from a terminal first."
        )
        return
    app = App(config)
    tray = TrayApp(
        config,
        app,
        on_quit=lambda: (app.stop_pipeline(), tray.stop()),
    )
    app.start_pipeline_async()
    tray.run()


if __name__ == "__main__":
    main()
