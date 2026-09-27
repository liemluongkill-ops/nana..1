import {
  Activity,
  Camera,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  CircleDot,
  Eye,
  Frame,
  Maximize2,
  MoveVertical,
  PersonStanding,
  RotateCw,
  RotateCcw,
  Smile,
  Hand,
  Heart,
  Ear,
  Lightbulb,
  Sparkles,
  ArrowDown,
  Rotate3d,
  ScanFace,
  SlidersHorizontal,
  Wifi,
  X,
  ZoomIn,
  Mic2,
  Shirt,
  createIcons,
} from "lucide";
import "./style.css";

createIcons({
  icons: {
    Activity,
    Camera,
    ChevronLeft,
    ChevronRight,
    CircleAlert,
    CircleDot,
    Eye,
    Frame,
    Maximize2,
    MoveVertical,
    PersonStanding,
    RotateCw,
    RotateCcw, Smile, Hand, Heart, Ear, Lightbulb, Sparkles, ArrowDown,
    Rotate3d,
    ScanFace,
    SlidersHorizontal,
    Wifi,
    X,
    ZoomIn,
    Mic2,
    Shirt,
  },
});

const RUNTIME_OBJECT = "NanaTargetDrivenRuntime";
const CAMERA_OBJECT = "NanaWebCameraRuntime";
const CAMERA_STORAGE_KEY = "nana-avatar-camera-v1";
const MODEL_STORAGE_KEY = "nana-avatar-model-v1";
const expressionSpecs = [
  ["wink_soft_smile", "Nháy nhẹ · Cười", "smile"],
  ["surprised_pout", "Ngạc nhiên · Bĩu", "circle-alert"],
  ["serious_think", "Nghiêm túc", "lightbulb"],
  ["cat_teary_smile", "Mèo · Rưng rưng", "scan-face"],
  ["shy_crying", "Ngại · Khóc", "eye"],
  ["playful_wink", "Nháy tinh nghịch", "sparkles"],
];
const eventSpecs = [
  ["curious", "Tò mò", "scan-face"], ["happy", "Cười nhẹ", "smile"],
  ["nod", "Gật đầu", "arrow-down"], ["listen", "Lắng nghe", "ear"],
  ["think", "Suy nghĩ", "lightbulb"], ["surprised", "Ngạc nhiên", "circle-alert"],
  ["shy", "Ngại ngùng", "heart"], ["playful", "Tinh nghịch", "sparkles"],
];
const eventLabels = Object.fromEntries([...eventSpecs, ...expressionSpecs].map(([action, label]) => [action, label]));
const expressionList = document.querySelector("#expression-list");
expressionList.innerHTML = expressionSpecs.map(([action, label, icon]) =>
  `<button type="button" class="event-button" data-expression="${action}" title="${label}" disabled><i data-lucide="${icon}"></i><span>${label}</span></button>`).join("");
createIcons({ root: expressionList, icons: { Smile, Heart, CircleAlert, Lightbulb, ScanFace, Eye, Sparkles } });
const expressionButtons = [...document.querySelectorAll("[data-expression]")];
const eventList = document.querySelector("#event-list");
eventList.innerHTML = eventSpecs.map(([action, label, icon]) =>
  `<button type="button" class="event-button" data-event="${action}" title="${action}" disabled><i data-lucide="${icon}"></i><span>${label}</span></button>`).join("");
createIcons({ root: eventList, icons: { ScanFace, Smile, ArrowDown, Ear, Lightbulb, CircleAlert, Heart, Sparkles, Hand, PersonStanding } });
const eventButtons = [...document.querySelectorAll("[data-event]")];
const eventsPanel = document.querySelector("#events-panel");
const eventsButton = document.querySelector("#events-button");
const settleButton = document.querySelector("#settle-event");
const cameraPresets = {
  portrait: { zoom: 0.28, height: 0.88, yaw: 0, pitch: 0 },
  waist: { zoom: 0.52, height: 0.75, yaw: 0, pitch: 0 },
  full: { zoom: 1, height: 0.5, yaw: 0, pitch: 0 },
};
const lookTargets = {
  left: { yaw: -24, pitch: -3, roll: 2 },
  center: { yaw: 0, pitch: 0, roll: 0 },
  right: { yaw: 24, pitch: -2, roll: -2 },
};

const elements = {
  stage: document.querySelector("#stage"),
  canvas: document.querySelector("#unity-canvas"),
  loading: document.querySelector("#loading"),
  loadingLabel: document.querySelector("#loading-label"),
  loadingDetail: document.querySelector("#loading-detail"),
  progress: document.querySelector("#progress-bar"),
  errorLayer: document.querySelector("#error-layer"),
  errorMessage: document.querySelector("#error-message"),
  retry: document.querySelector("#retry-button"),
  rendererState: document.querySelector("#renderer-state"),
  gatewayState: document.querySelector("#gateway-state"),
  settingsButton: document.querySelector("#settings-button"),
  settingsPanel: document.querySelector("#settings-panel"),
  closeSettings: document.querySelector("#close-settings"),
  fullscreen: document.querySelector("#fullscreen-button"),
  modelButton: document.querySelector("#model-button"),
  modelSelect: document.querySelector("#model-select"),
  controlSource: document.querySelector("#control-source"),
  idleToggle: document.querySelector("#idle-toggle"),
  blinkToggle: document.querySelector("#blink-toggle"),
  moveSpeed: document.querySelector("#move-speed"),
  moveSpeedValue: document.querySelector("#move-speed-value"),
  idleYaw: document.querySelector("#idle-yaw"),
  idleYawValue: document.querySelector("#idle-yaw-value"),
  eyeLead: document.querySelector("#eye-lead"),
  eyeLeadValue: document.querySelector("#eye-lead-value"),
  mouthTest: document.querySelector("#mouth-test"),
  mouthTestValue: document.querySelector("#mouth-test-value"),
  cameraZoom: document.querySelector("#camera-zoom"),
  cameraZoomValue: document.querySelector("#camera-zoom-value"),
  cameraHeight: document.querySelector("#camera-height"),
  cameraHeightValue: document.querySelector("#camera-height-value"),
  cameraYaw: document.querySelector("#camera-yaw"),
  cameraYawValue: document.querySelector("#camera-yaw-value"),
  cameraPitch: document.querySelector("#camera-pitch"),
  cameraPitchValue: document.querySelector("#camera-pitch-value"),
  cameraPresetButtons: [...document.querySelectorAll("[data-camera-preset]")],
  lookButtons: [...document.querySelectorAll("[data-look]")],
  lastCommand: document.querySelector("#last-command"),
  commandIndicator: document.querySelector("#command-indicator"),
};

let unityInstance = null;
let gatewayOnline = false;
let loadingStarted = false;
let selectedModel = 1;
let modelReady = false;
let modelRestored = false;

function chooseModel(value) {
  const model = Number(value);
  if (!modelReady || !unityInstance || ![1, 2].includes(model)) return;
  sendUnityTo("NanaModelRuntime", "SelectModel", model);
  try { localStorage.setItem(MODEL_STORAGE_KEY, String(model)); } catch { /* Session-only selection. */ }
}

window.addEventListener("nana-avatar-model-state", ({ detail }) => {
  modelReady = detail.ready === true && detail.count === 2;
  selectedModel = detail.selected === 2 ? 2 : 1;
  elements.modelButton.disabled = elements.modelSelect.disabled = !modelReady || !unityInstance;
  elements.modelSelect.value = String(selectedModel);
  elements.modelButton.title = `Model ${selectedModel} · Đổi sang Model ${selectedModel === 1 ? 2 : 1}`;
  if (modelReady && unityInstance && !modelRestored) {
    modelRestored = true;
    let saved = 1;
    try { saved = Number(localStorage.getItem(MODEL_STORAGE_KEY)) === 2 ? 2 : 1; } catch { /* Default outfit. */ }
    // Return from the Unity -> JS callback before sending another Unity message.
    window.setTimeout(() => chooseModel(saved), 0);
  }
});

elements.modelButton.addEventListener("click", () => chooseModel(selectedModel === 1 ? 2 : 1));
elements.modelSelect.addEventListener("change", () => chooseModel(elements.modelSelect.value));

function setChip(element, state, label) {
  element.classList.remove("pending", "online", "offline");
  element.classList.add(state);
  element.querySelector("span").textContent = label;
}

function setCommand(label, active = true) {
  elements.lastCommand.textContent = label;
  elements.commandIndicator.classList.toggle("active", active);
  window.clearTimeout(setCommand.timer);
  setCommand.timer = window.setTimeout(() => {
    elements.commandIndicator.classList.remove("active");
  }, 900);
}

function sendUnityTo(runtimeObject, method, payload) {
  if (!unityInstance) return false;
  unityInstance.SendMessage(runtimeObject, method, String(payload));
  return true;
}

function sendUnity(method, payload) {
  return sendUnityTo(RUNTIME_OBJECT, method, payload);
}

function sendCamera(method, payload) {
  return sendUnityTo(CAMERA_OBJECT, method, payload);
}

function cameraState() {
  return {
    zoom: Number(elements.cameraZoom.value),
    height: Number(elements.cameraHeight.value),
    yaw: Number(elements.cameraYaw.value),
    pitch: Number(elements.cameraPitch.value),
  };
}

function updateCameraOutputs() {
  elements.cameraZoomValue.value = `${Math.round(Number(elements.cameraZoom.value) * 100)}%`;
  elements.cameraHeightValue.value = `${Math.round(Number(elements.cameraHeight.value) * 100)}%`;
  elements.cameraYawValue.value = `${elements.cameraYaw.value}°`;
  elements.cameraPitchValue.value = `${elements.cameraPitch.value}°`;
}

function updatePresetSelection(selected = "") {
  elements.cameraPresetButtons.forEach((button) => {
    button.classList.toggle("active", button.dataset.cameraPreset === selected);
  });
}

function persistCamera() {
  try {
    localStorage.setItem(CAMERA_STORAGE_KEY, JSON.stringify(cameraState()));
  } catch {
    // Browser storage is optional for the local viewer.
  }
}

function applyCameraState() {
  const state = cameraState();
  sendCamera("SetZoom", state.zoom);
  sendCamera("SetHeight", state.height);
  sendCamera("SetYaw", state.yaw);
  sendCamera("SetPitch", state.pitch);
}

function setCameraControls(state, preset = "") {
  elements.cameraZoom.value = state.zoom;
  elements.cameraHeight.value = state.height;
  elements.cameraYaw.value = state.yaw;
  elements.cameraPitch.value = state.pitch;
  updateCameraOutputs();
  updatePresetSelection(preset);
  applyCameraState();
  persistCamera();
}

function restoreCameraControls() {
  try {
    const stored = JSON.parse(localStorage.getItem(CAMERA_STORAGE_KEY) || "null");
    if (stored && [stored.zoom, stored.height, stored.yaw, stored.pitch].every(Number.isFinite)) {
      setCameraControls(stored);
      return;
    }
  } catch {
    // Fall back to the portrait preset when storage is unavailable or invalid.
  }
  setCameraControls(cameraPresets.portrait, "portrait");
}

function lookPayload(direction) {
  return {
    action: "look",
    target: lookTargets[direction],
    move_s: Number(elements.moveSpeed.value),
    hold_s: direction === "center" ? 0.25 : 0.7,
    return_s: 0.8,
    return_to: direction === "center" ? "hold" : "neutral",
  };
}

async function sendIntent(payload, label) {
  if (!unityInstance) return;
  if (gatewayOnline) {
    try {
      const response = await fetch("/avatar-api/v1/avatar/intents", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok) {
        setCommand(`Không chạy: ${result.reason || response.status}`, false);
        return; // Never bypass a policy rejection with a direct preview.
      }
      elements.controlSource.textContent = "Nana gateway";
      setCommand(result.receipt?.status === "queued" ? `${label} · Đang chờ` : label);
      return;
    } catch {
      setCommand("Mất kết nối; kiểm tra trạng thái trước khi thử lại", false);
      return; // Delivery is uncertain: retrying locally could play the event twice.
    }
  }
  sendUnity("ReceiveAvatarIntentJson", JSON.stringify({ intent_id: crypto.randomUUID(), ...payload }));
  elements.controlSource.textContent = "Local preview";
  setCommand(label);
}

async function sendLook(direction) {
  const labels = { left: "Nhìn trái", center: "Nhìn giữa", right: "Nhìn phải" };
  await sendIntent(lookPayload(direction), labels[direction]);
}

async function refreshGateway() {
  try {
    const response = await fetch("/avatar-health", {
      cache: "no-store",
      signal: AbortSignal.timeout(1200),
    });
    const payload = response.ok ? await response.json() : { ok: false };
    gatewayOnline = payload.ok === true;
  } catch {
    gatewayOnline = false;
  }
  sendUnity("SetGatewayPollingEnabled", gatewayOnline ? "1" : "0");
  setChip(elements.gatewayState, gatewayOnline ? "online" : "offline", gatewayOnline ? "Gateway online" : "Local mode");
}

function showError(error) {
  elements.loading.hidden = true;
  elements.errorLayer.hidden = false;
  elements.errorMessage.textContent = error instanceof Error ? error.message : String(error);
  setChip(elements.rendererState, "offline", "Renderer lỗi");
}

async function loadUnity() {
  if (loadingStarted) return;
  loadingStarted = true;
  elements.errorLayer.hidden = true;
  elements.loading.hidden = false;
  elements.progress.style.width = "0%";
  elements.loadingDetail.textContent = "0%";
  setChip(elements.rendererState, "pending", "Đang nạp");

  try {
    const buildRoot = new URLSearchParams(location.search).get("build") === "baseline"
      ? "/unity" : "/unity-fidelity";
    const manifestResponse = await fetch(`${buildRoot}/build-config.json`, { cache: "no-store" });
    if (!manifestResponse.ok) throw new Error("Chưa có Unity WebGL build");
    const config = await manifestResponse.json();

    await new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = config.loaderUrl;
      script.onload = resolve;
      script.onerror = () => reject(new Error("Không tải được Unity loader"));
      document.body.appendChild(script);
    });

    unityInstance = await window.createUnityInstance(
      elements.canvas,
      {
        dataUrl: config.dataUrl,
        frameworkUrl: config.frameworkUrl,
        codeUrl: config.codeUrl,
        streamingAssetsUrl: config.streamingAssetsUrl || "StreamingAssets",
        companyName: "Nana Local",
        productName: "Nana Avatar",
        productVersion: "0.1.0",
        devicePixelRatio: Math.min(window.devicePixelRatio || 1, 1.5),
      },
      (progress) => {
        const percent = Math.round(progress * 100);
        elements.progress.style.width = `${percent}%`;
        elements.loadingDetail.textContent = `${percent}%`;
      },
    );

    elements.loading.hidden = true;
    setChip(elements.rendererState, "online", "Renderer online");
    elements.lookButtons.forEach((button) => {
      button.disabled = false;
    });
    eventButtons.forEach(button => { button.disabled = false; });
    expressionButtons.forEach(button => { button.disabled = false; });
    settleButton.disabled = false;
    sendUnity("SetIdleMotionEnabled", elements.idleToggle.checked ? "1" : "0");
    sendUnity("SetBlinkEnabled", elements.blinkToggle.checked ? "1" : "0");
    sendUnity("SetIdleYaw", elements.idleYaw.value);
    sendUnity("SetEyeLeadMultiplier", elements.eyeLead.value);
    sendUnity("SetMouthTest", Number(elements.mouthTest.value) / 40);
    sendUnity("SetGatewayPollingEnabled", gatewayOnline ? "1" : "0");
    applyCameraState();
    setCommand("Sẵn sàng", false);
  } catch (error) {
    showError(error);
  } finally {
    loadingStarted = false;
  }
}

elements.lookButtons.forEach((button) => {
  button.addEventListener("click", () => sendLook(button.dataset.look));
});

elements.settingsButton.addEventListener("click", () => {
  const opening = elements.settingsPanel.hidden;
  elements.settingsPanel.hidden = !opening;
  elements.settingsButton.setAttribute("aria-expanded", String(opening));
  if (opening) { eventsPanel.hidden = true; eventsButton.setAttribute("aria-expanded", "false"); }
});

eventsButton.addEventListener("click", () => {
  eventsPanel.hidden = !eventsPanel.hidden;
  eventsButton.setAttribute("aria-expanded", String(!eventsPanel.hidden));
  if (!eventsPanel.hidden) { elements.settingsPanel.hidden = true; elements.settingsButton.setAttribute("aria-expanded", "false"); }
});
document.querySelector("#close-events").addEventListener("click", () => {
  eventsPanel.hidden = true; eventsButton.setAttribute("aria-expanded", "false");
});
eventButtons.forEach(button => button.addEventListener("click", () => {
  const action = button.dataset.event;
  sendIntent({ action }, eventLabels[action]);
}));
expressionButtons.forEach(button => button.addEventListener("click", () => {
  const action = button.dataset.expression;
  sendIntent({ action }, eventLabels[action]);
}));
settleButton.addEventListener("click", () => sendIntent({ action: "settle" }, "Về idle"));
window.addEventListener("nana-avatar-state", ({ detail }) => {
  const phaseNames = { entering: "Đang chuyển", holding: "Giữ", returning: "Trở về", idle: "Idle" };
  const active = detail.faceAction || detail.action;
  const phase = detail.faceAction ? detail.facePhase : detail.phase;
  document.querySelector("#event-phase").textContent = `${eventLabels[active] || active} · ${phaseNames[phase] || phase}`;
  eventButtons.forEach(button => button.classList.toggle("active", button.dataset.event === detail.action || button.dataset.event === detail.faceAction));
  expressionButtons.forEach(button => button.classList.toggle("active", button.dataset.expression === detail.faceAction));
});

elements.closeSettings.addEventListener("click", () => {
  elements.settingsPanel.hidden = true;
  elements.settingsButton.setAttribute("aria-expanded", "false");
});

elements.fullscreen.addEventListener("click", async () => {
  if (!document.fullscreenElement) await elements.stage.requestFullscreen();
  else await document.exitFullscreen();
});

elements.retry.addEventListener("click", loadUnity);
elements.idleToggle.addEventListener("change", () => {
  sendUnity("SetIdleMotionEnabled", elements.idleToggle.checked ? "1" : "0");
});
elements.blinkToggle.addEventListener("change", () => {
  sendUnity("SetBlinkEnabled", elements.blinkToggle.checked ? "1" : "0");
});
elements.moveSpeed.addEventListener("input", () => {
  elements.moveSpeedValue.value = `${Number(elements.moveSpeed.value).toFixed(2)}s`;
});
elements.idleYaw.addEventListener("input", () => {
  elements.idleYawValue.value = `${elements.idleYaw.value}°`;
  sendUnity("SetIdleYaw", elements.idleYaw.value);
});
elements.eyeLead.addEventListener("input", () => {
  elements.eyeLeadValue.value = Number(elements.eyeLead.value).toFixed(2);
  sendUnity("SetEyeLeadMultiplier", elements.eyeLead.value);
});
elements.mouthTest.addEventListener("input", () => {
  elements.mouthTestValue.textContent = `${elements.mouthTest.value}%`;
  sendUnity("SetMouthTest", Number(elements.mouthTest.value) / 40);
});

elements.cameraPresetButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const preset = button.dataset.cameraPreset;
    setCameraControls(cameraPresets[preset], preset);
    setCommand({ portrait: "Khung cận", waist: "Khung nửa người", full: "Khung toàn thân" }[preset]);
  });
});

[
  [elements.cameraZoom, "SetZoom"],
  [elements.cameraHeight, "SetHeight"],
  [elements.cameraYaw, "SetYaw"],
  [elements.cameraPitch, "SetPitch"],
].forEach(([input, method]) => {
  input.addEventListener("input", () => {
    updateCameraOutputs();
    updatePresetSelection();
    sendCamera(method, input.value);
    persistCamera();
  });
});

window.addEventListener("beforeunload", () => unityInstance?.Quit?.());

refreshGateway();
window.setInterval(refreshGateway, 2500);
restoreCameraControls();
loadUnity();
