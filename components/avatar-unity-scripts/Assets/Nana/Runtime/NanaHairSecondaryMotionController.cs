using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Runtime.InteropServices;
using UnityEngine;
using UnityEngine.Serialization;
using VRC.Dynamics;
using VRC.SDK3.Dynamics.PhysBone.Components;

// Candidate owner around the bundled VRChat PhysBone solver.
[DefaultExecutionOrder(1600)]
public sealed class NanaHairSecondaryMotionController : MonoBehaviour
{
    [Serializable]
    public sealed class ModelBindings
    {
        public int model;
        public Transform head;
        public VRCPhysBone[] physBones;
        public VRCPhysBoneCollider[] colliders;
        public Transform[] windRoots;
        public Transform[] representativeTips;
        public string[] representativeIds;
        [FormerlySerializedAs("simulatedBoneCount")]
        public int structuralTransformUnionCount;
        public int expectedSolverBoneCount;
        public string[] structuralOnlyTransforms;
        public int variantOverlap;
    }

    [Serializable]
    private sealed class TipTelemetry
    {
        public string id;
        public Vector3 headLocal;
        public float displacement, speed, angle, minClearance;
    }

    [Serializable]
    private sealed class State
    {
        public string revision, solver, resetPhase, lastResetReason;
        public bool ready, secondaryMotion, collision, wind, settled, managerReady;
        public int selectedModel, physBoneCount, colliderCount, structuralTransformUnionCount, variantOverlap;
        public int registeredPhysBoneCount, expectedSolverBoneCount, initializedSolverBoneCount;
        public int windChangeCount, pauseResets, nanResets, collisionRecordCount;
        public int model1EnabledCount, model2EnabledCount, model1RegisteredCount, model2RegisteredCount;
        public int resetGeneration, resetRemovalBarrierCount, lastResetRemovalFrame, lastResetAddFrame;
        public int lastResetBarrierModel1Registered, lastResetBarrierModel2Registered;
        public float frameMs, controllerMs, lag, windStrength, clearanceScale;
        public float lagPullScale, lagSpringScale, maxAuthoredPullError, maxAuthoredSpringError;
        public float maxTipDisplacement, maxTipSpeed, maxPhysBoneAngle, minClearance;
        public float windBias, windTarget, windBlendSeconds, windLastInterval, windNextIn;
        public float maxAppliedWindAngle;
        public long managedBytes;
        public TipTelemetry[] tips;
    }

    private sealed class ModelCache
    {
        public ModelBindings bindings;
        public float[] pull, spring, colliderRadius, colliderHeight;
        public List<VRCPhysBoneColliderBase>[] colliderAssociations;
        public Quaternion[] windRest;
        public float[] windPhase, windResponse;
        public Vector3[] tipRest, tipPrevious;
        public TipTelemetry[] tipTelemetry;
    }

    private enum ResetPhase
    {
        None,
        Remove,
        Add
    }

    public NanaModelVariantController variants;
    public ModelBindings model1;
    public ModelBindings model2;
    public bool secondaryMotionEnabled = true;
    public bool collisionEnabled = true;
    public bool windEnabled = true;
    [Range(0f, 1f)] public float lag = .5f;
    [Range(0f, 1f)] public float windStrength = .15f;
    [Range(.8f, 1.2f)] public float collisionClearanceScale = 1f;

    private const string Revision = "hair_secondary_motion_v1.0.1.0-r2";
    private const float WindBlendSeconds = 14f;
    private const float WindIntervalMin = 60f;
    private const float WindIntervalMax = 120f;
    private const float PauseDelta = .10f;

    private readonly System.Random _windRandom = new System.Random(731042);
    private ModelCache[] _models;
    private int _selectedModel;
    private float _windBias, _windTarget, _windVelocity, _windNextAt, _windLastInterval;
    private int _windChangeCount, _pauseResets, _nanResets;
    private ResetPhase _resetPhase;
    private string _lastResetReason = "initialization";
    private int _resetGeneration, _resetRemovalBarrierCount;
    private int _lastResetRemovalFrame = -1, _lastResetAddFrame = -1;
    private int _lastResetBarrierModel1Registered = -1, _lastResetBarrierModel2Registered = -1;
    private bool _awakeComplete;
    private bool _frameGapArmed = true;
    private float _lagPullScale = 1f, _lagSpringScale = 1f;
    private float _maxAppliedWindAngle;
    private float _nextPublish, _controllerMs, _frameMs;

#if UNITY_WEBGL && !UNITY_EDITOR
    [DllImport("__Internal")]
    private static extern void NanaHairSecondaryState(string json);
#endif

    private void Awake()
    {
        EnsureRuntimeManagers();
        if (variants == null) variants = FindObjectOfType<NanaModelVariantController>();
        if (!Valid(model1) || !Valid(model2) || variants == null)
        {
            UnityEngine.Debug.LogError("NANA_HAIR_INIT missing serialized PhysBone candidate bindings", this);
            enabled = false;
            return;
        }
        _models = new[] { Cache(model1), Cache(model2) };
        EnsureCandidateCollidersEnabled();
        _selectedModel = variants.selectedModel == 2 ? 2 : 1;
        _windTarget = NextWindTarget(0f);
        _windLastInterval = NextWindInterval();
        _windNextAt = Time.unscaledTime + _windLastInterval;
        ApplyLag();
        ApplyClearance();
        ApplyCollisionAssociations();
        BeginHardReset("initialization");
        _awakeComplete = true;
        UnityEngine.Debug.Log(string.Format(
            "NANA_HAIR_READY revision={0} solver=VRCPhysBone model1_pb={1} model1_colliders={2} " +
            "model2_pb={3} model2_colliders={4} overlap={5}",
            Revision, model1.physBones.Length, model1.colliders.Length,
            model2.physBones.Length, model2.colliders.Length,
            model1.variantOverlap + model2.variantOverlap), this);
    }

    private void OnEnable()
    {
        if (!_awakeComplete || _models == null) return;
        EnsureRuntimeManagers();
        EnsureCandidateCollidersEnabled();
        ApplyClearance();
        ApplyCollisionAssociations();
        if (_resetPhase == ResetPhase.None) BeginHardReset("lifecycle_enable");
        else _lastResetReason = "lifecycle_enable";
    }

    private static void EnsureRuntimeManagers()
    {
        // VRChat ships this bootstrap in an Editor assembly, so standalone WebGL players
        // need the equivalent runtime setup before disabled scene PhysBones are enabled.
        if (ContactManager.Inst == null)
        {
            GameObject contacts = new GameObject("NanaHairContactManager");
            DontDestroyOnLoad(contacts);
            ContactManager.Inst = contacts.AddComponent<ContactManager>();
            contacts.hideFlags = HideFlags.HideInHierarchy;
        }
        if (PhysBoneManager.Inst == null)
        {
            GameObject physBones = new GameObject("NanaHairPhysBoneManager");
            DontDestroyOnLoad(physBones);
            PhysBoneManager.Inst = physBones.AddComponent<PhysBoneManager>();
            PhysBoneManager.Inst.IsSDK = true;
            PhysBoneManager.Inst.Init();
            physBones.hideFlags = HideFlags.HideInHierarchy;
        }
    }

    private void LateUpdate()
    {
        long started = Stopwatch.GetTimestamp();
        float delta = Mathf.Max(0f, Time.unscaledDeltaTime);
        _frameMs = delta * 1000f;
        if (_models == null) return;

        int selected = variants.selectedModel == 2 ? 2 : 1;
        if (selected != _selectedModel)
        {
            _selectedModel = selected;
            BeginHardReset("model_switch");
        }
        if (delta <= PauseDelta) _frameGapArmed = true;
        if (delta > PauseDelta && _frameGapArmed && _resetPhase == ResetPhase.None)
        {
            _pauseResets++;
            _frameGapArmed = false;
            BeginHardReset("frame_gap");
        }

        if (_resetPhase == ResetPhase.None)
        {
            UpdateWind(delta);
            ApplyWind();
        }
        else
        {
            RestoreWindRoots();
            if (_resetPhase == ResetPhase.Remove) SetAllPhysBonesEnabled(false);
        }
        // The VRChat client normally drives this from SystemsPlayerLoop. Standalone
        // WebGL has no such host, so run the SDK's synchronous public pipeline here.
        VRCDynamicsScheduler.UpdateConstraints(true);
        AdvanceHardResetAfterSchedule();
        UpdateTips(delta);
        _controllerMs = (float)((Stopwatch.GetTimestamp() - started) * 1000.0 / Stopwatch.Frequency);
        if (Time.unscaledTime >= _nextPublish)
        {
            _nextPublish = Time.unscaledTime + .2f;
            Publish();
        }
    }

    private static bool Valid(ModelBindings bindings)
    {
        return bindings != null && bindings.head != null && bindings.physBones != null &&
            bindings.physBones.Length > 0 && bindings.colliders != null &&
            bindings.windRoots != null && bindings.representativeTips != null;
    }

    private static ModelCache Cache(ModelBindings bindings)
    {
        var cache = new ModelCache {
            bindings = bindings,
            pull = new float[bindings.physBones.Length],
            spring = new float[bindings.physBones.Length],
            colliderAssociations = new List<VRCPhysBoneColliderBase>[bindings.physBones.Length],
            colliderRadius = new float[bindings.colliders.Length],
            colliderHeight = new float[bindings.colliders.Length],
            windRest = new Quaternion[bindings.windRoots.Length],
            windPhase = new float[bindings.windRoots.Length],
            windResponse = new float[bindings.windRoots.Length],
            tipRest = new Vector3[bindings.representativeTips.Length],
            tipPrevious = new Vector3[bindings.representativeTips.Length],
            tipTelemetry = new TipTelemetry[bindings.representativeTips.Length]
        };
        for (int index = 0; index < bindings.physBones.Length; index++)
        {
            cache.pull[index] = bindings.physBones[index].pull;
            cache.spring[index] = bindings.physBones[index].spring;
            cache.colliderAssociations[index] = bindings.physBones[index].colliders != null
                ? new List<VRCPhysBoneColliderBase>(bindings.physBones[index].colliders)
                : new List<VRCPhysBoneColliderBase>();
        }
        for (int index = 0; index < bindings.colliders.Length; index++)
        {
            cache.colliderRadius[index] = bindings.colliders[index].radius;
            cache.colliderHeight[index] = bindings.colliders[index].height;
        }
        for (int index = 0; index < bindings.windRoots.Length; index++)
        {
            cache.windRest[index] = bindings.windRoots[index].localRotation;
            string name = bindings.windRoots[index].name;
            cache.windPhase[index] = Hash01(name) * 17f;
            cache.windResponse[index] = .82f + Hash01(name + ":wind") * .28f;
        }
        for (int index = 0; index < bindings.representativeTips.Length; index++)
        {
            Vector3 position = bindings.head.InverseTransformPoint(bindings.representativeTips[index].position);
            cache.tipRest[index] = position;
            cache.tipPrevious[index] = position;
            cache.tipTelemetry[index] = new TipTelemetry {
                id = bindings.representativeIds != null && index < bindings.representativeIds.Length
                    ? bindings.representativeIds[index] : bindings.representativeTips[index].name
            };
        }
        return cache;
    }

    private void BeginHardReset(string reason)
    {
        if (_models == null) return;
        if (_resetPhase == ResetPhase.Remove)
        {
            _lastResetReason = reason;
            return;
        }
        if (_resetPhase == ResetPhase.Add)
        {
            RestoreWindRoots();
            SetAllPhysBonesEnabled(false);
            _lastResetReason = reason;
            _resetPhase = ResetPhase.Remove;
            return;
        }
        RestoreWindRoots();
        SetAllPhysBonesEnabled(false);
        _lastResetReason = reason;
        _resetPhase = ResetPhase.Remove;
    }

    private void EnsureCandidateCollidersEnabled()
    {
        if (_models == null) return;
        for (int model = 0; model < _models.Length; model++)
            for (int index = 0; index < _models[model].bindings.colliders.Length; index++)
            {
                VRCPhysBoneCollider collider = _models[model].bindings.colliders[index];
                if (collider == null || collider.enabled) continue;
                collider.enabled = true;
                collider.ApplyConfigurationChanges();
            }
    }

    private void AdvanceHardResetAfterSchedule()
    {
        if (_resetPhase == ResetPhase.Remove)
        {
            _resetRemovalBarrierCount++;
            _lastResetRemovalFrame = Time.frameCount;
            PhysBoneManager manager = PhysBoneManager.Inst;
            _lastResetBarrierModel1Registered = CountRegistered(model1, manager);
            _lastResetBarrierModel2Registered = CountRegistered(model2, manager);
            SetDesiredPhysBonesEnabled();
            ResetTipHistory();
            _resetPhase = ResetPhase.Add;
            return;
        }
        if (_resetPhase != ResetPhase.Add || !DesiredRegistrationStateReached()) return;
        _lastResetAddFrame = Time.frameCount;
        _resetGeneration++;
        _resetPhase = ResetPhase.None;
        ResetTipHistory();
    }

    private void SetAllPhysBonesEnabled(bool value)
    {
        for (int model = 0; model < _models.Length; model++)
            for (int index = 0; index < _models[model].bindings.physBones.Length; index++)
                _models[model].bindings.physBones[index].enabled = value;
    }

    private void SetDesiredPhysBonesEnabled()
    {
        for (int model = 0; model < _models.Length; model++)
        {
            ModelCache cache = _models[model];
            bool active = secondaryMotionEnabled && cache.bindings.model == _selectedModel;
            for (int index = 0; index < cache.bindings.physBones.Length; index++)
            {
                VRCPhysBone physBone = cache.bindings.physBones[index];
                physBone.enabled = active;
                if (!active) continue;
                physBone.ApplyConfigurationChanges();
                physBone.OnCollidersUpdated();
            }
        }
    }

    private bool DesiredRegistrationStateReached()
    {
        PhysBoneManager manager = PhysBoneManager.Inst;
        if (manager == null) return false;
        for (int model = 0; model < _models.Length; model++)
        {
            ModelCache cache = _models[model];
            bool desired = secondaryMotionEnabled && cache.bindings.model == _selectedModel;
            for (int index = 0; index < cache.bindings.physBones.Length; index++)
                if (manager.HasPhysBone(cache.bindings.physBones[index]) != desired) return false;
        }
        return true;
    }

    private void ResetTipHistory()
    {
        for (int model = 0; model < _models.Length; model++)
        {
            ModelCache cache = _models[model];
            for (int index = 0; index < cache.tipPrevious.Length; index++)
                cache.tipPrevious[index] = cache.bindings.head.InverseTransformPoint(
                    cache.bindings.representativeTips[index].position);
        }
    }

    private void ApplyLag()
    {
        float low = Mathf.Clamp01(lag * 2f);
        float high = Mathf.Clamp01((lag - .5f) * 2f);
        float pullScale = lag <= .5f ? Mathf.Lerp(1.15f, 1f, low) : Mathf.Lerp(1f, .78f, high);
        float springScale = lag <= .5f ? Mathf.Lerp(.86f, 1f, low) : Mathf.Lerp(1f, 1.12f, high);
        _lagPullScale = pullScale;
        _lagSpringScale = springScale;
        for (int model = 0; model < _models.Length; model++)
            for (int index = 0; index < _models[model].bindings.physBones.Length; index++)
            {
                VRCPhysBone physBone = _models[model].bindings.physBones[index];
                physBone.pull = Mathf.Clamp01(_models[model].pull[index] * pullScale);
                physBone.spring = Mathf.Clamp01(_models[model].spring[index] * springScale);
                physBone.configHasUpdated = true;
                if (physBone.enabled) physBone.ApplyConfigurationChanges();
            }
    }

    private void ApplyClearance()
    {
        float scale = collisionClearanceScale;
        for (int model = 0; model < _models.Length; model++)
            for (int index = 0; index < _models[model].bindings.colliders.Length; index++)
            {
                VRCPhysBoneCollider collider = _models[model].bindings.colliders[index];
                collider.radius = Mathf.Max(.0001f, _models[model].colliderRadius[index] * scale);
                collider.height = Mathf.Max(collider.radius * 2f,
                    _models[model].colliderHeight[index] * scale);
                if (collider.enabled) collider.ApplyConfigurationChanges();
            }
    }

    private void ApplyCollisionAssociations()
    {
        if (_models == null) return;
        for (int model = 0; model < _models.Length; model++)
        {
            ModelCache cache = _models[model];
            for (int index = 0; index < cache.bindings.physBones.Length; index++)
            {
                VRCPhysBone physBone = cache.bindings.physBones[index];
                physBone.colliders = collisionEnabled
                    ? new List<VRCPhysBoneColliderBase>(cache.colliderAssociations[index])
                    : new List<VRCPhysBoneColliderBase>();
                physBone.OnCollidersUpdated();
            }
        }
    }

    private void UpdateWind(float delta)
    {
        if (Time.unscaledTime >= _windNextAt)
        {
            _windTarget = NextWindTarget(_windTarget);
            _windChangeCount++;
            _windLastInterval = NextWindInterval();
            _windNextAt = Time.unscaledTime + _windLastInterval;
        }
        _windBias = Mathf.SmoothDamp(_windBias, _windTarget, ref _windVelocity,
            WindBlendSeconds, .12f, delta);
    }

    private void ApplyWind()
    {
        RestoreWindRoots();
        _maxAppliedWindAngle = 0f;
        if (!secondaryMotionEnabled || !windEnabled) return;
        ModelCache cache = _models[_selectedModel - 1];
        float time = Time.unscaledTime;
        for (int index = 0; index < cache.bindings.windRoots.Length; index++)
        {
            float gust = (Mathf.PerlinNoise(cache.windPhase[index], time * .045f) - .5f) * .30f;
            float angle = (_windBias + gust) * windStrength * 1.5f * cache.windResponse[index];
            _maxAppliedWindAngle = Mathf.Max(_maxAppliedWindAngle, Mathf.Abs(angle));
            cache.bindings.windRoots[index].localRotation = cache.windRest[index] *
                Quaternion.AngleAxis(angle, Vector3.forward);
        }
    }

    private void RestoreWindRoots()
    {
        if (_models == null) return;
        for (int model = 0; model < _models.Length; model++)
            for (int index = 0; index < _models[model].bindings.windRoots.Length; index++)
                _models[model].bindings.windRoots[index].localRotation = _models[model].windRest[index];
    }

    private void UpdateTips(float delta)
    {
        ModelCache cache = _models[_selectedModel - 1];
        for (int index = 0; index < cache.bindings.representativeTips.Length; index++)
        {
            Transform tip = cache.bindings.representativeTips[index];
            Vector3 current = cache.bindings.head.InverseTransformPoint(tip.position);
            TipTelemetry telemetry = cache.tipTelemetry[index];
            telemetry.headLocal = current;
            telemetry.displacement = Vector3.Distance(current, cache.tipRest[index]);
            telemetry.speed = delta > .0001f ? Vector3.Distance(current, cache.tipPrevious[index]) / delta : 0f;
            telemetry.minClearance = collisionEnabled
                ? MinimumClearance(tip.position, cache.bindings.colliders) : 0f;
            // VRCPhysBone.Angle is an interaction parameter, not the observed bend.
            telemetry.angle = Vector3.Angle(cache.tipRest[index], current);
            if (!Finite(current) || !Finite(telemetry.speed))
            {
                telemetry.displacement = 0f;
                telemetry.speed = 0f;
                _nanResets++;
                BeginHardReset("non_finite");
            }
            cache.tipPrevious[index] = current;
        }
    }

    private static float MinimumClearance(Vector3 point, VRCPhysBoneCollider[] colliders)
    {
        float minimum = float.PositiveInfinity;
        for (int index = 0; index < colliders.Length; index++)
        {
            VRCPhysBoneCollider collider = colliders[index];
            if (!collider.enabled) continue;
            Transform root = collider.GetRootTransform();
            if (root == null) continue;
            Vector3 center = root.TransformPoint(collider.position);
            float distance;
            if (collider.shapeType == VRCPhysBoneColliderBase.ShapeType.Capsule)
            {
                Vector3 axis = root.rotation * collider.rotation * Vector3.up;
                float halfLine = Mathf.Max(0f, collider.height * .5f - collider.radius);
                Vector3 a = center - axis * halfLine;
                Vector3 b = center + axis * halfLine;
                distance = Vector3.Distance(point, ClosestPoint(a, b, point)) - collider.radius;
            }
            else
            {
                distance = Vector3.Distance(point, center) - collider.radius;
            }
            minimum = Mathf.Min(minimum, distance);
        }
        return float.IsPositiveInfinity(minimum) ? 0f : minimum;
    }

    private void Publish()
    {
        ModelCache cache = _models[_selectedModel - 1];
        float maxDisplacement = 0f;
        float maxSpeed = 0f;
        float maxAngle = 0f;
        float minClearance = float.PositiveInfinity;
        for (int index = 0; index < cache.tipTelemetry.Length; index++)
        {
            TipTelemetry tip = cache.tipTelemetry[index];
            maxDisplacement = Mathf.Max(maxDisplacement, tip.displacement);
            maxSpeed = Mathf.Max(maxSpeed, tip.speed);
            maxAngle = Mathf.Max(maxAngle, tip.angle);
            minClearance = Mathf.Min(minClearance, tip.minClearance);
        }
        int collisionRecords = 0;
        int registeredPhysBones = 0;
        int initializedBones = 0;
        PhysBoneManager manager = PhysBoneManager.Inst;
        float maxPullError = 0f;
        float maxSpringError = 0f;
        for (int index = 0; index < cache.bindings.physBones.Length; index++)
        {
            if (manager != null && manager.HasPhysBone(cache.bindings.physBones[index])) registeredPhysBones++;
            if (cache.bindings.physBones[index].bones != null)
                initializedBones += cache.bindings.physBones[index].bones.Count;
            if (cache.bindings.physBones[index].collisionRecords != null)
                collisionRecords += cache.bindings.physBones[index].collisionRecords.Count;
            maxPullError = Mathf.Max(maxPullError,
                Mathf.Abs(cache.bindings.physBones[index].pull - cache.pull[index]));
            maxSpringError = Mathf.Max(maxSpringError,
                Mathf.Abs(cache.bindings.physBones[index].spring - cache.spring[index]));
        }
        State state = new State {
            revision = Revision,
            solver = "VRCPhysBone",
            resetPhase = _resetPhase.ToString().ToLowerInvariant(),
            lastResetReason = _lastResetReason,
            ready = true,
            secondaryMotion = secondaryMotionEnabled,
            collision = collisionEnabled,
            wind = windEnabled,
            settled = maxSpeed < .004f,
            managerReady = manager != null,
            selectedModel = _selectedModel,
            physBoneCount = cache.bindings.physBones.Length,
            colliderCount = cache.bindings.colliders.Length,
            structuralTransformUnionCount = cache.bindings.structuralTransformUnionCount,
            variantOverlap = cache.bindings.variantOverlap,
            registeredPhysBoneCount = registeredPhysBones,
            expectedSolverBoneCount = cache.bindings.expectedSolverBoneCount,
            initializedSolverBoneCount = initializedBones,
            windChangeCount = _windChangeCount,
            pauseResets = _pauseResets,
            nanResets = _nanResets,
            collisionRecordCount = collisionRecords,
            model1EnabledCount = CountEnabled(model1),
            model2EnabledCount = CountEnabled(model2),
            model1RegisteredCount = CountRegistered(model1, manager),
            model2RegisteredCount = CountRegistered(model2, manager),
            resetGeneration = _resetGeneration,
            resetRemovalBarrierCount = _resetRemovalBarrierCount,
            lastResetRemovalFrame = _lastResetRemovalFrame,
            lastResetAddFrame = _lastResetAddFrame,
            lastResetBarrierModel1Registered = _lastResetBarrierModel1Registered,
            lastResetBarrierModel2Registered = _lastResetBarrierModel2Registered,
            frameMs = _frameMs,
            controllerMs = _controllerMs,
            lag = lag,
            windStrength = windStrength,
            clearanceScale = collisionClearanceScale,
            lagPullScale = _lagPullScale,
            lagSpringScale = _lagSpringScale,
            maxAuthoredPullError = maxPullError,
            maxAuthoredSpringError = maxSpringError,
            maxTipDisplacement = maxDisplacement,
            maxTipSpeed = maxSpeed,
            maxPhysBoneAngle = maxAngle,
            minClearance = float.IsPositiveInfinity(minClearance) ? 0f : minClearance,
            windBias = _windBias,
            windTarget = _windTarget,
            windBlendSeconds = WindBlendSeconds,
            windLastInterval = _windLastInterval,
            windNextIn = Mathf.Max(0f, _windNextAt - Time.unscaledTime),
            maxAppliedWindAngle = _maxAppliedWindAngle,
            managedBytes = GC.GetTotalMemory(false),
            tips = cache.tipTelemetry
        };
#if UNITY_WEBGL && !UNITY_EDITOR
        NanaHairSecondaryState(JsonUtility.ToJson(state));
#endif
    }

    public void SetSecondaryMotionEnabled(string value)
    {
        bool next = Toggle(value, secondaryMotionEnabled);
        if (next == secondaryMotionEnabled) return;
        secondaryMotionEnabled = next;
        BeginHardReset(next ? "secondary_enable" : "secondary_disable");
    }

    public void SetHairCollisionEnabled(string value)
    {
        bool next = Toggle(value, collisionEnabled);
        if (next == collisionEnabled) return;
        collisionEnabled = next;
        ApplyCollisionAssociations();
    }

    public void SetHairWindEnabled(string value)
    {
        windEnabled = Toggle(value, windEnabled);
        if (!windEnabled) { _windVelocity = 0f; RestoreWindRoots(); }
    }

    public void SetHairMotionResponse(string value)
    {
        lag = Parse(value, lag, 0f, 1f);
        ApplyLag();
    }

    public void SetHairWindScale(string value)
    {
        windStrength = Parse(value, windStrength, 0f, 1f);
    }

    public void SetHairCollisionClearance(string value)
    {
        collisionClearanceScale = Parse(value, collisionClearanceScale, .8f, 1.2f);
        ApplyClearance();
    }

    public void ResetHairSimulation(string unused)
    {
        BeginHardReset("manual");
    }

    private void OnApplicationPause(bool paused)
    {
        if (paused) _frameGapArmed = false;
        if (paused) BeginHardReset("application_pause");
    }

    private void OnApplicationFocus(bool focused)
    {
        if (!focused) _frameGapArmed = false;
        if (!focused) BeginHardReset("focus_loss");
    }

    private void OnDisable()
    {
        RestoreWindRoots();
        if (_models == null) return;
        SetAllPhysBonesEnabled(false);
        _lastResetReason = "lifecycle_disable";
        _resetPhase = ResetPhase.Remove;
    }

    private static int CountEnabled(ModelBindings bindings)
    {
        int count = 0;
        if (bindings == null || bindings.physBones == null) return count;
        for (int index = 0; index < bindings.physBones.Length; index++)
            if (bindings.physBones[index] != null && bindings.physBones[index].enabled) count++;
        return count;
    }

    private static int CountRegistered(ModelBindings bindings, PhysBoneManager manager)
    {
        int count = 0;
        if (bindings == null || bindings.physBones == null || manager == null) return count;
        for (int index = 0; index < bindings.physBones.Length; index++)
            if (bindings.physBones[index] != null && manager.HasPhysBone(bindings.physBones[index])) count++;
        return count;
    }

    private float NextWindTarget(float previous)
    {
        float magnitude = .28f + (float)_windRandom.NextDouble() * .44f;
        if (Mathf.Abs(previous) < .05f) return _windRandom.Next(0, 2) == 0 ? -magnitude : magnitude;
        return previous > 0f ? -magnitude : magnitude;
    }

    private float NextWindInterval()
    {
        return WindIntervalMin + (float)_windRandom.NextDouble() * (WindIntervalMax - WindIntervalMin);
    }

    private static Vector3 ClosestPoint(Vector3 a, Vector3 b, Vector3 point)
    {
        Vector3 segment = b - a;
        float length2 = segment.sqrMagnitude;
        if (length2 < .0000001f) return a;
        return a + segment * Mathf.Clamp01(Vector3.Dot(point - a, segment) / length2);
    }

    private static float Hash01(string value)
    {
        unchecked
        {
            uint hash = 2166136261;
            for (int index = 0; index < value.Length; index++) hash = (hash ^ value[index]) * 16777619;
            return (hash & 0x00ffffff) / 16777215f;
        }
    }

    private static bool Finite(Vector3 value)
    {
        return Finite(value.x) && Finite(value.y) && Finite(value.z);
    }

    private static bool Finite(float value)
    {
        return !float.IsNaN(value) && !float.IsInfinity(value);
    }

    private static bool Toggle(string value, bool fallback)
    {
        string normalized = (value ?? string.Empty).Trim().ToLowerInvariant();
        if (normalized == "1" || normalized == "true" || normalized == "on") return true;
        if (normalized == "0" || normalized == "false" || normalized == "off") return false;
        return fallback;
    }

    private static float Parse(string value, float fallback, float minimum, float maximum)
    {
        return float.TryParse(value, NumberStyles.Float, CultureInfo.InvariantCulture, out float parsed) && Finite(parsed)
            ? Mathf.Clamp(parsed, minimum, maximum) : fallback;
    }

    public string DiagnosticResetPhase { get { return _resetPhase.ToString().ToLowerInvariant(); } }
    public string DiagnosticLastResetReason { get { return _lastResetReason; } }
    public int DiagnosticResetGeneration { get { return _resetGeneration; } }
    public int DiagnosticResetRemovalFrame { get { return _lastResetRemovalFrame; } }
    public int DiagnosticResetAddFrame { get { return _lastResetAddFrame; } }
    public float DiagnosticMaxAppliedWindAngle { get { return _maxAppliedWindAngle; } }
}
