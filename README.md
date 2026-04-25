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

1. **MediaPipe FaceLandmarker** detects 478 facial landmarks on each
   frame from every configured camera.
2. **OpenCV solvePnP** converts the landmarks into a head-pose yaw angle.
3. The camera where your face appears most front-facing (yaw closest to
   0°) is selected as the active feed.
4. A **hysteresis-based switcher** holds for several frames before
   committing a switch, preventing flickering.
5. The active camera's frames are pushed to a **pyvirtualcam** virtual
   camera device that appears as a regular webcam in any application.

## Prerequisites

- Windows 10 or 11
- Python 3.10+
- A virtual camera backend supported by pyvirtualcam (OBS Virtual Camera
  or Unity Capture)

## Installation

```bash
pip install -e .
```

To register the Unity Capture virtual camera filter (requires admin):

```cmd
install.bat
```

## Usage

### First-time setup

Scan for cameras and choose which ones to include:

```bash
vcs --setup
```

### Run

Start the virtual camera switcher as a system-tray application:

```bash
vcs
```

Use `--no-tray` to run in console mode instead.

## Configuration

Settings are stored in `~/.virtual-camera-switcher/config.json`:

| Setting                    | Default                      | Description                              |
|----------------------------|------------------------------|------------------------------------------|
| `cameras`                  | `[]`                         | List of camera indices to use            |
| `output_width`             | `1280`                       | Virtual camera output width              |
| `output_height`            | `720`                        | Virtual camera output height             |
| `output_fps`               | `30`                         | Virtual camera frame rate                |
| `hysteresis_frames`        | `10`                         | Frames before committing a camera switch |
| `virtual_camera_name`      | `"Virtual Camera Switcher"`  | Name shown to video-calling apps         |

## Architecture

```text
┌──────────────┐            ┌───────────────┐
│ Camera 0     │──frames──▶│ GazeDetector  │──▶ yaw 0
├──────────────┤            │ (per camera)  │──▶ yaw 1
│ Camera 1     │──frames──▶│               │
└──────────────┘            └───────────────┘
                                    │ yaw map
                                    ▼
                            ┌───────────────┐
                            │   Switcher    │──▶ pick most front-facing
                            │ (hysteresis)  │
                            └───────┬───────┘
                                    │ active frame
                                    ▼
                            ┌───────────────┐
                            │ VirtualCamera │──▶ Zoom / Teams / etc.
                            └───────────────┘
```

## Project structure

```text
src/virtual_camera_switcher/
  __init__.py
  main.py           # Entry point and App orchestration
  config.py         # Configuration dataclasses and persistence
  gaze.py           # MediaPipe FaceLandmarker + solvePnP head-pose detection
  cameras.py        # Multi-camera capture management
  switcher.py       # Hysteresis-based camera switching logic
  virtual_cam.py    # pyvirtualcam output wrapper
  tray.py           # System tray UI (pystray)
```
