export const OBS_CONFIG_VERSION = "1";
export const OBS_SETTINGS_STORAGE_KEY = "nana-app-obs-settings-v1";
export const DEFAULT_OBS_CONFIG = Object.freeze({
  zoom: 0.4,
  height: 0.88,
  yaw: 0,
  pitch: 0,
});

const LIMITS = Object.freeze({
  zoom: [0.2, 1],
  height: [0.05, 0.95],
  yaw: [-180, 180],
  pitch: [-15, 15],
});

function boundedNumber(value, fallback, [minimum, maximum]) {
  if (value === "" || value === null || value === undefined) return fallback;
  const number = Number(value);
  return Number.isFinite(number) && number >= minimum && number <= maximum
    ? number
    : fallback;
}

export function normalizeObsConfig(value = {}) {
  const source = value && typeof value === "object" ? value : {};
  return Object.fromEntries(
    Object.entries(DEFAULT_OBS_CONFIG).map(([key, fallback]) => [
      key,
      boundedNumber(source[key], fallback, LIMITS[key]),
    ]),
  );
}

export function readObsConfig(search = "") {
  const params = new URLSearchParams(String(search || "").replace(/^\?/, ""));
  const version = params.get("v");
  if (version !== null && version !== OBS_CONFIG_VERSION) {
    throw new Error("unsupported_obs_config_version");
  }
  return normalizeObsConfig(Object.fromEntries(
    Object.keys(DEFAULT_OBS_CONFIG).map((key) => [key, params.get(key)]),
  ));
}

export function buildObsUrl(origin, value = DEFAULT_OBS_CONFIG) {
  const url = new URL("/obs.html", String(origin));
  const config = normalizeObsConfig(value);
  url.searchParams.set("v", OBS_CONFIG_VERSION);
  for (const key of Object.keys(DEFAULT_OBS_CONFIG)) {
    url.searchParams.set(key, String(config[key]));
  }
  return url.href;
}
