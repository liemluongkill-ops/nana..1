import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_OBS_CONFIG,
  buildObsUrl,
  normalizeObsConfig,
  readObsConfig,
} from "../src/obsConfig.js";

test("OBS config uses per-field defaults for missing and invalid values", () => {
  assert.deepEqual(readObsConfig("?v=1&zoom=NaN&height=0.7"), {
    zoom: 0.4,
    height: 0.7,
    yaw: 0,
    pitch: 0,
  });
  assert.deepEqual(readObsConfig(""), DEFAULT_OBS_CONFIG);
});

test("OBS config rejects unsupported explicit versions", () => {
  assert.throws(() => readObsConfig("?v=2&zoom=0.5"), /unsupported_obs_config_version/);
});

test("OBS config rejects out-of-range values instead of clamping silently", () => {
  assert.deepEqual(
    normalizeObsConfig({ zoom: 3, height: -1, yaw: 181, pitch: -16 }),
    DEFAULT_OBS_CONFIG,
  );
});

test("OBS URL roundtrips framing and ignores untrusted fields", () => {
  const config = { zoom: 0.6, height: 0.8, yaw: 15, pitch: -3 };
  const url = new URL(buildObsUrl("http://127.0.0.1:5174", {
    ...config,
    gateway: "https://example.invalid",
    token: "secret",
  }));

  assert.equal(url.pathname, "/obs.html");
  assert.equal(url.searchParams.get("v"), "1");
  assert.equal(url.searchParams.has("gateway"), false);
  assert.equal(url.searchParams.has("token"), false);
  assert.deepEqual(readObsConfig(url.search), config);
});
