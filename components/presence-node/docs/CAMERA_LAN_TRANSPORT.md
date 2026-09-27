# Camera LAN Transport

## Purpose

Keep the OV5640 continuously reachable without saving or analyzing every
frame. The ESP32 is a bounded sensor adapter; Nana Core remains the owner of
reasoning and identity.

```text
OV5640
  -> one internal-DRAM JPEG framebuffer
  -> token-protected MJPEG over local Wi-Fi
  -> PC Presence Adapter
  -> bounded frame sampler or person/motion event
  -> Nana Core vision and identity boundaries
```

## Ownership

- `nana_camera.c` is the single camera owner.
- HTTP and serial diagnostics are mutually exclusive adapters.
- The ESP32 serves at most one active MJPEG client in this baseline.
- The production PC Presence Adapter should be that client.
- Browser viewing is for bring-up; do not run it beside the production adapter.

## Security and persistence

- Wi-Fi credentials are provisioned over physical USB-UART and stored in NVS.
- The Wi-Fi password is never written to source code or the PC config.
- A random camera token is generated once and stored in NVS.
- `.local/presence_node.json` stores only the LAN URL, token, and SSID and is
  excluded from version control.
- Keep the service on the trusted local network. Do not port-forward it.
- LAN mode does not write camera frames to microSD.

## Runtime policy

Continuous transport is not continuous inference. Nana should process a frame
only when a bounded sampler or a local event asks for one. The first PC adapter
target is approximately one sampled frame per second, reduced further when no
person or motion is present. Slow vision processing must drop stale frames
instead of building an unbounded queue.

## Bring-up commands

```bat
python tools\provision_wifi.py --port COM14
python tools\probe_camera_http.py
python tools\probe_camera_http.py --open
```

Provisioning automatically opens the tokenized viewer after a successful Wi-Fi
connection. The probe checks `/health` and requests one complete JPEG at
`captures\nana_camera_latest.jpg`.
