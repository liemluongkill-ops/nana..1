# Nana 3D Avatar State

## 2026-09-05 Current Decision

Status: **`NANA-3D-AVATAR-WEBGL-V1 = OWNER VISUAL ACCEPTED / LOCAL
PROTOTYPE BASELINE / STOPPED ON DEMAND`.**

Ba viewed the avatar running directly in a browser at
`http://127.0.0.1:5173`, accepted V1, and requested that the review processes
be stopped. The Vite server and Playwright browser were stopped after
acceptance. This is a preserved local baseline, not an always-running service
and not a production livestream claim.

V1 means:

- the current assembled Nana 3D model renders natively in Unity WebGL inside
  the browser; it is not a video stream or capture of the Unity Editor;
- Nana Core can submit bounded semantic `look` intents through the local
  Avatar Intent Gateway;
- left, right, and center head targets, smooth retargeting, eye lead, ambient
  idle motion, and blink were visually accepted by Ba;
- the WebGL export bakes Modular Avatar/NDMF before building, so the outfit,
  hair, and attached effects use the assembled avatar hierarchy rather than
  remaining detached prefab pieces;
- V1 does not yet include mouth/viseme lipsync, general gesture playback,
  runtime completion receipts, OBS composition, YouTube Live integration, or
  production startup automation.

## Final V1 Architecture

```text
Nana Core / private operator commands
  -> AvatarIntentGateway (nana.avatar.v1, loopback 127.0.0.1:8766)
  -> bounded command cursor endpoint
  -> Vite same-origin proxy (/avatar-api)
  -> Unity WebGL canvas in the local browser (127.0.0.1:5173)
  -> NanaTargetDrivenAvatarController
  -> baked Shinano humanoid hierarchy
```

Ownership is intentionally split:

```text
Nana Core
  owns semantic intent only: look left/right/center, timing, priority, policy

AvatarIntentGateway
  owns allowlist validation, bounds, conflicts, queueing, and transport

Unity avatar runtime
  owns current pose, easing, interpolation, head/neck split, eyes, blink,
  interruption, hold, and return-to-neutral behavior

Web page
  owns presentation, loading/error state, local controls, and same-origin proxy
```

Nana must not emit per-frame values, arbitrary bone paths, or cumulative bone
deltas. A `look` target is an absolute bounded offset from neutral. A new
target starts from the actual current runtime pose; it does not reset to
neutral first and does not add the previous command angle.

## Browser Runtime

Web project:

```text
<NANA_AVATAR_WEB>\web
```

The first screen is the live Unity canvas. The small surrounding UI provides:

- renderer and gateway status;
- left, center, and right look controls;
- idle and blink toggles;
- move-time, idle-yaw, and eye-lead controls;
- fullscreen;
- local semantic preview fallback when the gateway is offline.

The fallback still sends a semantic `look` payload through Unity
`SendMessage`; it is a browser-preview convenience, not a second Nana Core
control architecture.

The Vite proxy maps:

```text
/avatar-api/* -> http://127.0.0.1:8766/*
```

Keep both listeners on loopback. Do not expose the gateway or Vite dev server
to the LAN or public internet without a separate authenticated deployment
design.

## Unity Source And Preserved Baseline

Editable Unity project:

```text
<AVATAR_SOURCE>
Unity 2022.3.22f1
```

Preserve these original baseline files:

```text
Assets\Shinano\FBX\loop.anim
Assets\Nana\Animations\Nana_Local.controller
Assets\Nana\Scenes\NanaAvatar_Working.unity
Assets\Nana\Prefabs\NanaAvatar_Current.prefab
```

Target-driven candidate files:

```text
Assets\Nana\Runtime\NanaTargetDrivenAvatarController.cs
Assets\Nana\Runtime\Editor\NanaTargetDrivenAvatarControllerEditor.cs
Assets\Nana\Runtime\Editor\NanaTargetDrivenSceneMenu.cs
Assets\Nana\Runtime\README_TargetDriven.md
Assets\Nana\Animations\Nana_TargetDriven.controller
Assets\Nana\Prefabs\NanaAvatar_TargetDriven.prefab
Assets\Nana\Scenes\NanaAvatar_TargetDriven_Test.unity
```

The target-driven component owns only head, neck, eye gaze, and blink. Gesture
and full-body layers remain a later runtime concern. Do not run another owner
of idle head/eye/blink motion at the same time.

## WebGL Build Assembly

The WebGL build uses an isolated Unity copy:

```text
<NANA_AVATAR_WEB>\unity
```

This protects the editable `W:` project from WebGL-specific package and scene
changes. `Assets\NanaWeb\Editor\NanaWebBuild.cs` performs this sequence:

```text
open NanaAvatar_TargetDriven_Test
  -> unpack the avatar prefab
  -> run NDMF/Modular Avatar processing
  -> rebind Animator, Neck, Head, and Body face renderer
  -> strip VRChat/NDMF-only runtime components from the exported scene
  -> save NanaWebRuntime.unity
  -> build WebGL into web\public\unity
```

This bake step is required. The first raw WebGL export skipped it, so the body
animation moved the base skeleton while outfit sleeves, accessory hair, and
effects remained on separate assembly hierarchies. The accepted V1 build ran
the complete NDMF pipeline first.

Accepted bake evidence:

```text
platform=nadena.dev.ndmf.vrchat.avatar3
renderers=48->48
behaviours=141->1
head=Head
face=Body
```

The isolated package metadata also excludes `VRCCore-Editor.dll` from WebGL.
Without that local-only exclusion, the player compile sees both
`VRCCore-Editor` and `VRCCore-Standalone` and fails on duplicate `Logger`
types. Do not copy this WebGL package patch back into the editable VRChat
avatar project without a separate reason.

Current browser artifacts:

```text
<NANA_AVATAR_WEB>\web\public\unity\Build\unity.loader.js
<NANA_AVATAR_WEB>\web\public\unity\Build\unity.data
<NANA_AVATAR_WEB>\web\public\unity\Build\unity.framework.js
<NANA_AVATAR_WEB>\web\public\unity\Build\unity.wasm
```

Latest accepted build report:

```text
NANA_WEBGL_BUILD result=Succeeded
size=91443162
errors=0
warnings=1
```

The main payload remains large: approximately 62.9 MB data plus 28.1 MB WASM.
This is acceptable for the local V1 proof, not a production web-size target.

## Nana Core Contract

Core implementation:

```text
<NANA_REPO>\nana\runtime\avatar_intent_gateway.py
<NANA_REPO>\nana\cli\stage_runtime_commands.py
<NANA_REPO>\nana\docs\avatar_runtime_protocol.md
<NANA_REPO>\nana\tests\smoke\smoke_avatar_intent_gateway.py
<NANA_REPO>\nana\tests\smoke\smoke_avatar_look_contract.py
```

Gateway defaults remain fail-closed:

```text
NANA_AVATAR_GATEWAY_ENABLED=0
NANA_AVATAR_GATEWAY_HOST=127.0.0.1
NANA_AVATAR_GATEWAY_PORT=8766
NANA_AVATAR_GATEWAY_TRANSPORT=recording
NANA_AVATAR_GATEWAY_PENDING_LIMIT=1
NANA_AVATAR_GATEWAY_AUTO_EVENTS_ENABLED=0
```

`recording` is the accepted local V1 transport. Warudo WebSocket support is an
optional adapter contract only; no Warudo blueprint/runtime is part of the
accepted browser V1.

The gateway allowlist contains more actions than the browser runtime currently
implements. For V1, only `look` is verified end to end in Unity WebGL. Do not
claim `wave`, `think`, `happy`, `dance`, or other allowlisted actions are live
until each has a Unity runtime mapping and visual proof.

## Verification Ledger

Fresh offline Core verification on 2026-09-05:

```text
smoke_avatar_intent_gateway.py: 13/13 PASS
smoke_avatar_look_contract.py: 6/6 PASS
```

These tests use recording/fake or loopback transports. They do not prove a
visible character pose by themselves.

Build verification:

```text
Assembly-CSharp.csproj: build succeeded, 0 warnings, 0 errors
Assembly-CSharp-Editor.csproj: build succeeded, 0 warnings, 0 errors
Vite production build: PASS
Unity WebGL build: Succeeded, errors=0, warnings=1
```

Live local proof:

- Nana startup reported the avatar gateway enabled on `127.0.0.1:8766` in
  recording mode.
- `/avatar-runtime-look left`, `right`, and `center` each returned HTTP `202`
  and an accepted semantic receipt.
- Unity received all three commands and Ba accepted their direction, speed,
  eye behavior, and motion feel.
- The browser loaded the real WebGL canvas with renderer and gateway online.
- After NDMF bake, Ba visually accepted the V1 avatar and asked to stop all
  review processes.

## Known V1 Limits

The following are open and must not be silently promoted to complete:

1. Mouth/viseme lipsync is not implemented. This is the next owner-identified
   avatar priority, but it is not active until Ba asks to continue.
2. Unity does not yet post `started`/`finished` receipts back to the gateway;
   finite intents can expire by duration without proof that the pose completed.
3. Only target-driven `look` is verified end to end. Gesture, expression,
   state, FX, and full-body action mappings remain pending.
4. OBS, YouTube Live Chat, public voice scheduling, and subtitles are not
   connected to this browser runtime.
5. Warudo remains optional and unverified for this V1.
6. WebGL still reports lilToon texture-unit/outline warnings and some
   collider/preview-component console noise. Ba accepted the visual V1, but
   these remain cleanup items before a long production stream.
7. Owner screenshots showed high GPU utilization during local review. There
   is no controlled FPS, temperature, power, memory, or long-soak benchmark;
   do not claim RTX 2080 production headroom yet.
8. The local server is manually started and stopped. There is no supervised
   startup, crash restart, packaged desktop host, or production deployment.
9. Purchased model and customization assets are local licensed assets. Do not
   publish or redistribute source assets as part of a web deployment without
   checking their licenses.

## Operational Procedure

Private startup, activation, and operator approval commands are intentionally
omitted from this public snapshot.

## Stop Condition And Next Work

Current stop condition is satisfied: **V1 is accepted and all review-only
processes are stopped.** Preserve this baseline.

When Ba reopens the scope, the narrow next task is mouth/viseme lipsync on the
accepted WebGL avatar. Keep it as a separate channel from head, eyes, blink,
and gesture ownership. After lipsync is verified, consider runtime receipts,
gesture overlays, performance/soak work, and only then OBS/YouTube composition.
