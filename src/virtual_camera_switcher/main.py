import argparse
import logging
import sys
import threading
import time

from .cameras import CameraManager, enumerate_cameras
from .config import AppConfig
from .gaze import GazeDetector
from .switcher import CameraSwitcher
from .tray import TrayApp
from .virtual_cam import VirtualCameraOutput

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class App:
    def __init__(self, config: AppConfig):
        self.config = config
        self.running = False
        self._pipeline_thread: threading.Thread | None = None
        self._camera_manager: CameraManager | None = None
        self._virtual_cam: VirtualCameraOutput | None = None
        self._gaze_detector: GazeDetector | None = None
        self._switcher: CameraSwitcher | None = None
        self._active_camera: int | None = None
        self._on_camera_change: callable | None = None

    def start_pipeline(self):
        """Start the camera switching pipeline in a background thread."""
        if not self.config.cameras:
            logger.error("No cameras configured. Run with --setup first.")
            return

        self._camera_manager = CameraManager(
            self.config.cameras,
            (self.config.output_width, self.config.output_height),
        )
        self._virtual_cam = VirtualCameraOutput(
            self.config.output_width,
            self.config.output_height,
            self.config.output_fps,
            device=self.config.virtual_camera_name,
        )
        self._gaze_detector = GazeDetector()
        self._switcher = CameraSwitcher(
            self.config.cameras, self.config.hysteresis_frames,
        )
        self._active_camera = self._switcher.current_camera_index

        self._virtual_cam.start()
        self.running = True
        self._pipeline_thread = threading.Thread(target=self._run_pipeline, daemon=True)
        self._pipeline_thread.start()
        logger.info("Pipeline started")

    def _run_pipeline(self):
        try:
            while self.running:
                # Read all cameras and detect yaw on each
                yaw_by_camera: dict[int, float | None] = {}
                for idx in self._camera_manager.available_indices:
                    frame = self._camera_manager.read_camera(idx)
                    if frame is not None:
                        yaw_by_camera[idx] = self._gaze_detector.detect_yaw(frame)
                    else:
                        yaw_by_camera[idx] = None

                # Determine which camera to use
                best = self._switcher.update(yaw_by_camera)
                if best is not None and best != self._active_camera:
                    self._active_camera = best
                    self._camera_manager.active_index = best
                    if self._on_camera_change:
                        self._on_camera_change(best)

                # Send active camera frame to virtual camera
                frame = self._camera_manager.read_active()
                if frame is not None:
                    self._virtual_cam.send_frame(frame)
        except Exception:
            logger.exception("Pipeline error")
        finally:
            pass

    def stop_pipeline(self):
        self.running = False
        if self._pipeline_thread:
            self._pipeline_thread.join(timeout=5)
        if self._virtual_cam:
            self._virtual_cam.close()
        if self._camera_manager:
            self._camera_manager.close()
        if self._gaze_detector:
            self._gaze_detector.close()
        logger.info("Pipeline stopped")

    @property
    def active_camera(self) -> int | None:
        return self._active_camera

    def toggle(self):
        if self.running:
            self.stop_pipeline()
        else:
            self.start_pipeline()


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
        app.start_pipeline()
        tray = TrayApp(
            config,
            app,
            on_quit=lambda: (app.stop_pipeline(), tray.stop()),
        )
        tray.run()


if __name__ == "__main__":
    main()
