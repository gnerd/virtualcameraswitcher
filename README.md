---
title: Virtual Camera Switcher
description: >-
  A Windows 11 application that uses head-pose gaze detection to
  automatically switch between physical cameras, outputting the active
  feed through a virtual camera device.
---

## Overview

Virtual Camera Switcher detects which physical camera you are looking at
and automatically switches the virtual camera feed to that camera in real
time. Video-calling apps such as Zoom, Teams, and Discord see a single
virtual camera that seamlessly changes its source as you turn your head.

## How it works

1. **MediaPipe FaceLandmarker** detects facial landmarks on frames from
   each configured camera.
2. **OpenCV `RQDecomp3x3`** converts the landmarks' rotation matrix into
   a head-pose yaw angle.
3. A **two-state switcher** (`LOCKED` / `SEARCHING`) holds on the active
   camera until your yaw exceeds a threshold for a brief grace period,
   then quickly scans the other cameras to pick the one you're now
   facing most directly.
4. The active camera's frames are pushed to a **pyvirtualcam** virtual
   camera device that appears as a regular webcam in any application.

Because only one camera is being run through face detection at a time
(the active one, at a low FPS, until you look away), CPU usage stays
low and USB bandwidth on the inactive cameras is freed up.
## Prerequisites

- Windows 10 or 11
- Python 3.10+
- A virtual camera backend supported by pyvirtualcam (OBS Virtual Camera
  or Unity Capture)

## Installation

```cmd
git clone https://github.com/gnerd/virtualcameraswitcher.git
cd virtualcameraswitcher
```

Then **right-click `install.bat` → Run as administrator**. The installer will:

1. Register the bundled Unity Capture DirectShow filter (`filters\UnityCaptureFilter64.dll`).
2. Install the Python package and dependencies (`pip install -e .`).
3. Download the MediaPipe face-landmarker model on first run.

Nothing else has to be downloaded or registered manually.

## Usage

### First-time setup

Scan for cameras and choose which ones to include:

```bash
vcs --setup
```

### Run

Start the virtual camera switcher as a system-tray application:

```bash
vcs       # console version (shows logs in a terminal window)
vcsw      # windowless version (recommended; no terminal window)
```

Use `vcs --no-tray` to run in console-only mode without a tray icon.

### Tray menu

Once running, the tray icon offers:

- **Status** — yellow = starting, green = running, red = stopped.
- **Camera list** — each camera shown by its Windows device name (for
  example `cam0: HD Pro Webcam C920`); a dot marks the active feed.
- **Pause / Resume** — stop or restart the pipeline without quitting.
- **Calibrate camera ▸ cam{N}** — look directly at the chosen camera
  and click; you'll hear three short beeps, a higher "go" tone, and a
  rising chime when calibration succeeds. The yaw offset is saved per
  camera so its baseline reads as 0° even if it's mounted at an angle.
  A frame from the sample window is written to
  `~/.virtual-camera-switcher/snapshots/calibrate-cam{N}-{ts}.png` so
  you can verify what was captured.
- **Save snapshots** — grab the current frame from every camera into
  `~/.virtual-camera-switcher/snapshots/` (handy for diagnosing aim,
  framing, or focus).
- **Log to file** — toggle a per-session log under
  `~/.virtual-camera-switcher/logs/vcs-{ts}.log`.
- **Open logs folder** — opens that directory in Explorer.
- **Quit**.

## Configuration

Settings are stored in `~/.virtual-camera-switcher/config.json`. Most
users only need `vcs --setup` plus the **Calibrate camera** menu items;
the file is hand-editable for advanced tuning.

### Top-level settings

| Setting                  | Default        | Description                                                            |
|--------------------------|----------------|------------------------------------------------------------------------|
| `cameras`                | `[]`           | List of camera indices to use                                          |
| `output_width`           | `1280`         | Virtual camera output width                                            |
| `output_height`          | `720`          | Virtual camera output height                                           |
| `output_fps`             | `30`           | Virtual camera frame rate                                              |
| `virtual_camera_backend` | `"unitycapture"` | pyvirtualcam backend (`unitycapture`, `obs`, etc.)                   |
| `virtual_camera_name`    | `""`           | Optional explicit device name                                          |
| `capture_width`          | `1280`         | Default capture width per camera                                       |
| `capture_height`         | `720`          | Default capture height per camera                                      |
| `capture_fps`            | `30`           | Default capture frame rate per camera                                  |
| `capture_fourcc`         | `"MJPG"`       | Default FOURCC; MJPG keeps two USB-2.0 webcams within bandwidth budget |
| `look_away_yaw_deg`      | `25.0`         | Yaw (degrees) above which the active camera is considered "looked away"|
| `look_away_grace_s`      | `0.25`         | Seconds you must be looking away before a search starts                |
| `search_timeout_s`       | `0.6`          | Maximum seconds spent scanning the other cameras before giving up      |
| `detect_fps_locked`      | `8`            | Detection FPS while locked on the active camera                        |
| `detect_fps_searching`   | `20`           | Detection FPS while scanning for a new camera                          |

### Per-camera overrides

Add a `camera_overrides` object keyed by camera index to tune individual
cameras (capture format, backend, sensor controls, calibration offset):

```json
"camera_overrides": {
  "0": {
    "controls": {
      "autofocus": 1,
      "auto_exposure": 0.75,
      "sharpness": 200,
      "saturation": 145,
      "contrast": 140
    }
  },
  "1": {
    "capture_backend": "MSMF",
    "capture_fourcc": "MJPG",
    "capture_width": 1280,
    "capture_height": 720
  }
}
```

Supported override fields: `capture_backend` (`DSHOW`/`MSMF`/`ANY`),
`capture_fourcc`, `capture_width`, `capture_height`, `capture_fps`,
`yaw_offset` (set automatically by Calibrate), and `controls` (any of
`brightness`, `contrast`, `saturation`, `hue`, `gain`, `exposure`,
`auto_exposure`, `sharpness`, `gamma`, `backlight`, `white_balance`,
`auto_wb`, `autofocus`, `focus`, `zoom`, `pan`, `tilt`).

## Architecture

```text
┌──────────────┐         ┌───────────────┐
│ Active cam   │──frame──▶│ GazeDetector  │──▶ yaw
└──────────────┘         └───────┬───────┘
      ▲                          │
      │                          ▼
      │                  ┌───────────────┐
      │                  │ GazeSwitcher  │
      │                  │ LOCKED ⇄ SEARCH│
      │                  └───────┬───────┘
      │ (when SEARCHING,         │ picks new active
      │  scans inactive cams)    ▼
      └──────────────────── active frame ──▶ VirtualCamera
                                            ──▶ Zoom / Teams / etc.
```

## Project structure

```text
src/virtual_camera_switcher/
  __init__.py
  main.py           # Entry point, App orchestration, calibration, snapshots
  config.py         # AppConfig + CameraOverride dataclasses (JSON-persisted)
  gaze.py           # MediaPipe FaceLandmarker + RQDecomp3x3 head-pose yaw
  cameras.py        # CameraReader (multi-backend negotiation) + CameraManager
  switcher.py       # GazeSwitcher state machine (LOCKED / SEARCHING)
  virtual_cam.py    # pyvirtualcam output wrapper
  tray.py           # System tray UI (pystray) with calibration + snapshots
filters/
  UnityCaptureFilter64.dll  # Bundled DirectShow virtual-camera filter
install.bat         # Admin installer: registers filter, pip-installs, fetches model
```

## Troubleshooting

- **Switching feels reluctant or never happens.** Run **Calibrate camera**
  for each camera while looking directly at it. Cameras mounted at an
  angle have a non-zero baseline yaw that the switcher needs to know
  about; otherwise even a head-on glance reads as "looking away."
- **A camera gets the wrong framing (zoom, crop, off-center).** Some AI
  webcams (for example the OBSBOT Tiny series) reframe based on which
  capture mode you negotiate. If their built-in app shows the correct
  framing but this app doesn't, remove that camera's `capture_backend`
  / `capture_fourcc` overrides and let it negotiate naturally.
- **Two USB-2.0 webcams cause one to drop to ~1 fps.** USB 2.0 can't
  carry two uncompressed streams. Leave `capture_fourcc` set to `MJPG`
  so each camera compresses on the device.
- **Moved the project folder and the virtual camera disappeared.** The
  Unity Capture DLL is registered with an absolute path. Re-run
  `install.bat` as Administrator from the new location.
