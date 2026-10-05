import {
  ArrowLeft,
  ArrowUp,
  AudioLines,
  Copy,
  ExternalLink,
  Frame,
  MessageCircle,
  Mic,
  MonitorUp,
  Palette,
  PersonStanding,
  Plus,
  ScanFace,
  Search,
  SearchX,
  Settings,
  Settings2,
  UserRound,
  X,
  createIcons,
} from "lucide";
import {
  DEFAULT_OBS_CONFIG,
  OBS_SETTINGS_STORAGE_KEY,
  buildObsUrl,
  normalizeObsConfig,
} from "./obsConfig.js";
import { createConversationController } from "./conversationController.js";
import { createCoreChatTransport } from "./coreChatTransport.js";
import { createPrivateAvatarSignalConsumer } from "./privateAvatarSignals.js";
import "./style.css";

createIcons({
  icons: {
    ArrowLeft,
    ArrowUp,
    AudioLines,
    Copy,
    ExternalLink,
    Frame,
    MessageCircle,
    Mic,
    MonitorUp,
    Palette,
    PersonStanding,
    Plus,
    ScanFace,
    Search,
    SearchX,
    Settings,
    Settings2,
    UserRound,
    X,
  },
});

const RUNTIME_OBJECT = "NanaTargetDrivenRuntime";
const CAMERA_OBJECT = "NanaWebCameraRuntime";
const MODEL_OBJECT = "NanaModelRuntime";
const PRESENTATION_MODEL = 2;
const SETTINGS_STORAGE_KEY = "nana-app-settings-v1";

const BUILD = Object.freeze({
  loaderUrl: "/Build/unity-fidelity.loader.js",
  dataUrl: "/Build/unity-fidelity.data",
  frameworkUrl: "/Build/unity-fidelity.framework.js",
  codeUrl: "/Build/unity-fidelity.wasm",
  streamingAssetsUrl: "/StreamingAssets",
});

const DEFAULT_SETTINGS = Object.freeze({
  startupView: "avatar",
  rememberWindow: true,
  startFullscreen: false,
  theme: "dark",
  uiScale: 1,
  reduceMotion: false,
  cameraZoom: 0.4,
  cameraHeight: 0.88,
  cameraYaw: 0,
  cameraPitch: 0,
  idleEnabled: true,
  blinkEnabled: true,
  moveSpeed: 0.8,
  idleYaw: 8,
  eyeLead: 0.55,
  mouthTest: 0,
  sendOnEnter: true,
  showSubtitles: true,
  composerPosition: "bottom",
  microphone: "system",
  pushToTalk: true,
  autoListen: false,
});

const CAMERA_PRESETS = Object.freeze({
  portrait: { cameraZoom: 0.28, cameraHeight: 0.88, cameraYaw: 0, cameraPitch: 0 },
  waist: { cameraZoom: 0.52, cameraHeight: 0.75, cameraYaw: 0, cameraPitch: 0 },
  full: { cameraZoom: 1, cameraHeight: 0.5, cameraYaw: 0, cameraPitch: 0 },
});

const SECTION_LABELS = Object.freeze({
  general: ["Ứng dụng", "Chung"],
  appearance: ["Ứng dụng", "Giao diện"],
  avatar: ["Nhân vật", "Avatar"],
  chat: ["Tương tác", "Trò chuyện"],
  voice: ["Tương tác", "Giọng nói"],
});

const elements = {
  app: document.querySelector("#app"),
  canvas: document.querySelector("#unity-canvas"),
  loading: document.querySelector("#loading"),
  loadingLabel: document.querySelector("#loading-label"),
  loadingDetail: document.querySelector("#loading-detail"),
  progress: document.querySelector("#progress-bar"),
  errorLayer: document.querySelector("#error-layer"),
  errorMessage: document.querySelector("#error-message"),
  retry: document.querySelector("#retry-button"),
  chatComposer: document.querySelector("#chat-composer"),
  chatInput: document.querySelector("#chat-input"),
  chatPreview: document.querySelector("#chat-preview"),
  conversationStatus: document.querySelector("#conversation-status"),
  conversationStatusLabel: document.querySelector("#conversation-status-label"),
  microphoneButton: document.querySelector("#microphone-button"),
  voiceModeButton: document.querySelector("#voice-mode-button"),
  sendButton: document.querySelector("#send-button"),
  voiceStatus: document.querySelector("#voice-status"),
  voiceStatusLabel: document.querySelector("#voice-status-label"),
  settingsButton: document.querySelector("#settings-button"),
  settingsView: document.querySelector("#settings-view"),
  backToApp: document.querySelector("#back-to-app"),
  closeSettings: document.querySelector("#close-settings"),
  settingsSearch: document.querySelector("#settings-search-input"),
  settingsTitle: document.querySelector("#settings-title"),
  settingsEyebrow: document.querySelector("#settings-eyebrow"),
  settingsEmpty: document.querySelector("#settings-empty"),
  navItems: [...document.querySelectorAll("[data-settings-section]")],
  panels: [...document.querySelectorAll("[data-settings-panel]")],
  cameraPresetButtons: [...document.querySelectorAll("[data-camera-preset]")],
  startupView: document.querySelector("#startup-view"),
  rememberWindow: document.querySelector("#remember-window"),
  startFullscreen: document.querySelector("#start-fullscreen"),
  theme: document.querySelector("#theme-select"),
  uiScale: document.querySelector("#ui-scale"),
  uiScaleValue: document.querySelector("#ui-scale-value"),
  reduceMotion: document.querySelector("#reduce-motion"),
  cameraZoom: document.querySelector("#camera-zoom"),
  cameraZoomValue: document.querySelector("#camera-zoom-value"),
  cameraHeight: document.querySelector("#camera-height"),
  cameraHeightValue: document.querySelector("#camera-height-value"),
  cameraYaw: document.querySelector("#camera-yaw"),
  cameraYawValue: document.querySelector("#camera-yaw-value"),
  cameraPitch: document.querySelector("#camera-pitch"),
  cameraPitchValue: document.querySelector("#camera-pitch-value"),
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
  obsZoom: document.querySelector("#obs-camera-zoom"),
  obsZoomValue: document.querySelector("#obs-camera-zoom-value"),
  obsHeight: document.querySelector("#obs-camera-height"),
  obsHeightValue: document.querySelector("#obs-camera-height-value"),
  obsYaw: document.querySelector("#obs-camera-yaw"),
  obsYawValue: document.querySelector("#obs-camera-yaw-value"),
  obsPitch: document.querySelector("#obs-camera-pitch"),
  obsPitchValue: document.querySelector("#obs-camera-pitch-value"),
  obsUrl: document.querySelector("#obs-url"),
  obsStatus: document.querySelector("#obs-url-status"),
  obsUseCurrent: document.querySelector("#obs-use-current"),
  obsOpenPreview: document.querySelector("#obs-open-preview"),
  obsCopyUrl: document.querySelector("#obs-copy-url"),
  sendOnEnter: document.querySelector("#send-on-enter"),
  showSubtitles: document.querySelector("#show-subtitles"),
  composerPosition: document.querySelector("#composer-position"),
  microphone: document.querySelector("#microphone-select"),
  pushToTalk: document.querySelector("#push-to-talk"),
  autoListen: document.querySelector("#auto-listen"),
};

let unityInstance = null;
let loading = false;
let resizeTimer = 0;
let modelSelectionApplied = false;
let modelSelectionTimer = 0;
let appReady = false;
let activeSettingsSection = "general";
let settings = loadSettings();
let obsSettings = loadObsSettings();
const recentMessages = new Map();
const recentMessageTimers = new Map();
const expiredRecentRoles = new Set();
const RECENT_MESSAGE_TTL_MS = 10_000;
const RECENT_MESSAGE_FADE_MS = 800;

window.__nanaApp = {
  status: "booting",
  unityReady: false,
  framing: null,
  model: { ready: false, selected: null, requested: PRESENTATION_MODEL },
  settingsOpen: false,
  chat: {
    voiceMode: false,
    conversationState: "idle",
    submissionCount: 0,
    visibleRoles: [],
  },
  coreConnected: false,
  obs: {
    configured: true,
    connected: false,
    url: "",
  },
};

function loadSettings() {
  try {
    const stored = JSON.parse(localStorage.getItem(SETTINGS_STORAGE_KEY) || "null");
    if (stored && typeof stored === "object") return { ...DEFAULT_SETTINGS, ...stored };
  } catch {
    // Local settings are optional; defaults remain authoritative.
  }
  return { ...DEFAULT_SETTINGS };
}

function loadObsSettings() {
  try {
    const stored = JSON.parse(localStorage.getItem(OBS_SETTINGS_STORAGE_KEY) || "null");
    return normalizeObsConfig(stored || DEFAULT_OBS_CONFIG);
  } catch {
    return { ...DEFAULT_OBS_CONFIG };
  }
}

function persistObsSettings() {
  try {
    localStorage.setItem(OBS_SETTINGS_STORAGE_KEY, JSON.stringify(obsSettings));
  } catch {
    // The copyable URL remains available even without browser storage.
  }
  updateObsControls();
}

function persistSettings() {
  try {
    localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(settings));
  } catch {
    // The app remains usable when browser storage is unavailable.
  }
  window.__nanaApp.framing = {
    zoom: settings.cameraZoom,
    height: settings.cameraHeight,
    yaw: settings.cameraYaw,
    pitch: settings.cameraPitch,
  };
}

function sendUnityTo(objectName, method, value) {
  if (!unityInstance) return false;
  unityInstance.SendMessage(objectName, method, String(value));
  return true;
}

function safelySendUnityTo(objectName, method, value) {
  try { return sendUnityTo(objectName, method, value); }
  catch { return false; }
}

function applyCameraSettings() {
  sendUnityTo(CAMERA_OBJECT, "SetZoom", settings.cameraZoom);
  sendUnityTo(CAMERA_OBJECT, "SetHeight", settings.cameraHeight);
  sendUnityTo(CAMERA_OBJECT, "SetYaw", settings.cameraYaw);
  sendUnityTo(CAMERA_OBJECT, "SetPitch", settings.cameraPitch);
  updateCameraOutputs();
  updatePresetSelection();
  persistSettings();
}

function applyAvatarSettings() {
  sendUnityTo(RUNTIME_OBJECT, "SetIdleMotionEnabled", settings.idleEnabled ? "1" : "0");
  sendUnityTo(RUNTIME_OBJECT, "SetBlinkEnabled", settings.blinkEnabled ? "1" : "0");
  sendUnityTo(RUNTIME_OBJECT, "SetIdleYaw", settings.idleYaw);
  sendUnityTo(RUNTIME_OBJECT, "SetEyeLeadMultiplier", settings.eyeLead);
  sendUnityTo(RUNTIME_OBJECT, "SetGatewayPollingEnabled", "0");
  applyMouthPreview();
  applyCameraSettings();
}

function applyMouthPreview(state = coreTransport.snapshot()) {
  safelySendUnityTo(RUNTIME_OBJECT, "SetMouthTest", state.pendingTurn ? 0 : settings.mouthTest / 40);
}

function applyShellSettings() {
  document.documentElement.style.setProperty("--ui-scale", settings.uiScale);
  document.documentElement.classList.toggle("reduce-motion", settings.reduceMotion);
  elements.uiScaleValue.value = `${Math.round(settings.uiScale * 100)}%`;
}

function updateCameraOutputs() {
  elements.cameraZoomValue.value = `${Math.round(settings.cameraZoom * 100)}%`;
  elements.cameraHeightValue.value = `${Math.round(settings.cameraHeight * 100)}%`;
  elements.cameraYawValue.value = `${settings.cameraYaw}°`;
  elements.cameraPitchValue.value = `${settings.cameraPitch}°`;
  elements.moveSpeedValue.value = `${Number(settings.moveSpeed).toFixed(2)}s`;
  elements.idleYawValue.value = `${settings.idleYaw}°`;
  elements.eyeLeadValue.value = Number(settings.eyeLead).toFixed(2);
  elements.mouthTestValue.value = `${settings.mouthTest}%`;
}

function currentObsUrl() {
  return buildObsUrl(window.location.origin, obsSettings);
}

function updateObsControls() {
  elements.obsZoom.value = obsSettings.zoom;
  elements.obsHeight.value = obsSettings.height;
  elements.obsYaw.value = obsSettings.yaw;
  elements.obsPitch.value = obsSettings.pitch;
  elements.obsZoomValue.value = `${Math.round(obsSettings.zoom * 100)}%`;
  elements.obsHeightValue.value = `${Math.round(obsSettings.height * 100)}%`;
  elements.obsYawValue.value = `${obsSettings.yaw}°`;
  elements.obsPitchValue.value = `${obsSettings.pitch}°`;
  elements.obsUrl.value = currentObsUrl();
  window.__nanaApp.obs.url = elements.obsUrl.value;
}

function setObsStatus(message) {
  elements.obsStatus.textContent = message;
}

function updatePresetSelection(selected = "") {
  if (!selected) {
    selected = Object.entries(CAMERA_PRESETS).find(([, preset]) =>
      Object.entries(preset).every(([key, value]) => Number(settings[key]) === value),
    )?.[0] || "";
  }
  elements.cameraPresetButtons.forEach((button) => {
    button.classList.toggle("active", button.dataset.cameraPreset === selected);
  });
}

function syncControlsFromSettings() {
  elements.startupView.value = settings.startupView;
  elements.rememberWindow.checked = settings.rememberWindow;
  elements.startFullscreen.checked = settings.startFullscreen;
  elements.theme.value = settings.theme;
  elements.uiScale.value = settings.uiScale;
  elements.reduceMotion.checked = settings.reduceMotion;
  elements.cameraZoom.value = settings.cameraZoom;
  elements.cameraHeight.value = settings.cameraHeight;
  elements.cameraYaw.value = settings.cameraYaw;
  elements.cameraPitch.value = settings.cameraPitch;
  elements.idleToggle.checked = settings.idleEnabled;
  elements.blinkToggle.checked = settings.blinkEnabled;
  elements.moveSpeed.value = settings.moveSpeed;
  elements.idleYaw.value = settings.idleYaw;
  elements.eyeLead.value = settings.eyeLead;
  elements.mouthTest.value = settings.mouthTest;
  elements.sendOnEnter.checked = settings.sendOnEnter;
  elements.showSubtitles.checked = settings.showSubtitles;
  elements.composerPosition.value = settings.composerPosition;
  elements.microphone.value = settings.microphone;
  elements.pushToTalk.checked = settings.pushToTalk;
  elements.autoListen.checked = settings.autoListen;
  updateCameraOutputs();
  updateObsControls();
  updatePresetSelection();
  applyShellSettings();
}

function normalizeSearch(value) {
  return String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/đ/g, "d")
    .toLowerCase()
    .trim();
}

function selectSettingsSection(section) {
  activeSettingsSection = section in SECTION_LABELS ? section : "general";
  const [eyebrow, title] = SECTION_LABELS[activeSettingsSection];
  elements.settingsEyebrow.textContent = eyebrow;
  elements.settingsTitle.textContent = title;
  elements.settingsEmpty.hidden = true;
  elements.navItems.forEach((button) => {
    button.classList.toggle("active", button.dataset.settingsSection === activeSettingsSection);
  });
  elements.panels.forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.settingsPanel === activeSettingsSection);
  });
}

function filterSettings(query) {
  const normalized = normalizeSearch(query);
  if (!normalized) {
    selectSettingsSection(activeSettingsSection);
    return;
  }

  let matches = 0;
  elements.settingsEyebrow.textContent = "Cài đặt";
  elements.settingsTitle.textContent = "Kết quả tìm kiếm";
  elements.navItems.forEach((button) => button.classList.remove("active"));
  elements.panels.forEach((panel) => {
    const haystack = normalizeSearch(`${panel.dataset.searchTerms} ${panel.textContent}`);
    const match = haystack.includes(normalized);
    panel.classList.toggle("active", match);
    matches += Number(match);
  });
  elements.settingsEmpty.hidden = matches !== 0;
}

function openSettings(section = activeSettingsSection) {
  elements.settingsView.hidden = false;
  elements.settingsButton.setAttribute("aria-expanded", "true");
  window.__nanaApp.settingsOpen = true;
  syncControlsFromSettings();
  selectSettingsSection(section);
  window.requestAnimationFrame(() => elements.backToApp.focus());
}

function closeSettings() {
  elements.settingsView.hidden = true;
  elements.settingsButton.setAttribute("aria-expanded", "false");
  elements.settingsSearch.value = "";
  window.__nanaApp.settingsOpen = false;
  elements.settingsButton.focus();
}

function resizeChatInput() {
  elements.chatInput.style.height = "auto";
  elements.chatInput.style.height = `${Math.min(elements.chatInput.scrollHeight, 116)}px`;
}

function updateComposerState() {
  const hasText = elements.chatInput.value.trim().length > 0;
  elements.chatComposer.classList.toggle("has-text", hasText);
  const state = coreTransport.snapshot();
  elements.sendButton.disabled = !hasText || state.status !== 'connected' || !!state.pendingTurn;
  resizeChatInput();
}

function setVoiceMode(active) {
  const enabled = Boolean(active);
  elements.chatComposer.classList.toggle("voice-active", enabled);
  elements.chatInput.disabled = enabled;
  elements.microphoneButton.setAttribute("aria-pressed", String(enabled));
  elements.voiceModeButton.setAttribute("aria-pressed", String(enabled));
  elements.voiceStatus.setAttribute("aria-hidden", String(!enabled));
  window.__nanaApp.chat.voiceMode = enabled;
  conversationController.setListening?.(enabled);
  if (!enabled) window.requestAnimationFrame(() => elements.chatInput.focus());
}

function setConversationState(state) {
  const responding = state === "thinking" || state === "speaking";
  elements.chatComposer.dataset.conversationState = state;
  elements.chatComposer.classList.toggle("responding", responding);
  elements.conversationStatus.hidden = !responding;
  elements.chatInput.disabled = responding || state === "listening";
  elements.conversationStatusLabel.textContent = state === "thinking" ? "Nana đang nghĩ..." : "Nana đang nói...";
  elements.voiceStatusLabel.textContent = "Mic chưa bật trong bản v1";
  window.__nanaApp.chat.conversationState = state;
  if (state === "idle" && !window.__nanaApp.chat.voiceMode) {
    window.requestAnimationFrame(() => elements.chatInput.focus());
  }
}

function updateVisibleMessageDiagnostics() {
  window.__nanaApp.chat.visibleRoles = [...recentMessages.keys()];
}

function applySubtitleVisibility(message, role) {
  message.classList.toggle(
    "subtitle-hidden",
    role === "nana" && settings.showSubtitles !== true,
  );
}

function removeRecentMessage(role, message) {
  if (recentMessages.get(role) !== message) return;
  window.clearTimeout(recentMessageTimers.get(role));
  recentMessages.delete(role);
  recentMessageTimers.delete(role);
  expiredRecentRoles.add(role);
  message.remove();
  updateVisibleMessageDiagnostics();
}

function fadeRecentMessage(role, message, delayMs = 0) {
  if (!message || recentMessageTimers.has(role)) return;
  recentMessageTimers.set(role, window.setTimeout(() => {
    if (recentMessages.get(role) !== message) return;
    message.style.setProperty('--message-fade-duration', `${RECENT_MESSAGE_FADE_MS}ms`);
    message.classList.add('expiring');
    recentMessageTimers.set(role, window.setTimeout(
      () => removeRecentMessage(role, message), RECENT_MESSAGE_FADE_MS,
    ));
  }, delayMs));
}

function pushRecentMessage(role, text, { streaming = false } = {}) {
  const normalizedRole = role === "nana" ? "nana" : "user";
  const normalizedText = String(text || "").trim();
  if (!normalizedText) return false;

  window.clearTimeout(recentMessageTimers.get(normalizedRole));
  recentMessageTimers.delete(normalizedRole);
  expiredRecentRoles.delete(normalizedRole);
  recentMessages.get(normalizedRole)?.remove();

  const message = document.createElement("article");
  message.className = `recent-message ${normalizedRole}`;
  message.dataset.role = normalizedRole;

  const author = document.createElement("span");
  author.className = "message-author";
  author.textContent = normalizedRole === "nana" ? "Nana" : "Bạn";

  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  bubble.title = normalizedText;

  const messageText = document.createElement("span");
  messageText.className = "message-text";
  messageText.textContent = normalizedText;

  bubble.appendChild(messageText);
  message.append(author, bubble);
  message.classList.toggle("streaming", streaming);
  applySubtitleVisibility(message, normalizedRole);
  elements.chatPreview.appendChild(message);
  recentMessages.set(normalizedRole, message);
  if (normalizedRole === 'user') fadeRecentMessage(normalizedRole, message, RECENT_MESSAGE_TTL_MS);
  updateVisibleMessageDiagnostics();
  return true;
}

function updateRecentMessage(role, text, { streaming = false } = {}) {
  const normalizedRole = role === "nana" ? "nana" : "user";
  const normalizedText = String(text || "").trim();
  if (!normalizedText) return false;

  const message = recentMessages.get(normalizedRole);
  // Reconcile may update voice state after this turn's text has already expired.
  if (!message && expiredRecentRoles.has(normalizedRole)) return false;
  if (!message) return pushRecentMessage(normalizedRole, normalizedText, { streaming });

  const messageText = message.querySelector(".message-text");
  if (!messageText) return false;
  messageText.textContent = normalizedText;
  const bubble = message.querySelector(".message-bubble");
  if (bubble) bubble.title = normalizedText;
  message.classList.toggle("streaming", streaming);
  applySubtitleVisibility(message, normalizedRole);

  return true;
}

function submitLocalMessage() {
  const text = elements.chatInput.value.trim();
  if (!text) return;
  const transportState = coreTransport.snapshot();
  if (transportState.status !== "connected" || transportState.pendingTurn) {
    setConversationState(transportState.pendingTurn ? "thinking" : "idle");
    return;
  }
  const turnId = globalThis.crypto?.randomUUID?.();
  const correlationId = globalThis.crypto?.randomUUID?.();
  if (!turnId || !correlationId) return;
  try {
    coreTransport.submit({ turnId, correlationId, text });
    safelySendUnityTo(RUNTIME_OBJECT, "SetMouthTest", 0);
    conversationController.beginTurn({turnId, correlationId});
    window.clearTimeout(recentMessageTimers.get('nana'));
    recentMessageTimers.delete('nana');
    expiredRecentRoles.delete('nana');
    recentMessages.get('nana')?.remove();
    recentMessages.delete('nana');
  } catch {
    return;
  }
  pushRecentMessage("user", text);
  elements.chatInput.value = "";
  window.__nanaApp.chat.submissionCount += 1;
  updateComposerState();
}

function coreContext() {
  const state = coreTransport.snapshot();
  if (!state.serverEpoch || !state.sessionId) return null;
  return {
    serverEpoch: state.serverEpoch,
    sessionId: state.sessionId,
    clientInstanceId: state.clientInstanceId,
  };
}

const conversationController = createConversationController({
  onAction: ({ snapshot }) => {
    const state = snapshot.turnState;
    if (state === "accepted" || state === "thinking" || state === "generated") setConversationState("thinking");
    else if (state === "complete" || state === "failed" || state === "unknown") setConversationState("idle");
    const labels = {idle: '', queued: 'Đang chuẩn bị giọng nói', speaking: 'Nana đang nói', delivered: 'Đã nói xong', failed: 'Phát giọng nói thất bại', unknown: 'Chưa xác nhận kết quả giọng nói'};
    document.querySelector('#voice-result-status').textContent = snapshot.uncertain ? 'Đang xác nhận kết quả với Nana' : labels[snapshot.voiceState];
  },
});

const privateAvatarConsumer = createPrivateAvatarSignalConsumer({sendUnity: sendUnityTo});

function syncPrivateAvatarContext(state) {
  const turn = state.lastTurn;
  privateAvatarConsumer.syncContext({
    connected: state.status === "connected" && state.confirmed === true,
    serverEpoch: state.serverEpoch,
    sessionId: state.sessionId,
    turnId: turn?.turnId,
    correlationId: turn?.correlationId,
    voiceState: turn?.voiceState,
  });
}

const coreTransport = createCoreChatTransport({
  onState: (state) => {
    window.__nanaApp.coreConnected = state.status === "connected";
    const labels = {connected: 'Đã kết nối Nana', connecting: 'Đang kết nối Nana', handshaking: 'Đang kết nối Nana', disconnected: 'Chưa kết nối Nana', degraded: 'Kết nối Nana gặp lỗi', stopped: 'Đã ngắt kết nối'};
    document.querySelector('#connection-status').textContent = labels[state.status] || labels.disconnected;
    if (state.status === 'connected') conversationController.connect(coreContext());
    else conversationController.disconnect();
    syncPrivateAvatarContext(state);
    updateComposerState();
  },
  onVisual: (signal) => privateAvatarConsumer.accept(signal),
  onEvent: (event) => {
    const result = conversationController.apply(event, coreContext());
    if (result.action !== "applied") return result;
    if (event.type === "assistant.delta") updateRecentMessage("nana", `${conversationController.snapshot().activeTurn?.text || ""}`, { streaming: true });
    if (event.type === "assistant.final" || event.type === 'turn.snapshot') {
      const streaming = event.type === 'turn.snapshot'
        && ['accepted', 'thinking'].includes(event.payload.turn_state);
      updateRecentMessage('nana', event.payload.text, {streaming});
      if (!streaming) document.querySelector('#assistant-announcement').textContent = event.payload.text;
    }
    if (event.type === 'turn.state' && ['failed', 'unknown'].includes(event.payload.state)) {
      updateRecentMessage('nana', conversationController.snapshot().activeTurn?.text, {streaming: false});
    }
    // Text completion is not audio completion. Only the correlated Core receipt
    // may fade Nana's bubble; reconcile must not restart or resurrect the fade.
    if (result.snapshot.turnState === 'complete' && result.snapshot.voiceState === 'delivered') {
      fadeRecentMessage('nana', recentMessages.get('nana'));
    }
    return result;
  },
});

function bindCheckbox(element, key, onChange = null) {
  element.addEventListener("change", () => {
    settings[key] = element.checked;
    persistSettings();
    onChange?.();
  });
}

function bindSelect(element, key, onChange = null) {
  element.addEventListener("change", () => {
    settings[key] = element.value;
    persistSettings();
    onChange?.();
  });
}

function bindNumberInput(element, key, onChange = null) {
  element.addEventListener("input", () => {
    settings[key] = Number(element.value);
    persistSettings();
    onChange?.();
  });
}

function bindObsNumberInput(element, key) {
  element.addEventListener("input", () => {
    obsSettings = normalizeObsConfig({ ...obsSettings, [key]: Number(element.value) });
    persistObsSettings();
    setObsStatus("URL đã thay đổi, hãy cập nhật Browser Source trong OBS.");
  });
}

function finishAppReady() {
  if (appReady) return;
  appReady = true;
  elements.loading.hidden = true;
  elements.app.classList.add("ready");
  window.__nanaApp.status = "ready";
  window.__nanaApp.unityReady = true;
}

function requestPresentationModel() {
  if (!unityInstance || modelSelectionApplied) return;
  modelSelectionApplied = true;
  window.clearTimeout(modelSelectionTimer);
  modelSelectionTimer = window.setTimeout(() => {
    sendUnityTo(MODEL_OBJECT, "SelectModel", PRESENTATION_MODEL);
    window.setTimeout(finishAppReady, 2400);
  }, 1200);
}

window.addEventListener("nana-avatar-model-state", ({ detail }) => {
  window.__nanaApp.model = {
    ready: detail?.ready === true,
    selected: Number(detail?.selected) || null,
    requested: PRESENTATION_MODEL,
  };
  if (detail?.ready === true && Number(detail.selected) === PRESENTATION_MODEL) {
    modelSelectionApplied = true;
    window.clearTimeout(modelSelectionTimer);
    if (unityInstance) finishAppReady();
    return;
  }
  if (detail?.ready === true) requestPresentationModel();
});

function showError(error) {
  elements.loading.hidden = true;
  elements.errorLayer.hidden = false;
  elements.errorMessage.textContent = error instanceof Error ? error.message : String(error);
  window.__nanaApp.status = "error";
}

async function loadUnity() {
  if (loading) return;
  loading = true;
  privateAvatarConsumer.setUnityReady(false);
  unityInstance = null;
  window.__nanaApp.unityReady = false;
  elements.errorLayer.hidden = true;
  elements.loading.hidden = false;
  elements.progress.style.width = "0%";
  elements.loadingDetail.textContent = "0%";
  elements.loadingLabel.textContent = "Đang mở không gian của Nana";
  window.__nanaApp.status = "loading";

  try {
    await new Promise((resolve, reject) => {
      const existing = document.querySelector(`script[src="${BUILD.loaderUrl}"]`);
      if (existing && window.createUnityInstance) {
        resolve();
        return;
      }
      const script = existing || document.createElement("script");
      script.src = BUILD.loaderUrl;
      script.onload = resolve;
      script.onerror = () => reject(new Error("Không tải được Unity WebGL loader"));
      if (!existing) document.body.appendChild(script);
    });

    unityInstance = await window.createUnityInstance(
      elements.canvas,
      {
        dataUrl: BUILD.dataUrl,
        frameworkUrl: BUILD.frameworkUrl,
        codeUrl: BUILD.codeUrl,
        streamingAssetsUrl: BUILD.streamingAssetsUrl,
        companyName: "Nana Local",
        productName: "Nana",
        productVersion: "0.1.0",
        devicePixelRatio: Math.min(window.devicePixelRatio || 1, 1.5),
      },
      (progress) => {
        const percent = Math.round(progress * 100);
        elements.progress.style.width = `${percent}%`;
        elements.loadingDetail.textContent = `${percent}%`;
        if (percent >= 90) elements.loadingLabel.textContent = "Nana sắp sẵn sàng";
      },
    );

    applyAvatarSettings();
    privateAvatarConsumer.setUnityReady(true);
    if (window.__nanaApp.model.selected === PRESENTATION_MODEL) finishAppReady();
    else requestPresentationModel();
  } catch (error) {
    showError(error);
  } finally {
    loading = false;
  }
}

elements.settingsButton.addEventListener("click", () => openSettings());
elements.backToApp.addEventListener("click", closeSettings);
elements.closeSettings.addEventListener("click", closeSettings);
elements.settingsSearch.addEventListener("input", () => filterSettings(elements.settingsSearch.value));
elements.navItems.forEach((button) => {
  button.addEventListener("click", () => {
    elements.settingsSearch.value = "";
    selectSettingsSection(button.dataset.settingsSection);
  });
});

elements.cameraPresetButtons.forEach((button) => {
  button.addEventListener("click", () => {
    settings = { ...settings, ...CAMERA_PRESETS[button.dataset.cameraPreset] };
    syncControlsFromSettings();
    applyCameraSettings();
    updatePresetSelection(button.dataset.cameraPreset);
  });
});

bindSelect(elements.startupView, "startupView");
bindCheckbox(elements.rememberWindow, "rememberWindow");
bindCheckbox(elements.startFullscreen, "startFullscreen");
bindSelect(elements.theme, "theme");
bindNumberInput(elements.uiScale, "uiScale", applyShellSettings);
bindCheckbox(elements.reduceMotion, "reduceMotion", applyShellSettings);
bindNumberInput(elements.cameraZoom, "cameraZoom", applyCameraSettings);
bindNumberInput(elements.cameraHeight, "cameraHeight", applyCameraSettings);
bindNumberInput(elements.cameraYaw, "cameraYaw", applyCameraSettings);
bindNumberInput(elements.cameraPitch, "cameraPitch", applyCameraSettings);
bindCheckbox(elements.idleToggle, "idleEnabled", applyAvatarSettings);
bindCheckbox(elements.blinkToggle, "blinkEnabled", applyAvatarSettings);
bindNumberInput(elements.moveSpeed, "moveSpeed", updateCameraOutputs);
bindNumberInput(elements.idleYaw, "idleYaw", applyAvatarSettings);
bindNumberInput(elements.eyeLead, "eyeLead", applyAvatarSettings);
bindNumberInput(elements.mouthTest, "mouthTest", applyAvatarSettings);
bindObsNumberInput(elements.obsZoom, "zoom");
bindObsNumberInput(elements.obsHeight, "height");
bindObsNumberInput(elements.obsYaw, "yaw");
bindObsNumberInput(elements.obsPitch, "pitch");
bindCheckbox(elements.sendOnEnter, "sendOnEnter");
bindCheckbox(elements.showSubtitles, "showSubtitles", () => {
  recentMessages.get("nana") && applySubtitleVisibility(recentMessages.get("nana"), "nana");
});
bindSelect(elements.composerPosition, "composerPosition");
bindSelect(elements.microphone, "microphone");
bindCheckbox(elements.pushToTalk, "pushToTalk");
bindCheckbox(elements.autoListen, "autoListen");

elements.chatInput.addEventListener("input", updateComposerState);
elements.chatInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && settings.sendOnEnter) {
    event.preventDefault();
    elements.chatComposer.requestSubmit();
  }
});
elements.chatComposer.addEventListener("submit", (event) => {
  event.preventDefault();
  submitLocalMessage();
});
elements.microphoneButton.addEventListener("click", () => {
  setVoiceMode(!elements.chatComposer.classList.contains("voice-active"));
});
elements.voiceModeButton.addEventListener("click", () => {
  setVoiceMode(!elements.chatComposer.classList.contains("voice-active"));
});

elements.obsUseCurrent.addEventListener("click", () => {
  obsSettings = normalizeObsConfig({
    zoom: settings.cameraZoom,
    height: settings.cameraHeight,
    yaw: settings.cameraYaw,
    pitch: settings.cameraPitch,
  });
  persistObsSettings();
  setObsStatus("Đã lấy khung hình hiện tại. Hãy cập nhật URL trong OBS.");
});

elements.obsOpenPreview.addEventListener("click", () => {
  window.open(currentObsUrl(), "_blank", "noopener,noreferrer");
  setObsStatus("Đã mở bản xem trước. Đây chưa phải xác nhận OBS đã kết nối.");
});

elements.obsCopyUrl.addEventListener("click", async () => {
  const url = currentObsUrl();
  try {
    await navigator.clipboard.writeText(url);
  } catch {
    elements.obsUrl.focus();
    elements.obsUrl.select();
    document.execCommand("copy");
  }
  setObsStatus("Đã sao chép URL avatar cho OBS.");
});

window.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !elements.settingsView.hidden) closeSettings();
});

window.addEventListener("resize", () => {
  window.clearTimeout(resizeTimer);
  resizeTimer = window.setTimeout(applyCameraSettings, 140);
});

elements.retry.addEventListener("click", loadUnity);
elements.microphoneButton.disabled = true;
elements.voiceModeButton.disabled = true;
elements.microphoneButton.title = "Mic sẽ được bật ở increment sau";
elements.voiceModeButton.title = "Voice control sẽ được bật ở increment sau";
syncControlsFromSettings();
elements.app.classList.add('ready');
updateComposerState();
privateAvatarConsumer.setVisible(document.visibilityState !== 'hidden');
document.addEventListener('visibilitychange', () => {
  const visible = document.visibilityState !== 'hidden';
  privateAvatarConsumer.setVisible(visible);
  if (!visible) safelySendUnityTo(RUNTIME_OBJECT, "SetMouthTest", 0);
});
coreTransport.start().catch(() => {
  window.__nanaApp.coreConnected = false;
});
window.addEventListener('pagehide', () => {
  privateAvatarConsumer.stop();
  coreTransport.stop(); conversationController.reset(null);
  elements.chatInput.value = ''; elements.chatPreview.replaceChildren();
  document.querySelector('#assistant-announcement').textContent = '';
  recentMessages.clear();
});
loadUnity();
