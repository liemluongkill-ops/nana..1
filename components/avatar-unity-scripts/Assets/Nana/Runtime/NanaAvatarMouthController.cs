using System;
using System.Collections.Generic;
using System.Globalization;
using System.Collections;
using UnityEngine;
using UnityEngine.Networking;

// Mouth-only channel. It accepts normalized voice energy now and can accept
// explicit visemes later without competing with eyes or facial events.
[DefaultExecutionOrder(1200)]
public sealed class NanaAvatarMouthController : MonoBehaviour
{
    [Serializable]
    private sealed class MouthPayload
    {
        public float open;
        public float energy;
        public string viseme;
        public bool speaking;
        public long timestamp_ms;
    }

    [Serializable]
    private sealed class MouthStreamResponse
    {
        public bool ok;
        public string protocol;
        public int cursor;
        public int stale_after_ms;
        public MouthPayload sample;
    }

    public SkinnedMeshRenderer face;
    [Range(0.02f, 0.25f)] public float attackSeconds = 0.055f;
    [Range(0.03f, 0.35f)] public float releaseSeconds = 0.045f;
    [Range(0f, 2f)] public float sensitivity = 1.35f;
    [Range(0f, 40f)] public float testOpen;
    [Range(0.1f, 0.4f)] public float maxOpen = 0.4f;
    public bool gatewayMouthPollingEnabled = true;
    [Range(0.02f, 0.25f)] public float gatewayPollInterval = 0.02f;

    private readonly Dictionary<string, int> _indices = new Dictionary<string, int>();
    private readonly Dictionary<string, float> _baseline = new Dictionary<string, float>();
    private readonly Dictionary<string, float> _weights = new Dictionary<string, float>();
    private float _targetOpen;
    private float _currentOpen;
    private string _targetViseme = "aa";
    private bool _ready;
    private NanaTargetDrivenAvatarController _gaze;
    private Coroutine _gatewayRoutine;
    private bool _streamActive;
    private bool _gatewayWasActive;
    private float _nextTelemetryAt;
    private float _sampleExpiresAt;

    private float OpenLimit => Mathf.Clamp(maxOpen, 0f, .4f);

#if UNITY_WEBGL && !UNITY_EDITOR
    [System.Runtime.InteropServices.DllImport("__Internal")]
    private static extern void NanaAvatarMouthState(string json);
#endif

    private static readonly string[] MouthShapes = {
        "vrc.v_aa", "vrc.v_ih", "vrc.v_ee", "vrc.v_oh", "vrc.v_ou", "vrc.v_nn", "vrc.v_sil",
        "mouth_a1", "mouth_i1", "mouth_o1", "mouth_0"
    };

    public float CurrentOpen => _currentOpen;
    public string CurrentViseme => _targetViseme;

    private void Start()
    {
        if (face == null)
        {
            NanaTargetDrivenAvatarController gaze = GetComponentInChildren<NanaTargetDrivenAvatarController>(true);
            face = gaze != null ? gaze.face : FindFaceRenderer(transform.root);
        }
        if (face == null || face.sharedMesh == null) return;
        foreach (string shape in MouthShapes)
        {
            int index = face.sharedMesh.GetBlendShapeIndex(shape);
            _indices[shape] = index;
            _baseline[shape] = index >= 0 ? face.GetBlendShapeWeight(index) : 0f;
            _weights[shape] = 0f;
        }
        PublishTelemetry();
        _ready = true;
        _gaze = GetComponentInChildren<NanaTargetDrivenAvatarController>(true);
        _gatewayRoutine = StartCoroutine(PollGatewayLoop());
    }

    private void Update()
    {
        if (!_ready) return;
        // Expire even while an HTTP request is stalled; silence must not wait for it.
        if (_streamActive && Time.realtimeSinceStartup >= _sampleExpiresAt)
            CloseStream();
        float dt = Mathf.Max(0f, Time.unscaledDeltaTime);
        float target = _streamActive
            ? Mathf.Clamp(_targetOpen, 0f, OpenLimit)
            : Mathf.Clamp(Mathf.Max(_targetOpen, testOpen / 100f), 0f, OpenLimit);
        float seconds = target > _currentOpen ? attackSeconds : releaseSeconds;
        _currentOpen = Mathf.MoveTowards(_currentOpen, target, dt / Mathf.Max(0.01f, seconds));

        foreach (string shape in MouthShapes)
        {
            float desired = VisemeWeight(shape, _targetViseme, _currentOpen);
            // Opening is already eased above. Do not low-pass a closing edge twice.
            _weights[shape] = desired <= _weights[shape]
                ? desired : Mathf.MoveTowards(_weights[shape], desired, dt * 900f);
            int index = _indices[shape];
            if (index >= 0)
                face.SetBlendShapeWeight(index, Mathf.Clamp(_baseline[shape] + _weights[shape], 0f, 100f));
        }
    }

    private void LateUpdate()
    {
        if (_ready && Time.unscaledTime >= _nextTelemetryAt)
        {
            _nextTelemetryAt = Time.unscaledTime + .02f;
            PublishTelemetry();
        }
    }

    public void SetMouthOpen(string value)
    {
        if (!float.TryParse(value, NumberStyles.Float, CultureInfo.InvariantCulture, out float parsed)) return;
        if (float.IsNaN(parsed) || float.IsInfinity(parsed)) return;
        _targetOpen = Mathf.Clamp(parsed, 0f, 1f) * OpenLimit;
        _targetViseme = "aa";
    }

    public void SetMouthVisemeJson(string json)
    {
        if (string.IsNullOrWhiteSpace(json)) return;
        try
        {
            MouthPayload payload = JsonUtility.FromJson<MouthPayload>(json);
            if (payload == null) return;
            float open = Mathf.Max(payload.open, payload.energy * sensitivity);
            if (float.IsNaN(open) || float.IsInfinity(open)) return;
            _targetOpen = Mathf.Clamp(open, 0f, 1f) * OpenLimit;
            string viseme = (payload.viseme ?? "aa").Trim().ToLowerInvariant();
            _targetViseme = IsSupportedViseme(viseme) ? viseme : "aa";
        }
        catch (Exception exception)
        {
            Debug.LogWarning("Nana mouth payload invalid: " + exception.GetType().Name, this);
        }
    }

    public void SetMouthTest(string value) { SetMouthOpen(value); }

    private IEnumerator PollGatewayLoop()
    {
        while (isActiveAndEnabled)
        {
            float requestStartedAt = Time.realtimeSinceStartup;
            if (_gaze == null)
                _gaze = GetComponentInChildren<NanaTargetDrivenAvatarController>(true);
            bool gatewayActive = gatewayMouthPollingEnabled && _gaze != null && _gaze.gatewayPollingEnabled;
            if (gatewayActive)
            {
                _gatewayWasActive = true;
                using (UnityWebRequest request = UnityWebRequest.Get(
                    _gaze.ResolveGatewayBaseUrl() + "/v1/avatar/mouth"))
                {
                    request.timeout = 1;
                    if (!string.IsNullOrEmpty(_gaze.gatewayToken))
                        request.SetRequestHeader("Authorization", "Bearer " + _gaze.gatewayToken);
                    yield return request.SendWebRequest();
                    if (request.result == UnityWebRequest.Result.Success)
                    {
                        ApplyGatewaySample(request.downloadHandler.text);
                    }
                    else
                    {
                        CloseStream();
                    }
                }
            }
            else if (_gatewayWasActive)
            {
                // Gateway loss and explicit disable both fail closed.
                _gatewayWasActive = false;
                _streamActive = false;
                _targetOpen = 0f;
                _targetViseme = "sil";
            }
            // Start-to-start cadence, with only one request in flight.
            float remaining = Mathf.Max(.02f, gatewayPollInterval)
                - (Time.realtimeSinceStartup - requestStartedAt);
            if (remaining > 0f) yield return new WaitForSecondsRealtime(remaining);
            else yield return null;
        }
        _gatewayRoutine = null;
    }

    private void ApplyGatewaySample(string json)
    {
        MouthStreamResponse response;
        try { response = JsonUtility.FromJson<MouthStreamResponse>(json); }
        catch { response = null; }
        if (response == null || !response.ok || response.protocol != "nana.avatar.mouth.v1" || response.sample == null)
        {
            CloseStream();
            return;
        }
        long nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        long ageMs = nowMs - response.sample.timestamp_ms;
        int staleAfter = response.stale_after_ms > 0 ? Math.Min(response.stale_after_ms, 180) : 180;
        _streamActive = true;
        if (response.sample.timestamp_ms <= 0 || ageMs < -100 || ageMs >= staleAfter || !response.sample.speaking)
        {
            CloseStream();
            return;
        }
        float open = Mathf.Max(response.sample.open, response.sample.energy * sensitivity);
        if (float.IsNaN(open) || float.IsInfinity(open)) { CloseStream(); return; }
        _sampleExpiresAt = Time.realtimeSinceStartup + (staleAfter - Math.Max(0, ageMs)) / 1000f;
        _targetOpen = Mathf.Clamp01(open) * OpenLimit;
        _targetViseme = IsSupportedViseme(response.sample.viseme) ? response.sample.viseme : "aa";
    }

    private void CloseStream()
    {
        _streamActive = true;
        _targetOpen = 0f;
        _targetViseme = "sil";
        _sampleExpiresAt = 0f;
    }

    private void OnDisable()
    {
        if (_gatewayRoutine != null)
        {
            StopCoroutine(_gatewayRoutine);
            _gatewayRoutine = null;
        }
    }

    private void PublishTelemetry()
    {
#if UNITY_WEBGL && !UNITY_EDITOR
        NanaAvatarMouthState(JsonUtility.ToJson(new MouthTelemetry {
            ready = _ready, open = _currentOpen, target = _targetOpen, viseme = _targetViseme,
            stream_active = _streamActive,
            cadence_revision = "fast-close-v1",
            aa_weight = face != null && _indices.TryGetValue("vrc.v_aa", out int aa) && aa >= 0
                ? face.GetBlendShapeWeight(aa) : 0f,
            mouth_a_weight = face != null && _indices.TryGetValue("mouth_a1", out int a) && a >= 0
                ? face.GetBlendShapeWeight(a) : 0f,
        }));
#endif
    }

    [Serializable]
    private sealed class MouthTelemetry
    {
        public bool ready;
        public float open;
        public float target;
        public string viseme;
        public bool stream_active;
        public string cadence_revision;
        public float aa_weight;
        public float mouth_a_weight;
    }

    [ContextMenu("Nana/Mouth Open")]
    private void ContextMouthOpen() { SetMouthOpen("0.8"); }

    [ContextMenu("Nana/Mouth Close")]
    private void ContextMouthClose() { SetMouthOpen("0"); }

    private static bool IsSupportedViseme(string value)
    {
        return value == "aa" || value == "ih" || value == "ee" || value == "oh" ||
               value == "ou" || value == "nn" || value == "sil";
    }

    private static float VisemeWeight(string shape, string viseme, float open)
    {
        float factor = open * 100f;
        if (viseme == "sil") return 0f;
        if (shape == "mouth_a1") return viseme == "aa" ? factor * .82f : factor * .08f;
        if (shape == "mouth_i1") return viseme == "ih" || viseme == "ee" ? factor * .48f : factor * .04f;
        if (shape == "mouth_o1") return viseme == "oh" ? factor * .78f : factor * .05f;
        if (shape == "mouth_0") return viseme == "oh" || viseme == "ou" ? factor * .42f : 0f;
        if (shape == "vrc.v_aa") return viseme == "aa" ? factor : factor * .12f;
        if (shape == "vrc.v_ih") return viseme == "ih" ? factor * .72f : factor * .05f;
        if (shape == "vrc.v_ee") return viseme == "ee" ? factor * .65f : factor * .04f;
        if (shape == "vrc.v_oh") return viseme == "oh" ? factor * .82f : factor * .08f;
        if (shape == "vrc.v_ou") return viseme == "ou" ? factor * .75f : factor * .06f;
        if (shape == "vrc.v_nn") return viseme == "nn" ? factor * .35f : 0f;
        return 0f;
    }

    private static SkinnedMeshRenderer FindFaceRenderer(Transform root)
    {
        foreach (SkinnedMeshRenderer candidate in root.GetComponentsInChildren<SkinnedMeshRenderer>(true))
            if (candidate.sharedMesh != null && candidate.sharedMesh.GetBlendShapeIndex("vrc.v_aa") >= 0) return candidate;
        return null;
    }
}
