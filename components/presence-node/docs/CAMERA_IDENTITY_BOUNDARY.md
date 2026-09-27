# Camera and Identity Boundary

The OV5640 is evidence input, not a person database.

```text
OV5640 -> ESP32 MJPEG -> PC Presence Adapter -> sampled evidence event
                                           -> Nana Core face detector
                                           -> identity resolver
                                           -> person_id
                                           -> relationship profile
```

## ESP32 owns

- Camera initialization and health telemetry.
- JPEG capture with bounded buffers.
- Optional face-presence detection after PSRAM is stable.
- Transport metadata such as frame time, dimensions, and sequence number.
- No durable face embedding or relationship record.
- No continuous inference and no routine frame archive on microSD.

## PC Presence Adapter owns

- The sole long-lived MJPEG connection to the ESP32.
- Bounded frame sampling and backpressure.
- Short-lived frame decoding and image-quality checks.
- Conversion from raw frames into typed evidence events for Nana Core.
- No identity decision and no relationship mutation.

## Nana Core owns

- Face detection and image-quality validation.
- Face embeddings and multi-evidence identity resolution.
- Consent, enrollment, deletion, and retention policy.
- `person_id`, names, relationship state, and social memory.
- Authorization as a separate decision from familiarity.

## Bring-up stages

1. Prove OV5640 identity and one complete VGA JPEG in internal DRAM.
2. Save the frame to microSD and inspect it on the PC.
3. Run `tools/detect_faces.py` to prove face detection without identity.
4. Correct and stress-test PSRAM before increasing beyond VGA or adding frame
   buffers.
5. Prove the token-protected LAN stream and one requested capture.
6. Add explicit, consent-based identity enrollment.

The LAN service is transport, not identity. Browser viewing is a bring-up tool;
the production path will let the PC Presence Adapter own the stream and expose
only bounded evidence to Nana Core.

Do not infer personality from a face. Store observed interaction facts only.
