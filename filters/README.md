---
title: Virtual Camera Filters
description: >-
  DirectShow virtual-camera filter DLLs bundled with this project.
---

## Bundled

- `UnityCaptureFilter64.dll` — Unity Capture DirectShow filter, MIT-licensed,
  copied unchanged from the upstream
  [Unity Capture repository](https://github.com/schellingb/UnityCapture).

`install.bat` (run as Administrator) registers this DLL with `regsvr32`
so video-calling apps see the virtual camera. Re-run the installer if you
move the repository to a different path — the DLL must stay where it was
registered.
