using System;
using System.Collections;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using UnityEngine;
using UnityEngine.Networking;

// Runs on the humanoid Animator object: Unity owns the arm IK solve.
[DefaultExecutionOrder(1100)]
public sealed class NanaAvatarEventController : MonoBehaviour
{
    [Serializable]
    public sealed class Intent
    {
        public string action;
        public string intent_id;
        public float duration_s = -1f;
    }

    [Serializable]
    public sealed class FaceChannel
    {
        public string shape;
        public float weight, delay;
    }

    [Serializable]
    public sealed class FacePreset
    {
        public string id, version, action;
        public float enter, exit, duration;
        public bool ownsHead;
        public Vector3 head;
        public FaceChannel[] channels;

        public FaceChannel Find(string shape)
        {
            foreach (FaceChannel channel in channels)
                if (channel.shape == shape) return channel;
            return null;
        }
    }

    [Serializable]
    public sealed class FacePresetCatalog
    {
        public string id, version;
        public FacePreset[] presets;
    }

    [Serializable]
    private sealed class Receipt { public string intent_id; public string status; public string reason; }
    [Serializable]
    private sealed class State
    {
        public bool ready;
        public string action, phase, faceAction, facePhase, intent_id;
        public float progress, smile, armWeight, handDistance;
        public Vector3 head, leftHand, rightHand, leftFoot, rightFoot, headPosition, chestAngles, rightFingerDirection;
        public string face_intent_id, expression_revision;
        public float face_elapsed, face_enter, face_hold, face_exit, face_head_elapsed;
        public bool face_head_waiting, face_head_interrupted;
        public FaceWeight[] faceWeights;
    }
    [Serializable]
    private sealed class FaceWeight
    {
        public string shape;
        public float weight;
    }
    private sealed class Track
    {
        public Intent intent;
        public bool gateway;
        public float time, enter, hold, exit, headTime;
        public bool releasing, headInterrupted, headWaiting;
        public readonly HashSet<string> faceChannels = new HashSet<string>();
        public string Phase => releasing ? "returning" : time < enter ? "entering" : "holding";
    }

    public NanaTargetDrivenAvatarController gaze;
    public Animator animator;
    public TextAsset referenceFaceCatalog;
    private readonly Dictionary<string, FacePreset> _facePresets = new Dictionary<string, FacePreset>();
    private readonly List<string> _faceShapes = new List<string>(Shapes);
    private readonly HashSet<string> _writtenFaceShapes = new HashSet<string>();
    private Vector3 _faceHeadStart, _faceHeadNow;
    private Transform _root, _left, _right, _hips, _chest;
    private Quaternion _spineBase, _chestBase;
    private Quaternion _leftStartRotation, _rightStartRotation, _leftRotation, _rightRotation;
    private Vector3 _leftStart, _rightStart, _leftGoal, _rightGoal, _headStart, _headNow;
    private float _armWeight, _armStartWeight, _bow, _bowStart, _height, _motionWeight;
    private float _leftWeight, _leftStartWeight, _gripWeight, _openWeight;
    private bool _ready, _headOwned, _faceHeadOwned;
    private Track _motion, _expression;
    private readonly Queue<Receipt> _receipts = new Queue<Receipt>();
    private Coroutine _receiptRoutine;
    private float _nextStateAt;
    private int _leftFistLayer, _rightFistLayer, _rightOpenLayer;
    private readonly Dictionary<string, int> _indices = new Dictionary<string, int>();
    private readonly Dictionary<string, float> _baseline = new Dictionary<string, float>();
    private readonly Dictionary<string, float> _currentFace = new Dictionary<string, float>();
    private readonly Dictionary<string, float> _faceStart = new Dictionary<string, float>();
    private static readonly string[] Shapes = {
        "mouth_smile", "eye_joy", "eye_happy", "eye_surprise", "eyebrow_up",
        "eyebrow_sad1", "mouth_hawa", "other_cheek_1", "eye_joy_L", "eye_close_L", "eye_close_R"
    };
    public bool Ready => _ready;

#if UNITY_WEBGL && !UNITY_EDITOR
    [DllImport("__Internal")] private static extern void NanaAvatarEventState(string json);
#endif

    private IEnumerator Start()
    {
        if (animator == null) animator = GetComponent<Animator>();
        if (gaze == null) gaze = GetComponentInChildren<NanaTargetDrivenAvatarController>(true);
        yield return null;
        yield return null;
        if (animator == null || !animator.isHuman || gaze == null || !gaze.RigReady) yield break;
        _root = animator.transform;
        _left = animator.GetBoneTransform(HumanBodyBones.LeftHand);
        _right = animator.GetBoneTransform(HumanBodyBones.RightHand);
        _hips = animator.GetBoneTransform(HumanBodyBones.Hips);
        _chest = animator.GetBoneTransform(HumanBodyBones.Chest);
        _spineBase = animator.GetBoneTransform(HumanBodyBones.Spine).localRotation;
        _chestBase = _chest.localRotation;
        _height = _root.InverseTransformPoint(gaze.head.position).y;
        _leftFistLayer = animator.GetLayerIndex("Event Left Grip");
        _rightFistLayer = animator.GetLayerIndex("Event Right Grip");
        _rightOpenLayer = animator.GetLayerIndex("Event Wave Fingers");
        if (referenceFaceCatalog != null)
        {
            FacePresetCatalog catalog = JsonUtility.FromJson<FacePresetCatalog>(referenceFaceCatalog.text);
            foreach (FacePreset preset in catalog.presets)
            {
                _facePresets.Add(preset.action, preset);
                foreach (FaceChannel channel in preset.channels)
                    if (!_faceShapes.Contains(channel.shape)) _faceShapes.Add(channel.shape);
            }
        }
        foreach (string name in _faceShapes)
        {
            int index = gaze.face.sharedMesh.GetBlendShapeIndex(name);
            _indices[name] = index;
            _baseline[name] = index >= 0 ? gaze.face.GetBlendShapeWeight(index) : 0f;
            _currentFace[name] = 0f;
            _faceStart[name] = 0f;
        }
        gaze.events = this;
        _ready = _left != null && _right != null;
    }

    public static bool Supports(string action)
    {
        switch (action)
        {
            case "curious": case "happy": case "nod": case "listen": case "think":
            case "surprised": case "shy": case "playful": case "wave": case "shy_smile":
            case "wink_soft_smile": case "heart_happy": case "surprised_pout":
            case "serious_think": case "cat_teary_smile": case "shy_crying": case "playful_wink":
            case "settle": case "idle": case "blink": return true;
            default: return false;
        }
    }

    public static bool IsCatalogExpression(string action)
    {
        switch (action)
        {
            case "wink_soft_smile": case "heart_happy": case "surprised_pout":
            case "serious_think": case "cat_teary_smile": case "shy_crying": case "playful_wink": return true;
            default: return false;
        }
    }

    private FacePreset FaceProfile(string action)
    {
        return !string.IsNullOrEmpty(action) && _facePresets.TryGetValue(action, out FacePreset preset) ? preset : null;
    }

    public void SetExpression(string action)
    {
        string selected = (action ?? string.Empty).Trim();
        if (selected.Length == 0 || selected == "none" || selected == "idle")
        {
            if (_expression != null && !_expression.releasing) BeginRelease(_expression);
            return;
        }
        if (FaceProfile(selected) != null) Receive(new Intent { action = selected }, false);
    }

    public void Receive(Intent intent, bool gateway)
    {
        if (intent == null) return;
        // Owner policy, 2026-09-08: these remain review assets, never live actions.
        if (intent.action == "wave" || intent.action == "shy_smile" || intent.action == "heart_happy")
        {
            Ack(intent, gateway, "failed", "owner_disabled_action");
            return;
        }
        if (!_ready || !Supports(intent.action))
        {
            Ack(intent, gateway, "failed", !_ready ? "rig_not_ready" : "unsupported_action");
            return;
        }
        if (string.IsNullOrEmpty(intent.intent_id)) intent.intent_id = Guid.NewGuid().ToString("N");
        if ((_motion != null && _motion.intent.intent_id == intent.intent_id) ||
            (_expression != null && _expression.intent.intent_id == intent.intent_id)) return;
        if (intent.action == "idle")
        {
            gaze.idleMotionEnabled = true;
            Ack(intent, gateway, "started", "runtime_started");
            Ack(intent, gateway, "finished", "idle_enabled");
            return; // Ambient enable never takes ownership away from a gesture.
        }
        FacePreset faceProfile = FaceProfile(intent.action);
        if (IsCatalogExpression(intent.action) && faceProfile == null)
        { Ack(intent, gateway, "failed", "missing_face_profile"); return; }
        bool faceOnly = faceProfile != null || intent.action == "happy" || intent.action == "surprised" || intent.action == "blink";
        bool settle = intent.action == "settle";
        if (float.IsNaN(intent.duration_s) || float.IsInfinity(intent.duration_s))
        { Ack(intent, gateway, "failed", "invalid_duration"); return; }
        float duration = intent.duration_s < 0f ? 0f : Mathf.Clamp(intent.duration_s, 0f, 60f);
        if (float.IsNaN(duration) || float.IsInfinity(duration))
        { Ack(intent, gateway, "failed", "invalid_duration"); return; }
        float enter = intent.action == "shy_smile" ? 1.25f : intent.action == "wave" ? .65f : .5f;
        float exit = intent.action == "shy_smile" ? 1f : .65f;
        if (faceProfile != null) { enter = faceProfile.enter; exit = faceProfile.exit; }
        if (intent.action == "nod") { enter = .35f; exit = .4f; }
        if (intent.action == "blink") { enter = .1f; exit = .16f; }
        if (duration > 0 && duration < enter + exit + .1f)
        {
            float factor = duration / (enter + exit + .1f);
            enter *= factor; exit *= factor;
        }
        float hold = intent.action == "blink" ? .09f : 1f;
        if (duration > 0f) hold = Mathf.Min(hold, Mathf.Max(.05f, duration - enter - exit));
        Track track = new Track { intent = intent, gateway = gateway, enter = enter, exit = exit, hold = hold };
        if (faceOnly)
        {
            if (_expression != null)
                foreach (string shape in _expression.faceChannels) track.faceChannels.Add(shape);
            foreach (string shape in _faceShapes)
                if (FaceTarget(intent.action, shape) > 0f) track.faceChannels.Add(shape);
            Cancel(_expression); _expression = track;
            foreach (string shape in track.faceChannels) _faceStart[shape] = _currentFace[shape];
            if (faceProfile != null && faceProfile.ownsHead)
            {
                _faceHeadStart = gaze.CurrentOffset;
                _faceHeadNow = _faceHeadStart;
                track.headWaiting = _headOwned;
            }
        }
        else
        {
            Track previousMotion = _motion;
            Cancel(_motion);
            SnapshotMotion();
            _motion = track;
            if (intent.action != "wave" || _headOwned) _headOwned = true;
            if (settle)
            {
                if (previousMotion != null)
                    foreach (string shape in _faceShapes)
                        if (FaceTarget(previousMotion.intent.action, shape) > 0f) track.faceChannels.Add(shape);
                if (_expression != null)
                    foreach (string shape in _expression.faceChannels) track.faceChannels.Add(shape);
                Cancel(_expression); _expression = null;
                _faceHeadOwned = false;
                foreach (string shape in track.faceChannels) _faceStart[shape] = _currentFace[shape];
                track.releasing = true; track.time = 0f; track.exit = duration > 0 ? Mathf.Min(duration, 1f) : 1f;
                _headOwned = true;
            }
        }
        Ack(intent, gateway, "started", "runtime_started");
        Publish();
    }

    public void InterruptHead()
    {
        if (!_headOwned && !_faceHeadOwned) return;
        // A look can interrupt a compound pose; arms blend back instead of snapping.
        if (_motion != null) { Cancel(_motion); SnapshotMotion(); _motion.gateway = false; BeginRelease(_motion); }
        if (_expression != null) _expression.headInterrupted = true;
        _headOwned = false;
        _faceHeadOwned = false;
        gaze.ReleaseEventHead(false);
    }

    private static float DefaultDuration(string action)
    {
        switch (action)
        {
            case "shy_smile": return 5.5f; case "wave": return 2.8f;
            case "think": case "listen": return 4f; case "curious": return 3.2f;
            case "shy": return 3.5f; case "playful": return 3f; case "nod": return 1.2f;
            case "happy": return 2f; case "surprised": return 1.6f; case "blink": return .35f;
            default: return 1.2f;
        }
    }

    private void SnapshotMotion()
    {
        _leftStart = _root.InverseTransformPoint(_left.position);
        _rightStart = _root.InverseTransformPoint(_right.position);
        _leftStartRotation = Quaternion.Inverse(_root.rotation) * _left.rotation;
        _rightStartRotation = Quaternion.Inverse(_root.rotation) * _right.rotation;
        _armStartWeight = _armWeight;
        _leftStartWeight = _leftWeight;
        _headStart = gaze.CurrentOffset;
        _bowStart = _bow;
    }

    private void BeginRelease(Track track)
    {
        if (track.releasing) return;
        track.releasing = true; track.time = 0f;
        if (track == _motion) SnapshotMotion();
        if (track == _expression)
        {
            foreach (string shape in track.faceChannels) _faceStart[shape] = _currentFace[shape];
            _faceHeadStart = gaze.CurrentOffset;
        }
    }

    private void Update()
    {
        if (!_ready) return;
        float dt = Mathf.Max(Time.unscaledDeltaTime, 0f);
        Tick(ref _motion, dt);
        Tick(ref _expression, dt);
        UpdatePose();
        bool grip = _motion != null && _motion.intent.action == "shy_smile";
        bool wave = _motion != null && _motion.intent.action == "wave";
        _gripWeight = Mathf.MoveTowards(_gripWeight, grip ? _armWeight : 0, dt * 3);
        _openWeight = Mathf.MoveTowards(_openWeight, wave ? _armWeight : 0, dt * 3);
        if (_leftFistLayer >= 0) animator.SetLayerWeight(_leftFistLayer, _gripWeight * .7f);
        if (_rightFistLayer >= 0) animator.SetLayerWeight(_rightFistLayer, _gripWeight * .25f);
        if (_rightOpenLayer >= 0) animator.SetLayerWeight(_rightOpenLayer, _openWeight);
        UpdateFaceHead(dt);
        if (_headOwned) gaze.SetEventHead(_headNow);
        else if (_faceHeadOwned) gaze.SetEventHead(_faceHeadNow);
        if (_receipts.Count > 0 && _receiptRoutine == null) _receiptRoutine = StartCoroutine(SendReceipts());
    }

    private void Tick(ref Track track, float dt)
    {
        if (track == null) return;
        track.time += dt;
        if (!track.releasing && EnterAndHoldComplete(track)) BeginRelease(track);
        if (track.releasing && track.time >= track.exit)
        {
            Ack(track.intent, track.gateway, "finished", "runtime_finished");
            bool wasMotion = ReferenceEquals(track, _motion);
            track = null;
            if (wasMotion)
            {
                _armWeight = 0; _leftWeight = 0; _bow = 0; _motionWeight = 0;
                if (_headOwned) gaze.ReleaseEventHead(true);
                _headOwned = false;
            }
        }
    }

    private bool EnterAndHoldComplete(Track track)
    {
        FacePreset preset = track == _expression ? FaceProfile(track.intent.action) : null;
        if (preset != null && preset.ownsHead && !track.headInterrupted)
        {
            // Head accents are optional. Do not hold a gateway receipt indefinitely
            // while a long body gesture owns the head.
            if (track.headWaiting && track.time >= .5f) track.headInterrupted = true;
            if (!track.headInterrupted)
                return !track.headWaiting && track.headTime >= track.enter + track.hold;
        }
        return track.time >= track.enter + track.hold;
    }

    private void UpdateFaceHead(float dt)
    {
        FacePreset preset = _expression != null ? FaceProfile(_expression.intent.action) : null;
        if (preset == null || !preset.ownsHead || _expression.headInterrupted)
        {
            if (_faceHeadOwned && !_headOwned) gaze.ReleaseEventHead(true);
            _faceHeadOwned = false;
            return;
        }
        if (_headOwned) { _expression.headWaiting = true; return; }
        if (_expression.headWaiting)
        {
            _expression.headWaiting = false;
            _expression.headTime = 0f;
            _faceHeadStart = gaze.CurrentOffset;
        }
        if (!_expression.releasing) _expression.headTime += dt;
        float elapsed = _expression.releasing ? _expression.time : _expression.headTime;
        float progress = Smooth(elapsed / Mathf.Max(.05f, _expression.releasing ? _expression.exit : _expression.enter));
        _faceHeadNow = Vector3.Lerp(_faceHeadStart, _expression.releasing ? Vector3.zero : preset.head, progress);
        _faceHeadOwned = true;
    }

    private static float Smooth(float t) { t = Mathf.Clamp01(t); return t * t * t * (t * (t * 6f - 15f) + 10f); }
    private static float Weight(Track t) => t == null ? 0f : t.releasing ? 1f - Smooth(t.time / t.exit) : Smooth(t.time / t.enter);

    private void UpdatePose()
    {
        if (_motion == null) return;
        Track t = _motion;
        string action = t.intent.action;
        float p = Smooth(t.time / (t.releasing ? t.exit : t.enter));
        _motionWeight = Weight(t);
        Vector3 target = Vector3.zero;
        float bow = 0;
        switch (action)
        {
            case "curious": target = new Vector3(-2, -5, 8); break;
            case "listen": target = new Vector3(-1, 0, 3); break;
            case "think": target = new Vector3(-7, 12, -4); break;
            case "shy": target = new Vector3(7, -14, 5); break;
            case "playful": target = new Vector3(-3, 6, -9); break;
            case "shy_smile": target = new Vector3(-10, -4, 7); bow = 9; break;
            case "nod": target = _headStart + new Vector3(10, 0, 0); break;
        }
        if (t.releasing)
        {
            _headNow = Vector3.Lerp(_headStart, Vector3.zero, p);
            _armWeight = Mathf.Lerp(_armStartWeight, 0, p);
            _leftWeight = Mathf.Lerp(_leftStartWeight, 0, p);
            _bow = Mathf.Lerp(_bowStart, 0, p);
            return;
        }
        _headNow = Vector3.Lerp(_headStart, target, p);
        _bow = Mathf.Lerp(_bowStart, bow, p);
        if (action == "shy_smile")
        {
            _headNow = Vector3.Lerp(_headStart, target, Smooth((t.time - .3f) / .95f));
            _bow = t.time < .3f ? Mathf.Lerp(_bowStart, -2f, Smooth(t.time / .3f))
                : Mathf.Lerp(-2f, bow, Smooth((t.time - .3f) / .95f));
        }
        bool arms = action == "wave" || action == "shy_smile";
        _armWeight = Mathf.Lerp(_armStartWeight, arms ? 1 : 0, p);
        _leftWeight = Mathf.Lerp(_leftStartWeight, action == "shy_smile" ? 1 : 0, p);
        if (!arms) return;
        Vector3 hips = _root.InverseTransformPoint(_hips.position);
        float s = _height / 1.13f;
        if (action == "shy_smile")
        {
            // Two-stage path clears the torso before the hands meet behind the waist.
            Vector3 l = hips + new Vector3(-.035f, -.055f, -.195f) * s;
            Vector3 r = hips + new Vector3(.015f, -.09f, -.20f) * s;
            _leftGoal = AroundBody(_leftStart, hips + new Vector3(-.24f, -.09f, -.16f) * s, l, p);
            _rightGoal = AroundBody(_rightStart, hips + new Vector3(.24f, -.09f, -.16f) * s, r, p);
            _leftRotation = Quaternion.Slerp(_leftStartRotation, HandRotation(true, new Vector3(1, -.2f, 0), Vector3.forward), p);
            _rightRotation = Quaternion.Slerp(_rightStartRotation, HandRotation(false, new Vector3(-.5f, -.9f, 0), Vector3.back), p);
        }
        else
        {
            Vector3 chest = _root.InverseTransformPoint(_chest.position);
            Vector3 r = chest + new Vector3(.25f, .21f, .10f) * s;
            float waving = Mathf.Max(0, t.time - t.enter);
            r.x += Mathf.Sin(waving * 9f) * .023f * s;
            _rightGoal = Vector3.Lerp(_rightStart, r, p);
            _leftGoal = _leftStart;
            _rightRotation = Quaternion.Slerp(_rightStartRotation,
                Quaternion.AngleAxis(Mathf.Sin(waving * 9f) * 14f, Vector3.forward) * HandRotation(false, Vector3.up, Vector3.forward), p);
            _leftRotation = _leftStartRotation;
        }
    }

    private static Vector3 AroundBody(Vector3 from, Vector3 via, Vector3 to, float t)
    {
        return t < .55f ? Vector3.Lerp(from, via, Smooth(t / .55f)) : Vector3.Lerp(via, to, Smooth((t - .55f) / .45f));
    }

    private Quaternion HandRotation(bool left, Vector3 direction, Vector3 palm)
    {
        Transform hand = left ? _left : _right;
        Transform middle = animator.GetBoneTransform(left ? HumanBodyBones.LeftMiddleProximal : HumanBodyBones.RightMiddleProximal);
        Transform index = animator.GetBoneTransform(left ? HumanBodyBones.LeftIndexProximal : HumanBodyBones.RightIndexProximal);
        Transform little = animator.GetBoneTransform(left ? HumanBodyBones.LeftLittleProximal : HumanBodyBones.RightLittleProximal);
        Vector3 forward = hand.InverseTransformPoint(middle.position).normalized;
        Vector3 across = hand.InverseTransformPoint(index.position) - hand.InverseTransformPoint(little.position);
        Vector3 normal = Vector3.Cross(forward, across).normalized * (left ? -1f : 1f);
        return Quaternion.LookRotation(direction, palm) * Quaternion.Inverse(Quaternion.LookRotation(forward, normal));
    }

    private void OnAnimatorIK(int layer)
    {
        if (!_ready) return;
        ApplyBow(HumanBodyBones.Spine, _spineBase, _bow * .45f);
        ApplyBow(HumanBodyBones.Chest, _chestBase, _bow * .55f);
        ApplyHand(AvatarIKGoal.LeftHand, AvatarIKHint.LeftElbow, _leftGoal, _leftRotation, _leftWeight, true);
        ApplyHand(AvatarIKGoal.RightHand, AvatarIKHint.RightElbow, _rightGoal, _rightRotation, _armWeight, false);
    }

    private void ApplyHand(AvatarIKGoal goal, AvatarIKHint hint, Vector3 position, Quaternion rotation, float weight, bool left)
    {
        animator.SetIKPositionWeight(goal, weight);
        // Humanoid IK goal rotations use an avatar-specific wrist frame. Orient the
        // actual hand transform after IK, using its measured finger/palm basis.
        animator.SetIKRotationWeight(goal, 0);
        animator.SetIKHintPositionWeight(hint, weight * .7f);
        animator.SetIKPosition(goal, _root.TransformPoint(position));
        animator.SetIKRotation(goal, _root.rotation * rotation);
        Vector3 hips = _root.InverseTransformPoint(_hips.position);
        bool wave = _motion != null && _motion.intent.action == "wave";
        animator.SetIKHintPosition(hint, _root.TransformPoint(hips + new Vector3(left ? -.24f : .24f, wave ? .04f : -.01f, wave ? .06f : -.10f) * (_height / 1.13f)));
    }

    private void ApplyBow(HumanBodyBones bone, Quaternion basis, float angle)
    {
        Transform joint = animator.GetBoneTransform(bone);
        Vector3 axis = joint.parent.InverseTransformDirection(_root.right);
        animator.SetBoneLocalRotation(bone, Quaternion.AngleAxis(angle, axis) * basis);
    }

    private float FaceTarget(string action, string shape)
    {
        FacePreset preset = FaceProfile(action);
        if (preset != null) return preset.Find(shape)?.weight ?? 0f;
        switch (action)
        {
            case "happy": return shape == "mouth_smile" ? 70 : shape == "eye_joy" ? 35 : 0;
            case "curious": return shape == "mouth_smile" ? 35 : shape == "eyebrow_up" ? 20 : 0;
            case "listen": return shape == "mouth_smile" ? 12 : 0;
            case "think": return shape == "eyebrow_sad1" ? 22 : 0;
            case "surprised": return shape == "eye_surprise" ? 60 : shape == "eyebrow_up" ? 35 : shape == "mouth_hawa" ? 22 : 0;
            case "shy": return shape == "mouth_smile" ? 45 : shape == "eyebrow_sad1" ? 15 : shape == "other_cheek_1" ? 25 : 0;
            case "shy_smile": return shape == "mouth_smile" ? 65 : shape == "eye_joy" ? 22 : shape == "other_cheek_1" ? 25 : 0;
            case "playful": return shape == "mouth_smile" ? 70 : shape == "eye_joy_L" ? 90 : 0;
            case "wave": return shape == "mouth_smile" ? 55 : shape == "eye_joy" ? 15 : 0;
            case "blink": return shape == "eye_close_L" || shape == "eye_close_R" ? 100 : 0;
            default: return 0;
        }
    }

    private void LateUpdate()
    {
        if (!_ready) return;
        if (_leftWeight > 0) _left.rotation = Quaternion.Slerp(_left.rotation, _root.rotation * _leftRotation, _leftWeight);
        if (_armWeight > 0) _right.rotation = Quaternion.Slerp(_right.rotation, _root.rotation * _rightRotation, _armWeight);
        foreach (string shape in _faceShapes)
        {
            float desired = _motion == null ? 0 : FaceTarget(_motion.intent.action, shape) * _motionWeight;
            bool motionOwns = _motion != null && _motion.intent.action != "settle" && FaceTarget(_motion.intent.action, shape) > 0f;
            bool expressionTracks = _expression != null && _expression.faceChannels.Contains(shape);
            bool settleTracks = _motion != null && _motion.intent.action == "settle" && _motion.faceChannels.Contains(shape);
            if (!motionOwns && !expressionTracks && !settleTracks && !_writtenFaceShapes.Contains(shape)) continue;
            if (_motion != null && !_motion.releasing && (_motion.intent.action == "curious" || _motion.intent.action == "shy_smile"))
                desired *= Smooth((_motion.time - .45f) / .7f);
            if (shape == "eye_joy_L" && _motion != null && _motion.intent.action == "playful")
                desired *= Mathf.Clamp01(1 - Mathf.Abs(_motion.time - 1.3f) / .2f);
            if (expressionTracks)
            {
                float f = Smooth(_expression.time / (_expression.releasing ? _expression.exit : _expression.enter));
                FacePreset preset = FaceProfile(_expression.intent.action);
                FaceChannel activeChannel = preset?.Find(shape);
                bool expressionOwns = activeChannel != null || (preset == null && FaceTarget(_expression.intent.action, shape) > 0f);
                if (!_expression.releasing && activeChannel != null)
                {
                    float delay = activeChannel.delay * _expression.enter;
                    f = Smooth((_expression.time - delay) / Mathf.Max(.01f, _expression.enter - delay));
                }
                float goal = _expression.releasing || !expressionOwns ? desired : FaceTarget(_expression.intent.action, shape);
                desired = Mathf.Lerp(_faceStart[shape], goal, f);
            }
            else if (settleTracks) desired = Mathf.Lerp(_faceStart[shape], 0f, Smooth(_motion.time / _motion.exit));
            _currentFace[shape] = Mathf.MoveTowards(_currentFace[shape], desired, Time.unscaledDeltaTime * 260f);
            int index = _indices[shape];
            if (index < 0) continue;
            float value = _baseline[shape] + _currentFace[shape];
            if (shape.StartsWith("eye_close_")) value = Mathf.Max(value, gaze.face.GetBlendShapeWeight(index));
            gaze.face.SetBlendShapeWeight(index, Mathf.Clamp(value, 0, 100));
            if (motionOwns || expressionTracks || settleTracks || Mathf.Abs(_currentFace[shape]) > .01f) _writtenFaceShapes.Add(shape);
            else _writtenFaceShapes.Remove(shape);
        }
        if (Time.unscaledTime >= _nextStateAt) { Publish(); _nextStateAt = Time.unscaledTime + .1f; }
    }

    private void Cancel(Track track)
    {
        if (track != null) Ack(track.intent, track.gateway, "cancelled", "runtime_interrupted");
    }

    private void OnDisable()
    {
        if (!_ready) return;
        StopAllCoroutines();
        _receiptRoutine = null;
        _receipts.Clear();
        _motion = null; _expression = null;
        _armWeight = 0; _leftWeight = 0; _bow = 0;
        _gripWeight = 0; _openWeight = 0;
        if ((_headOwned || _faceHeadOwned) && gaze != null) gaze.ReleaseEventHead(true);
        _headOwned = false;
        _faceHeadOwned = false;
        foreach (string shape in _writtenFaceShapes)
        {
            if (gaze != null && gaze.face != null && _indices[shape] >= 0)
                gaze.face.SetBlendShapeWeight(_indices[shape], _baseline[shape]);
            _currentFace[shape] = 0;
        }
        _writtenFaceShapes.Clear();
        if (animator != null)
        {
            if (_leftFistLayer >= 0) animator.SetLayerWeight(_leftFistLayer, 0);
            if (_rightFistLayer >= 0) animator.SetLayerWeight(_rightFistLayer, 0);
            if (_rightOpenLayer >= 0) animator.SetLayerWeight(_rightOpenLayer, 0);
        }
    }

    private void Ack(Intent intent, bool gateway, string status, string reason)
    {
        if (gateway && !string.IsNullOrEmpty(intent.intent_id))
            _receipts.Enqueue(new Receipt { intent_id = intent.intent_id, status = status, reason = reason });
    }

    private IEnumerator SendReceipts()
    {
        while (_receipts.Count > 0)
        {
            Receipt receipt = _receipts.Dequeue();
            using (UnityWebRequest request = new UnityWebRequest(gaze.ResolveGatewayBaseUrl() + "/v1/avatar/receipts", "POST"))
            {
                request.uploadHandler = new UploadHandlerRaw(System.Text.Encoding.UTF8.GetBytes(JsonUtility.ToJson(receipt)));
                request.downloadHandler = new DownloadHandlerBuffer();
                request.SetRequestHeader("Content-Type", "application/json");
                if (!string.IsNullOrEmpty(gaze.gatewayToken)) request.SetRequestHeader("Authorization", "Bearer " + gaze.gatewayToken);
                request.timeout = 2;
                yield return request.SendWebRequest();
            }
        }
        _receiptRoutine = null;
    }

    private void Publish()
    {
        if (!_ready) return;
        State state = new State { ready = _ready, action = _motion?.intent.action ?? "idle", phase = _motion?.Phase ?? "idle",
            faceAction = _expression?.intent.action ?? "", facePhase = _expression?.Phase ?? "idle",
            intent_id = _motion?.intent.intent_id ?? _expression?.intent.intent_id ?? "",
            progress = _motion == null ? 0 : Mathf.Clamp01(_motion.time / (_motion.releasing ? _motion.exit : _motion.enter)),
            smile = _currentFace["mouth_smile"], armWeight = _armWeight, head = gaze.CurrentOffset,
            leftHand = _root.InverseTransformPoint(_left.position), rightHand = _root.InverseTransformPoint(_right.position),
            leftFoot = _root.InverseTransformPoint(animator.GetBoneTransform(HumanBodyBones.LeftFoot).position),
            rightFoot = _root.InverseTransformPoint(animator.GetBoneTransform(HumanBodyBones.RightFoot).position),
            handDistance = Vector3.Distance(_left.position, _right.position) };
        state.face_intent_id = _expression?.intent.intent_id ?? "";
        state.expression_revision = "owner-approved-reactions-v1";
        state.face_elapsed = _expression?.time ?? 0f;
        state.face_enter = _expression?.enter ?? 0f;
        state.face_hold = _expression?.hold ?? 0f;
        state.face_exit = _expression?.exit ?? 0f;
        state.face_head_elapsed = _expression?.headTime ?? 0f;
        state.face_head_waiting = _expression?.headWaiting ?? false;
        state.face_head_interrupted = _expression?.headInterrupted ?? false;
        var weights = new List<FaceWeight>();
        foreach (string shape in _faceShapes)
            if (_indices[shape] >= 0) weights.Add(new FaceWeight { shape = shape, weight = gaze.face.GetBlendShapeWeight(_indices[shape]) });
        state.faceWeights = weights.ToArray();
        state.headPosition = _root.InverseTransformPoint(gaze.head.position);
        state.chestAngles = _chest.localEulerAngles;
        state.rightFingerDirection = _root.InverseTransformDirection((animator.GetBoneTransform(HumanBodyBones.RightMiddleProximal).position - _right.position).normalized);
#if UNITY_WEBGL && !UNITY_EDITOR
        NanaAvatarEventState(JsonUtility.ToJson(state));
#endif
    }
}
