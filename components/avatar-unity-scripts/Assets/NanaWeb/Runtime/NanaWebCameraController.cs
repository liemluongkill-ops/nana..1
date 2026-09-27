using System;
using System.Collections;
using System.Globalization;
using UnityEngine;

[DefaultExecutionOrder(2000)]
public sealed class NanaWebCameraController : MonoBehaviour
{
    private static readonly Color OpaqueBackgroundColor =
        new Color(0.035f, 0.045f, 0.052f, 1f);
    private static readonly Color ChromaBackgroundColor = new Color(0f, 1f, 0f, 1f);

    public Camera targetCamera;
    public Transform avatarRoot;
    [Range(0f, 1f)] public float zoom = 0.28f;
    [Range(0.05f, 0.95f)] public float height = 0.88f;
    [Range(-180f, 180f)] public float yaw;
    [Range(-15f, 15f)] public float pitch;
    [Range(30f, 70f)] public float fieldOfView = 50f;
    [Range(0.02f, 0.5f)] public float smoothTime = 0.12f;

    private Bounds _avatarBounds;
    private bool _boundsReady;
    private Vector3 _positionVelocity;
    private bool _transparentBackground;
    private bool _chromaBackground;

    private void Awake()
    {
        ResolveReferences();
        RefreshBounds();
        ApplyPose(true);
    }

    private IEnumerator Start()
    {
        yield return null;
        RefreshBounds();
        ApplyPose(true);
    }

    private void OnDisable()
    {
        RestoreOpaqueBackground();
    }

    private void OnDestroy()
    {
        RestoreOpaqueBackground();
    }

    private void LateUpdate()
    {
        if (!_boundsReady)
            RefreshBounds();
        ApplyPose(false);
    }

    public void SetZoom(string value)
    {
        zoom = ParseNormalized(value, zoom);
    }

    public void SetHeight(string value)
    {
        height = Mathf.Clamp(ParseNormalized(value, height), 0.05f, 0.95f);
    }

    public void SetYaw(string value)
    {
        yaw = ParseFloat(value, yaw, -180f, 180f);
    }

    public void SetPitch(string value)
    {
        pitch = ParseFloat(value, pitch, -15f, 15f);
    }

    public void SetTransparentBackground(string value)
    {
        _transparentBackground = ParseToggle(value, _transparentBackground);
        _chromaBackground = false;
        ApplyBackground();
    }

    public void SetChromaBackground(string value)
    {
        _chromaBackground = ParseToggle(value, _chromaBackground);
        _transparentBackground = false;
        ApplyBackground();
    }

    private void LogBackgroundState()
    {
        if (targetCamera == null)
            return;
        string mode = _chromaBackground
            ? "chroma"
            : (_transparentBackground ? "transparent" : "opaque");
        Debug.Log(
            $"NANA_WEB_CAMERA_BACKGROUND mode={mode} " +
            $"clearFlags={targetCamera.clearFlags} color={targetCamera.backgroundColor}",
            this);
    }

    public void SetPreset(string preset)
    {
        switch ((preset ?? string.Empty).Trim().ToLowerInvariant())
        {
            case "portrait":
                zoom = 0.28f;
                height = 0.88f;
                yaw = 0f;
                pitch = 0f;
                break;
            case "waist":
                zoom = 0.52f;
                height = 0.75f;
                yaw = 0f;
                pitch = 0f;
                break;
            case "full":
                zoom = 1f;
                height = 0.5f;
                yaw = 0f;
                pitch = 0f;
                break;
        }
    }

    private void ResolveReferences()
    {
        if (targetCamera == null)
            targetCamera = Camera.main ?? FindObjectOfType<Camera>();
        if (avatarRoot != null)
            return;
        foreach (Animator animator in FindObjectsOfType<Animator>(true))
        {
            if (!animator.isHuman)
                continue;
            avatarRoot = animator.transform;
            break;
        }
    }

    private void ApplyBackground()
    {
        ResolveReferences();
        if (targetCamera == null)
            return;
        targetCamera.clearFlags = CameraClearFlags.SolidColor;
        targetCamera.backgroundColor = _chromaBackground
            ? ChromaBackgroundColor
            : (_transparentBackground ? Color.clear : OpaqueBackgroundColor);
        LogBackgroundState();
    }

    private void RestoreOpaqueBackground()
    {
        _transparentBackground = false;
        _chromaBackground = false;
        if (targetCamera == null)
            return;
        targetCamera.clearFlags = CameraClearFlags.SolidColor;
        targetCamera.backgroundColor = OpaqueBackgroundColor;
    }

    private void RefreshBounds()
    {
        ResolveReferences();
        if (avatarRoot == null)
            return;

        Animator animator = avatarRoot.GetComponent<Animator>();
        if (animator != null && animator.isHuman)
        {
            Transform head = animator.GetBoneTransform(HumanBodyBones.Head);
            Transform hips = animator.GetBoneTransform(HumanBodyBones.Hips);
            Transform leftFoot = animator.GetBoneTransform(HumanBodyBones.LeftFoot);
            Transform rightFoot = animator.GetBoneTransform(HumanBodyBones.RightFoot);
            if (head != null && hips != null && leftFoot != null && rightFoot != null)
            {
                float footY = Mathf.Min(leftFoot.position.y, rightFoot.position.y);
                float headSpan = Mathf.Max(0.1f, head.position.y - hips.position.y);
                float floorY = footY - Mathf.Max(0.06f, headSpan * 0.2f);
                float topY = head.position.y + Mathf.Max(0.14f, headSpan * 0.55f);
                float cleanHeight = Mathf.Max(0.5f, topY - floorY);
                Vector3 center = new Vector3(
                    (head.position.x + hips.position.x) * 0.5f,
                    (topY + floorY) * 0.5f,
                    (head.position.z + hips.position.z) * 0.5f);
                _avatarBounds = new Bounds(
                    center,
                    new Vector3(cleanHeight * 0.58f, cleanHeight, cleanHeight * 0.45f));
                _boundsReady = true;
                Debug.Log(
                    $"NANA_WEB_CAMERA_BOUNDS source=humanoid center={_avatarBounds.center:F3} " +
                    $"size={_avatarBounds.size:F3}",
                    this);
                return;
            }
        }

        bool initialized = false;
        Bounds combined = default;
        foreach (Renderer renderer in avatarRoot.GetComponentsInChildren<Renderer>(false))
        {
            if (renderer is ParticleSystemRenderer || !renderer.enabled)
                continue;
            if (!initialized)
            {
                combined = renderer.bounds;
                initialized = true;
            }
            else
            {
                combined.Encapsulate(renderer.bounds);
            }
        }
        if (!initialized || combined.size.y < 0.1f)
            return;
        _avatarBounds = combined;
        _boundsReady = true;
        Debug.Log(
            $"NANA_WEB_CAMERA_BOUNDS source=renderer center={combined.center:F3} " +
            $"size={combined.size:F3}",
            this);
    }

    private void ApplyPose(bool immediate)
    {
        if (!_boundsReady || targetCamera == null)
            return;

        targetCamera.fieldOfView = fieldOfView;
        targetCamera.nearClipPlane = 0.03f;

        float avatarHeight = Mathf.Max(0.1f, _avatarBounds.size.y);
        float targetY = _avatarBounds.min.y + avatarHeight * height;
        Vector3 target = new Vector3(_avatarBounds.center.x, targetY, _avatarBounds.center.z);

        float verticalFov = fieldOfView * Mathf.Deg2Rad;
        float fullDistance = avatarHeight * 0.5f / Mathf.Tan(verticalFov * 0.5f) * 1.20f;
        float portraitDistance = avatarHeight * 0.29f;
        float easedZoom = zoom * zoom * (3f - 2f * zoom);
        float distance = Mathf.Lerp(portraitDistance, fullDistance, easedZoom);

        Quaternion orbit = Quaternion.Euler(pitch, yaw, 0f);
        Vector3 desiredPosition = target + orbit * Vector3.forward * distance;
        Quaternion desiredRotation = Quaternion.LookRotation(target - desiredPosition, Vector3.up);

        if (immediate || smoothTime <= 0.02f)
        {
            targetCamera.transform.SetPositionAndRotation(desiredPosition, desiredRotation);
            _positionVelocity = Vector3.zero;
            return;
        }

        targetCamera.transform.position = Vector3.SmoothDamp(
            targetCamera.transform.position,
            desiredPosition,
            ref _positionVelocity,
            smoothTime,
            Mathf.Infinity,
            Time.unscaledDeltaTime);
        float rotationBlend = 1f - Mathf.Exp(-Time.unscaledDeltaTime / smoothTime);
        targetCamera.transform.rotation = Quaternion.Slerp(
            targetCamera.transform.rotation,
            desiredRotation,
            rotationBlend);
    }

    private static float ParseNormalized(string value, float fallback)
    {
        return ParseFloat(value, fallback, 0f, 1f);
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

    private static float ParseFloat(string value, float fallback, float minimum, float maximum)
    {
        if (!float.TryParse(
                value,
                NumberStyles.Float,
                CultureInfo.InvariantCulture,
                out float parsed))
            return fallback;
        return Mathf.Clamp(parsed, minimum, maximum);
    }
}
