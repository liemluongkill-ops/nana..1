using System;
using System.Collections;
using System.Globalization;
using UnityEngine;
using UnityEngine.Networking;

#pragma warning disable 0649

/// <summary>
/// Small target-driven head/eye layer for the local Nana avatar candidate.
/// The component owns only head, neck, gaze and blink. Gesture layers remain
/// free to own the rest of the avatar.
/// </summary>
[DefaultExecutionOrder(1000)]
public sealed class NanaTargetDrivenAvatarController : MonoBehaviour
{
    [Serializable]
    public struct LookTarget
    {
        public float yaw;
        public float pitch;
        public float roll;

        public LookTarget(float yaw, float pitch, float roll)
        {
            this.yaw = yaw;
            this.pitch = pitch;
            this.roll = roll;
        }

        public Vector3 ToVector()
        {
            return new Vector3(pitch, yaw, roll);
        }

        public static LookTarget FromVector(Vector3 value)
        {
            return new LookTarget(value.y, value.x, value.z);
        }
    }

    [Serializable]
    private sealed class PollResponse
    {
        public bool ok;
        public int cursor;
        public string session_id;
        public CommandEnvelope[] commands;
    }

    [Serializable]
    private sealed class CommandEnvelope
    {
        public int cursor;
        public string protocol;
        public string type;
        public IntentPayload intent;
    }

    [Serializable]
    private sealed class IntentPayload
    {
        public string intent_id;
        public string action;
        public float duration_s;
        public float move_s;
        public float hold_s;
        public float return_s;
        public string return_to;
        public LookTargetPayload target;
    }

    [Serializable]
    private sealed class LookTargetPayload
    {
        public float yaw;
        public float pitch;
        public float roll;
    }

    [Serializable]
    private sealed class DirectLookPayload
    {
        public LookTargetPayload target;
        public float move_s = 0.8f;
        public float hold_s = 0.7f;
        public float return_s = 0.8f;
        public string return_to = "neutral";
    }

#pragma warning restore 0649

    private enum MotionPhase
    {
        IdlePause,
        Moving,
        Holding,
        Returning,
        Held,
    }

    private const string ProtocolName = "nana.avatar.v1";
    private const float MinimumPhaseSeconds = 0.05f;

    [Header("Rig")]
    public Animator animator;
    public NanaAvatarEventController events;
    public NanaAvatarMouthController mouth;
    public Transform neck;
    public Transform head;
    public SkinnedMeshRenderer face;

    [Header("Ownership")]
    public bool idleMotionEnabled = true;
    public bool gatewayPollingEnabled = false;
    public string gatewayUrl = "http://127.0.0.1:8766";
    public string gatewayToken = "";
    [Range(0.05f, 1f)] public float gatewayPollInterval = 0.1f;
    public int gatewayInitialCursor = 0;

    [Header("Safe limits (degrees)")]
    [Range(1f, 30f)] public float maxYaw = 30f;
    [Range(1f, 15f)] public float maxPitch = 15f;
    [Range(1f, 12f)] public float maxRoll = 12f;
    [Range(0.05f, 1f)] public float neckShare = 0.32f;

    [Header("Idle target generator")]
    [Range(0f, 15f)] public float idleYaw = 8f;
    [Range(0f, 8f)] public float idlePitch = 4f;
    [Range(0f, 6f)] public float idleRoll = 2.5f;
    [Range(0.6f, 3f)] public float idleMoveMin = 1.1f;
    [Range(0.6f, 4f)] public float idleMoveMax = 2.2f;
    [Range(0f, 3f)] public float idleHoldMin = 0.35f;
    [Range(0f, 3f)] public float idleHoldMax = 1.1f;
    [Range(0.2f, 3f)] public float idleReturnMin = 0.75f;
    [Range(0.2f, 3f)] public float idleReturnMax = 1.5f;
    [Range(0.1f, 3f)] public float idlePauseMin = 0.45f;
    [Range(0.1f, 4f)] public float idlePauseMax = 1.7f;

    [Header("Eyes")]
    [Range(0.04f, 0.5f)] public float eyeSmoothTime = 0.12f;
    [Range(0.2f, 1.2f)] public float eyeLeadMultiplier = 0.55f;
    [Range(0f, 30f)] public float eyeYawLimit = 18f;
    [Range(0f, 20f)] public float eyePitchLimit = 12f;
    [Range(0f, 100f)] public float eyeWeightScale = 100f;

    [Header("Blink")]
    public bool blinkEnabled = true;
    [Range(1f, 12f)] public float blinkIntervalMin = 2.8f;
    [Range(1f, 16f)] public float blinkIntervalMax = 6.4f;
    [Range(0.04f, 0.2f)] public float blinkCloseSeconds = 0.1f;
    [Range(0.04f, 0.3f)] public float blinkOpenSeconds = 0.12f;

    [Header("Candidate preview")]
    public bool runRetargetDemoOnPlay = false;

    private readonly int[] _eyeIndices = { -1, -1, -1, -1, -1, -1, -1 };
    private readonly float[] _faceBaseWeights = { 0f, 0f, 0f, 0f, 0f, 0f, 0f };
    private Quaternion _baseNeckRotation;
    private Quaternion _baseHeadRotation;
    private Vector3 _currentOffset;
    private Vector3 _motionStart;
    private Vector3 _motionTarget;
    private Vector3 _eyeCurrent;
    private Vector3 _eyeVelocity;
    private MotionPhase _phase = MotionPhase.IdlePause;
    private float _phaseElapsed;
    private float _moveSeconds;
    private float _holdSeconds;
    private float _returnSeconds;
    private float _idlePauseRemaining;
    private bool _returnToNeutral;
    private bool _rigReady;
    private bool _basePoseCaptured;
    private bool _eventHeadOwned;
    private bool _pendingLook;
    private LookTarget _pendingTarget;
    private float _pendingMoveSeconds;
    private float _pendingHoldSeconds;
    private float _pendingReturnSeconds;
    private bool _pendingReturnToNeutral;
    private bool _blinkActive;
    private float _blinkElapsed;
    private float _nextBlinkAt;
    private int _gatewayCursor;
    private string _gatewaySession;
    private Coroutine _gatewayPollRoutine;
    private float _lastGatewayWarningAt = -100f;

    private static readonly string[] EyeShapeNames =
    {
        "eye_close",
        "eye_close_L",
        "eye_close_R",
        "eye_look_left",
        "eye_look_right",
        "eye_look_up",
        "eye_look_down",
    };

    public Vector3 CurrentOffset => _currentOffset;
    public string CurrentPhase => _phase.ToString();
    public bool RigReady => _rigReady;

    private void Awake()
    {
#if UNITY_WEBGL && !UNITY_EDITOR
        // HTML chat/settings own typing when focused; the canvas keeps its input.
        WebGLInput.captureAllKeyboardInput = false;
#endif
        _gatewayCursor = Mathf.Max(0, gatewayInitialCursor);
        DiscoverRig();
        ScheduleNextBlink();
    }

    private void Start()
    {
        StartCoroutine(InitializeAfterAnimatorFrame());
    }

    private void LateUpdate()
    {
        if (!_rigReady)
        {
            DiscoverRig();
            if (_rigReady && !_basePoseCaptured)
                CaptureBasePose();
        }
        if (!_rigReady || !_basePoseCaptured)
            return;

        float delta = Mathf.Max(0f, Time.unscaledDeltaTime);
        if (!_eventHeadOwned) UpdateMotion(delta);
        ApplyHeadOffset();
        UpdateEyes(delta);
        UpdateBlink(delta);
    }

    private void Update()
    {
        if (!_basePoseCaptured)
            return;
        if (gatewayPollingEnabled && _gatewayPollRoutine == null)
            _gatewayPollRoutine = StartCoroutine(PollGatewayLoop());
        else if (!gatewayPollingEnabled && _gatewayPollRoutine != null)
        {
            StopCoroutine(_gatewayPollRoutine);
            _gatewayPollRoutine = null;
        }
    }

    private void OnDisable()
    {
        if (_gatewayPollRoutine != null)
        {
            StopCoroutine(_gatewayPollRoutine);
            _gatewayPollRoutine = null;
        }
        RestoreBasePose();
    }

    private void OnValidate()
    {
        maxYaw = Mathf.Clamp(maxYaw, 1f, 30f);
        maxPitch = Mathf.Clamp(maxPitch, 1f, 15f);
        maxRoll = Mathf.Clamp(maxRoll, 1f, 12f);
        neckShare = Mathf.Clamp(neckShare, 0.05f, 0.7f);
        idleMoveMax = Mathf.Max(idleMoveMin, idleMoveMax);
        idleHoldMax = Mathf.Max(idleHoldMin, idleHoldMax);
        idleReturnMax = Mathf.Max(idleReturnMin, idleReturnMax);
        idlePauseMax = Mathf.Max(idlePauseMin, idlePauseMax);
        blinkIntervalMax = Mathf.Max(blinkIntervalMin, blinkIntervalMax);
    }

    public void LookLeft()
    {
        RequestLook(new LookTarget(-24f, -3f, 2f), 0.8f, 0.7f, 0.8f, true);
    }

    public void LookRight()
    {
        RequestLook(new LookTarget(24f, -2f, -2f), 0.8f, 0.7f, 0.8f, true);
    }

    public void LookCenter()
    {
        RequestLook(new LookTarget(0f, 0f, 0f), 0.65f, 0.35f, 0.7f, true);
    }

    public void RequestLook(
        LookTarget target,
        float moveSeconds = 0.8f,
        float holdSeconds = 0.7f,
        float returnSeconds = 0.8f,
        bool returnToNeutral = true)
    {
        if (!_rigReady)
        {
            _pendingLook = true;
            _pendingTarget = target;
            _pendingMoveSeconds = moveSeconds;
            _pendingHoldSeconds = holdSeconds;
            _pendingReturnSeconds = returnSeconds;
            _pendingReturnToNeutral = returnToNeutral;
            return;
        }

        if (!_basePoseCaptured)
        {
            _pendingLook = true;
            _pendingTarget = target;
            _pendingMoveSeconds = moveSeconds;
            _pendingHoldSeconds = holdSeconds;
            _pendingReturnSeconds = returnSeconds;
            _pendingReturnToNeutral = returnToNeutral;
            return;
        }

        BeginLook(target, moveSeconds, holdSeconds, returnSeconds, returnToNeutral);
    }

    private void BeginLook(
        LookTarget target,
        float moveSeconds,
        float holdSeconds,
        float returnSeconds,
        bool returnToNeutral)
    {
        _pendingLook = false;

        _motionStart = _currentOffset;
        _motionTarget = ClampTarget(target).ToVector();
        _moveSeconds = Mathf.Max(MinimumPhaseSeconds, moveSeconds);
        _holdSeconds = Mathf.Max(0f, holdSeconds);
        _returnSeconds = Mathf.Max(MinimumPhaseSeconds, returnSeconds);
        _returnToNeutral = returnToNeutral;
        _phaseElapsed = 0f;
        _phase = MotionPhase.Moving;
    }

    public void ReturnToNeutral()
    {
        RequestLook(new LookTarget(0f, 0f, 0f), 0.7f, 0.25f, 0.8f, true);
    }

    public void ReceiveLookIntentJson(string json)
    {
        if (string.IsNullOrWhiteSpace(json))
            return;
        DirectLookPayload payload;
        try
        {
            payload = JsonUtility.FromJson<DirectLookPayload>(json);
        }
        catch (Exception exception)
        {
            Debug.LogWarning("Nana look preview payload invalid: " + exception.GetType().Name, this);
            return;
        }
        if (payload == null || payload.target == null)
            return;
        if (events != null) events.InterruptHead();
        RequestLook(
            new LookTarget(payload.target.yaw, payload.target.pitch, payload.target.roll),
            Mathf.Max(0.1f, payload.move_s),
            Mathf.Max(0f, payload.hold_s),
            Mathf.Max(0.1f, payload.return_s),
            payload.return_to != "hold");
    }

    public void ReceiveAvatarIntentJson(string json)
    {
        try
        {
            var intent = JsonUtility.FromJson<NanaAvatarEventController.Intent>(json);
            if (intent == null) return;
            if (intent.action == "look") ReceiveLookIntentJson(json);
            else if (events != null) events.Receive(intent, false);
        }
        catch (Exception e) { Debug.LogWarning("Invalid avatar event: " + e.GetType().Name); }
    }

    public void SetEventHead(Vector3 offset)
    {
        _eventHeadOwned = true;
        _currentOffset = ClampTarget(LookTarget.FromVector(offset)).ToVector();
    }

    public void SetExpression(string action)
    {
        if (events != null) events.SetExpression(action);
    }

    public void ReleaseEventHead(bool neutral)
    {
        _eventHeadOwned = false;
        if (neutral) ReturnToNeutral();
    }

    public void SetIdleMotionEnabled(string value)
    {
        idleMotionEnabled = ParseToggle(value, idleMotionEnabled);
        if (!idleMotionEnabled && _basePoseCaptured)
            ReturnToNeutral();
    }

    public void SetBlinkEnabled(string value)
    {
        blinkEnabled = ParseToggle(value, blinkEnabled);
    }

    public void SetGatewayPollingEnabled(string value)
    {
        gatewayPollingEnabled = ParseToggle(value, gatewayPollingEnabled);
    }

    public void SetIdleYaw(string value)
    {
        idleYaw = ParseBoundedFloat(value, idleYaw, 0f, 15f);
    }

    public void SetEyeLeadMultiplier(string value)
    {
        eyeLeadMultiplier = ParseBoundedFloat(value, eyeLeadMultiplier, 0.2f, 1.2f);
    }

    public void SetMouthTest(string value)
    {
        if (mouth != null) mouth.SetMouthTest(value);
    }

    public void SetMouthOpen(string value)
    {
        if (mouth != null) mouth.SetMouthOpen(value);
    }

    public void SetMouthVisemeJson(string json)
    {
        if (mouth != null) mouth.SetMouthVisemeJson(json);
    }

    [ContextMenu("Nana/Look Left")]
    private void ContextLookLeft()
    {
        LookLeft();
    }

    [ContextMenu("Nana/Look Right")]
    private void ContextLookRight()
    {
        LookRight();
    }

    [ContextMenu("Nana/Look Center")]
    private void ContextLookCenter()
    {
        LookCenter();
    }

    private void DiscoverRig()
    {
        Transform scope = transform;
        while (scope != null && animator == null)
        {
            animator = scope.GetComponent<Animator>();
            scope = scope.parent;
        }
        if (animator == null)
        {
            Transform root = transform.root;
            animator = root.GetComponentInChildren<Animator>(true);
        }
        if (animator == null)
            animator = FindObjectOfType<Animator>();

        Transform avatarScope = animator != null ? animator.transform.root : transform.root;

        if (animator != null && animator.isHuman)
        {
            if (neck == null)
                neck = animator.GetBoneTransform(HumanBodyBones.Neck);
            if (head == null)
                head = animator.GetBoneTransform(HumanBodyBones.Head);
        }
        if (head == null)
            head = FindNamedChild(avatarScope, "Head");
        if (neck == null)
            neck = FindNamedChild(avatarScope, "Neck");
        if (face == null)
            face = FindFaceRenderer(avatarScope);

        _rigReady = head != null && face != null;
    }

    private void CaptureBasePose()
    {
        if (!_rigReady)
            return;
        _baseHeadRotation = head.localRotation;
        _baseNeckRotation = neck != null ? neck.localRotation : Quaternion.identity;
        for (int i = 0; i < EyeShapeNames.Length; i++)
        {
            _eyeIndices[i] = face != null && face.sharedMesh != null
                ? face.sharedMesh.GetBlendShapeIndex(EyeShapeNames[i])
                : -1;
            _faceBaseWeights[i] = _eyeIndices[i] >= 0
                ? face.GetBlendShapeWeight(_eyeIndices[i])
                : 0f;
        }
        _currentOffset = Vector3.zero;
        _motionStart = Vector3.zero;
        _motionTarget = Vector3.zero;
        _idlePauseRemaining = UnityEngine.Random.Range(idlePauseMin, idlePauseMax);
        _basePoseCaptured = true;
        if (_pendingLook)
        {
            BeginLook(
                _pendingTarget,
                _pendingMoveSeconds,
                _pendingHoldSeconds,
                _pendingReturnSeconds,
                _pendingReturnToNeutral);
        }
    }

    private IEnumerator InitializeAfterAnimatorFrame()
    {
        yield return null;
        DiscoverRig();
        if (!_rigReady)
            yield break;
        CaptureBasePose();
        if (gatewayPollingEnabled)
            _gatewayPollRoutine = StartCoroutine(PollGatewayLoop());
        if (runRetargetDemoOnPlay)
            StartCoroutine(RetargetDemo());
    }

    private void RestoreBasePose()
    {
        if (!_rigReady)
            return;
        if (neck != null)
            neck.localRotation = _baseNeckRotation;
        if (head != null)
            head.localRotation = _baseHeadRotation;
        ApplyFaceWeight(0, 0f);
        ApplyFaceWeight(1, 0f);
        ApplyFaceWeight(2, 0f);
        ApplyFaceWeight(3, 0f);
        ApplyFaceWeight(4, 0f);
        ApplyFaceWeight(5, 0f);
        ApplyFaceWeight(6, 0f);
    }

    private void UpdateMotion(float delta)
    {
        if (delta <= 0f)
            return;

        _phaseElapsed += delta;
        switch (_phase)
        {
            case MotionPhase.IdlePause:
                _currentOffset = Vector3.zero;
                _idlePauseRemaining -= delta;
                if (idleMotionEnabled && _idlePauseRemaining <= 0f)
                    BeginIdleMove();
                break;

            case MotionPhase.Moving:
                _currentOffset = Vector3.Lerp(
                    _motionStart,
                    _motionTarget,
                    EaseInOut(_phaseElapsed / _moveSeconds));
                if (_phaseElapsed >= _moveSeconds)
                {
                    _currentOffset = _motionTarget;
                    _phaseElapsed = 0f;
                    _phase = _holdSeconds > 0f ? MotionPhase.Holding : NextAfterHold();
                }
                break;

            case MotionPhase.Holding:
                _currentOffset = _motionTarget;
                if (_phaseElapsed >= _holdSeconds)
                {
                    _phaseElapsed = 0f;
                    _phase = NextAfterHold();
                }
                break;

            case MotionPhase.Returning:
                _currentOffset = Vector3.Lerp(
                    _motionStart,
                    Vector3.zero,
                    EaseInOut(_phaseElapsed / _returnSeconds));
                if (_phaseElapsed >= _returnSeconds)
                {
                    _currentOffset = Vector3.zero;
                    _phaseElapsed = 0f;
                    _idlePauseRemaining = UnityEngine.Random.Range(idlePauseMin, idlePauseMax);
                    _phase = MotionPhase.IdlePause;
                }
                break;

            case MotionPhase.Held:
                _currentOffset = _motionTarget;
                break;
        }
    }

    private MotionPhase NextAfterHold()
    {
        if (_returnToNeutral)
        {
            _motionStart = _currentOffset;
            return MotionPhase.Returning;
        }
        return MotionPhase.Held;
    }

    private void BeginIdleMove()
    {
        _motionStart = _currentOffset;
        _motionTarget = new Vector3(
            UnityEngine.Random.Range(-idlePitch, idlePitch),
            UnityEngine.Random.Range(-idleYaw, idleYaw),
            UnityEngine.Random.Range(-idleRoll, idleRoll));
        _moveSeconds = UnityEngine.Random.Range(idleMoveMin, idleMoveMax);
        _holdSeconds = UnityEngine.Random.Range(idleHoldMin, idleHoldMax);
        _returnSeconds = UnityEngine.Random.Range(idleReturnMin, idleReturnMax);
        _returnToNeutral = true;
        _phaseElapsed = 0f;
        _phase = MotionPhase.Moving;
    }

    private void ApplyHeadOffset()
    {
        Vector3 offset = _currentOffset;
        Quaternion headOffset = Quaternion.Euler(
            offset.x * (1f - neckShare),
            offset.y * (1f - neckShare),
            offset.z * (1f - neckShare));
        if (neck != null)
        {
            Quaternion neckOffset = Quaternion.Euler(
                offset.x * neckShare,
                offset.y * neckShare,
                offset.z * neckShare);
            neck.localRotation = _baseNeckRotation * neckOffset;
        }
        head.localRotation = _baseHeadRotation * headOffset;
    }

    private void UpdateEyes(float delta)
    {
        if (face == null || delta <= 0f)
            return;

        Vector3 source = _currentOffset;
        if (!_eventHeadOwned && _phase == MotionPhase.Moving)
            source = _motionTarget;

        float desiredYaw = Mathf.Clamp(source.y * eyeLeadMultiplier, -eyeYawLimit, eyeYawLimit);
        float desiredPitch = Mathf.Clamp(source.x * eyeLeadMultiplier, -eyePitchLimit, eyePitchLimit);
        _eyeCurrent.x = Mathf.SmoothDamp(
            _eyeCurrent.x,
            desiredPitch,
            ref _eyeVelocity.x,
            Mathf.Max(0.04f, eyeSmoothTime),
            Mathf.Infinity,
            delta);
        _eyeCurrent.y = Mathf.SmoothDamp(
            _eyeCurrent.y,
            desiredYaw,
            ref _eyeVelocity.y,
            Mathf.Max(0.04f, eyeSmoothTime),
            Mathf.Infinity,
            delta);

        float left = Mathf.Clamp01(-_eyeCurrent.y / Mathf.Max(0.1f, eyeYawLimit)) * eyeWeightScale;
        float right = Mathf.Clamp01(_eyeCurrent.y / Mathf.Max(0.1f, eyeYawLimit)) * eyeWeightScale;
        float up = Mathf.Clamp01(-_eyeCurrent.x / Mathf.Max(0.1f, eyePitchLimit)) * eyeWeightScale;
        float down = Mathf.Clamp01(_eyeCurrent.x / Mathf.Max(0.1f, eyePitchLimit)) * eyeWeightScale;
        ApplyFaceWeight(3, left);
        ApplyFaceWeight(4, right);
        ApplyFaceWeight(5, up);
        ApplyFaceWeight(6, down);
    }

    private void UpdateBlink(float delta)
    {
        if (!blinkEnabled || face == null || delta <= 0f)
        {
            _blinkActive = false;
            ApplyFaceWeight(0, 0f);
            ApplyFaceWeight(1, 0f);
            ApplyFaceWeight(2, 0f);
            return;
        }

        float now = Time.unscaledTime;
        if (!_blinkActive && now >= _nextBlinkAt)
        {
            _blinkActive = true;
            _blinkElapsed = 0f;
        }

        float weight = 0f;
        if (_blinkActive)
        {
            _blinkElapsed += delta;
            if (_blinkElapsed <= blinkCloseSeconds)
            {
                weight = Mathf.Clamp01(_blinkElapsed / blinkCloseSeconds) * 100f;
            }
            else if (_blinkElapsed <= blinkCloseSeconds + blinkOpenSeconds)
            {
                float t = (_blinkElapsed - blinkCloseSeconds) / blinkOpenSeconds;
                weight = (1f - Mathf.Clamp01(t)) * 100f;
            }
            else
            {
                _blinkActive = false;
                ScheduleNextBlink();
            }
        }
        ApplyFaceWeight(0, weight);
        ApplyFaceWeight(1, weight);
        ApplyFaceWeight(2, weight);
    }

    private void ScheduleNextBlink()
    {
        _nextBlinkAt = Time.unscaledTime + UnityEngine.Random.Range(blinkIntervalMin, blinkIntervalMax);
    }

    private void ApplyFaceWeight(int slot, float addedWeight)
    {
        if (face == null || slot < 0 || slot >= _eyeIndices.Length || _eyeIndices[slot] < 0)
            return;
        float baseWeight = _faceBaseWeights[slot];
        face.SetBlendShapeWeight(_eyeIndices[slot], Mathf.Clamp(baseWeight + addedWeight, 0f, 100f));
    }

    private IEnumerator PollGatewayLoop()
    {
        while (isActiveAndEnabled && gatewayPollingEnabled)
        {
            string baseUrl = ResolveGatewayBaseUrl();
            if (baseUrl.Length > 0)
            {
                using (UnityWebRequest request = UnityWebRequest.Get(
                    baseUrl + "/v1/avatar/commands?after=" + _gatewayCursor))
                {
                    request.timeout = 1;
                    if (!string.IsNullOrEmpty(gatewayToken))
                        request.SetRequestHeader("Authorization", "Bearer " + gatewayToken);
                    yield return request.SendWebRequest();
                    if (request.result == UnityWebRequest.Result.Success)
                    {
                        ConsumeGatewayResponse(request.downloadHandler.text);
                    }
                    else
                    {
                        if (Time.unscaledTime - _lastGatewayWarningAt > 5f)
                        {
                            _lastGatewayWarningAt = Time.unscaledTime;
                            Debug.LogWarning("Nana avatar gateway poll unavailable: " + request.error, this);
                        }
                        gatewayPollingEnabled = false;
                    }
                }
            }
            yield return new WaitForSecondsRealtime(Mathf.Max(0.05f, gatewayPollInterval));
        }
        _gatewayPollRoutine = null;
    }

    private void ConsumeGatewayResponse(string json)
    {
        if (string.IsNullOrEmpty(json))
            return;
        PollResponse response;
        try
        {
            response = JsonUtility.FromJson<PollResponse>(json);
        }
        catch (Exception exception)
        {
            Debug.LogWarning("Nana avatar gateway response invalid: " + exception.GetType().Name, this);
            return;
        }
        if (response == null || !response.ok)
            return;
        if (!string.IsNullOrEmpty(response.session_id))
        {
            bool restarted = !string.IsNullOrEmpty(_gatewaySession) && _gatewaySession != response.session_id;
            _gatewaySession = response.session_id;
            if (restarted)
            {
                _gatewayCursor = 0;
                return; // Repoll the new session from zero, even if its cursor matches the old one.
            }
        }
        if (response.commands != null)
        {
            foreach (CommandEnvelope command in response.commands)
            {
                if (command == null || command.protocol != ProtocolName || command.intent == null)
                    continue;
                if (command.intent.action != "look")
                {
                    if (events != null) events.Receive(new NanaAvatarEventController.Intent {
                        action = command.intent.action, intent_id = command.intent.intent_id,
                        duration_s = command.intent.duration_s
                    }, true);
                    continue;
                }
                if (command.intent.target == null) continue;
                if (events != null) events.InterruptHead();
                if (command.intent.return_to == "hold")
                {
                    RequestLook(
                        new LookTarget(
                            command.intent.target.yaw,
                            command.intent.target.pitch,
                            command.intent.target.roll),
                        command.intent.move_s,
                        command.intent.hold_s,
                        Mathf.Max(MinimumPhaseSeconds, command.intent.return_s),
                        false);
                }
                else
                {
                    RequestLook(
                        new LookTarget(
                            command.intent.target.yaw,
                            command.intent.target.pitch,
                            command.intent.target.roll),
                        command.intent.move_s,
                        command.intent.hold_s,
                        command.intent.return_s,
                        true);
                }
            }
        }
        _gatewayCursor = Mathf.Max(_gatewayCursor, response.cursor);
    }

    public string ResolveGatewayBaseUrl()
    {
        string configured = (gatewayUrl ?? string.Empty).Trim().TrimEnd('/');
        if (!configured.StartsWith("/", StringComparison.Ordinal))
            return configured;
        if (string.IsNullOrWhiteSpace(Application.absoluteURL))
            return configured;
        try
        {
            Uri page = new Uri(Application.absoluteURL);
            return page.GetLeftPart(UriPartial.Authority) + configured;
        }
        catch (UriFormatException)
        {
            return configured;
        }
    }

    private IEnumerator RetargetDemo()
    {
        yield return new WaitForSecondsRealtime(1f);
        RequestLook(new LookTarget(-24f, -3f, 2f), 0.8f, 0.7f, 0.8f, true);
        yield return new WaitForSecondsRealtime(0.35f);
        RequestLook(new LookTarget(24f, -2f, -2f), 0.55f, 0.6f, 0.7f, true);
        yield return new WaitForSecondsRealtime(1.1f);
        RequestLook(new LookTarget(0f, 0f, 0f), 0.55f, 0.25f, 0.65f, true);
    }

    private LookTarget ClampTarget(LookTarget target)
    {
        return new LookTarget(
            Mathf.Clamp(target.yaw, -maxYaw, maxYaw),
            Mathf.Clamp(target.pitch, -maxPitch, maxPitch),
            Mathf.Clamp(target.roll, -maxRoll, maxRoll));
    }

    private static float EaseInOut(float value)
    {
        float t = Mathf.Clamp01(value);
        return t * t * (3f - 2f * t);
    }

    private static bool ParseToggle(string value, bool fallback)
    {
        string normalized = (value ?? string.Empty).Trim().ToLowerInvariant();
        if (normalized == "1" || normalized == "true" || normalized == "on")
            return true;
        if (normalized == "0" || normalized == "false" || normalized == "off")
            return false;
        return fallback;
    }

    private static float ParseBoundedFloat(string value, float fallback, float minimum, float maximum)
    {
        if (!float.TryParse(value, NumberStyles.Float, CultureInfo.InvariantCulture, out float parsed))
            return fallback;
        return Mathf.Clamp(parsed, minimum, maximum);
    }

    private static Transform FindNamedChild(Transform root, string name)
    {
        if (root == null)
            return null;
        Transform[] all = root.GetComponentsInChildren<Transform>(true);
        foreach (Transform candidate in all)
        {
            if (candidate.name.Equals(name, StringComparison.OrdinalIgnoreCase))
                return candidate;
        }
        return null;
    }

    private static SkinnedMeshRenderer FindFaceRenderer(Transform root)
    {
        if (root == null)
            return null;
        SkinnedMeshRenderer fallback = null;
        SkinnedMeshRenderer[] renderers = root.GetComponentsInChildren<SkinnedMeshRenderer>(true);
        foreach (SkinnedMeshRenderer candidate in renderers)
        {
            if (candidate.sharedMesh == null || candidate.sharedMesh.GetBlendShapeIndex("eye_look_left") < 0)
                continue;
            if (candidate.name.Equals("Body", StringComparison.OrdinalIgnoreCase))
                return candidate;
            fallback = fallback ?? candidate;
        }
        return fallback;
    }
}
