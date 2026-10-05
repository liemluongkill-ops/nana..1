import { readObsConfig } from "./obsConfig.js";
import {
  UNITY_RUNTIME_OBJECT,
  bindObsSignalLifecycle,
  createObsSignalConsumer,
} from "./obsSignals.js";
import "./obs.css";

const RUNTIME_OBJECT = UNITY_RUNTIME_OBJECT;
const CAMERA_OBJECT = "NanaWebCameraRuntime";
const MODEL_OBJECT = "NanaModelRuntime";
const PRESENTATION_MODEL = 2;
const OBS_BUILD_REVISION = "20260924-chroma-r10";
const OBS_BUILD_ROOT = "/obs-avatar";

const BUILD = Object.freeze({
  loaderUrl: `${OBS_BUILD_ROOT}/Build/unity-obs.loader.js?v=${OBS_BUILD_REVISION}`,
  dataUrl: `${OBS_BUILD_ROOT}/Build/unity-obs.data?v=${OBS_BUILD_REVISION}`,
  frameworkUrl: `${OBS_BUILD_ROOT}/Build/unity-obs.framework.js?v=${OBS_BUILD_REVISION}`,
  codeUrl: `${OBS_BUILD_ROOT}/Build/unity-obs.wasm?v=${OBS_BUILD_REVISION}`,
  streamingAssetsUrl: `${OBS_BUILD_ROOT}/StreamingAssets`,
});

const canvas = document.querySelector("#unity-canvas");
let unityInstance = null;
let latestModelState = window.__nanaAvatarModel || null;
let obsSignalConsumer = null;
let disposeObsSignalLifecycle = null;

window.__nanaObs = {
  status: "booting",
  reason: "",
  config: null,
  unityReady: false,
  modelReady: false,
  selectedModel: null,
  transparentRequested: false,
  chromaRequested: false,
  backgroundMode: "chroma_green",
  contextAlpha: null,
  audioEnabled: false,
  gatewayPollingEnabled: false,
  coreConnected: false,
  signalsEnabled: false,
};

function sendUnity(objectName, method, value) {
  if (!unityInstance) return false;
  unityInstance.SendMessage(objectName, method, String(value));
  return true;
}

function fail(reason, error = null) {
  window.__nanaObs.status = "error";
  window.__nanaObs.reason = reason;
  canvas.classList.remove("ready");
  if (error) console.error(`[Nana OBS] ${reason}`, error);
  else console.error(`[Nana OBS] ${reason}`);
}

function applyObsPresentation(config) {
  sendUnity(RUNTIME_OBJECT, "SetGatewayPollingEnabled", "0");
  sendUnity(RUNTIME_OBJECT, "SetMouthTest", "0");
  sendUnity(CAMERA_OBJECT, "SetChromaBackground", "1");
  sendUnity(CAMERA_OBJECT, "SetZoom", config.zoom);
  sendUnity(CAMERA_OBJECT, "SetHeight", config.height);
  sendUnity(CAMERA_OBJECT, "SetYaw", config.yaw);
  sendUnity(CAMERA_OBJECT, "SetPitch", config.pitch);
  if (Number(latestModelState?.selected) !== PRESENTATION_MODEL) {
    sendUnity(MODEL_OBJECT, "SelectModel", PRESENTATION_MODEL);
  }
  window.__nanaObs.chromaRequested = true;
}

function startPublicSignals() {
  if (obsSignalConsumer || disposeObsSignalLifecycle) return;
  obsSignalConsumer = createObsSignalConsumer({
    sendUnity,
    onConnectionChange(connected) {
      window.__nanaObs.coreConnected = connected;
    },
  });
  disposeObsSignalLifecycle = bindObsSignalLifecycle({ consumer: obsSignalConsumer });
  window.__nanaObs.signalsEnabled = true;
}

function finishIfReady() {
  if (window.__nanaObs.status === "ready") return;
  if (!unityInstance || latestModelState?.ready !== true) return;
  if (Number(latestModelState.selected) !== PRESENTATION_MODEL) return;
  applyObsPresentation(window.__nanaObs.config);
  window.__nanaObs.status = "ready";
  window.__nanaObs.reason = "";
  window.__nanaObs.unityReady = true;
  window.__nanaObs.modelReady = true;
  window.__nanaObs.selectedModel = PRESENTATION_MODEL;
  startPublicSignals();
  requestAnimationFrame(() => requestAnimationFrame(() => canvas.classList.add("ready")));
}

window.addEventListener("nana-avatar-model-state", ({ detail }) => {
  latestModelState = detail || null;
  window.__nanaObs.modelReady = detail?.ready === true;
  window.__nanaObs.selectedModel = Number(detail?.selected) || null;
  if (unityInstance && detail?.ready === true && Number(detail.selected) !== PRESENTATION_MODEL) {
    sendUnity(MODEL_OBJECT, "SelectModel", PRESENTATION_MODEL);
  }
  finishIfReady();
});

async function loadUnity() {
  let config;
  try {
    config = readObsConfig(window.location.search);
  } catch (error) {
    fail(error instanceof Error ? error.message : "invalid_obs_config", error);
    return;
  }
  window.__nanaObs.config = config;
  window.__nanaObs.status = "loading";

  try {
    await new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = BUILD.loaderUrl;
      script.onload = resolve;
      script.onerror = () => reject(new Error("obs_unity_loader_unavailable"));
      document.body.appendChild(script);
    });

    unityInstance = await window.createUnityInstance(
      canvas,
      {
        dataUrl: BUILD.dataUrl,
        frameworkUrl: BUILD.frameworkUrl,
        codeUrl: BUILD.codeUrl,
        streamingAssetsUrl: BUILD.streamingAssetsUrl,
        companyName: "Nana Local",
        productName: "Nana OBS Avatar",
        productVersion: "0.1.0",
        devicePixelRatio: 1,
        webglContextAttributes: {
          alpha: true,
          premultipliedAlpha: false,
          preserveDrawingBuffer: false,
          powerPreference: 2,
        },
      },
      () => {},
    );

    const gl = canvas.getContext("webgl2") || canvas.getContext("webgl");
    window.__nanaObs.contextAlpha = gl?.getContextAttributes()?.alpha === true;
    applyObsPresentation(config);
    window.__nanaObs.status = "waiting_for_model";
    finishIfReady();
  } catch (error) {
    fail(error instanceof Error ? error.message : "obs_unity_start_failed", error);
  }
}

loadUnity();
