using System;
using UnityEngine;

// One live rig owns all behavior. The other outfit only mirrors its final pose.
[DefaultExecutionOrder(1500)]
public sealed class NanaModelVariantController : MonoBehaviour
{
    [Serializable]
    public sealed class BoneBinding
    {
        public Transform source;
        public Transform destination;
        public Vector3 sourcePosition;
        public Vector3 destinationPosition;
        public Quaternion rotationOffset;
        public Vector3 scaleRatio;
    }

    [Serializable]
    public sealed class ShapeBinding
    {
        public int source;
        public int destination;
        public float baselineOffset;
    }

    public Transform primaryRoot;
    public Transform secondaryRoot;
    public SkinnedMeshRenderer primaryFace;
    public SkinnedMeshRenderer secondaryFace;
    public Transform primaryHead;
    public Transform secondaryHead;
    public Renderer[] primaryRenderers;
    public Renderer[] secondaryRenderers;
    public BoneBinding[] bones;
    public ShapeBinding[] shapes;
    public int selectedModel = 1;
    private float _nextTelemetry;

#if UNITY_WEBGL && !UNITY_EDITOR
    [System.Runtime.InteropServices.DllImport("__Internal")]
    private static extern void NanaAvatarModelState(string json);
#endif

    private void Awake()
    {
        selectedModel = selectedModel == 2 ? 2 : 1;
        ApplyVisibility();
    }

    public void SelectModel(string value)
    {
        if (!int.TryParse(value, out int model) || (model != 1 && model != 2)) return;
        selectedModel = model;
        CopyPose();
        ApplyVisibility();
        Publish();
    }

    private void LateUpdate()
    {
        if (selectedModel == 2) CopyPose();
        if (Time.unscaledTime < _nextTelemetry) return;
        _nextTelemetry = Time.unscaledTime + .1f;
        Publish();
    }

    private void CopyPose()
    {
        secondaryRoot.SetPositionAndRotation(primaryRoot.position, primaryRoot.rotation);
        foreach (BoneBinding bone in bones)
        {
            bone.destination.localPosition = bone.destinationPosition + bone.source.localPosition - bone.sourcePosition;
            bone.destination.localRotation = bone.rotationOffset * bone.source.localRotation;
            bone.destination.localScale = Vector3.Scale(bone.source.localScale, bone.scaleRatio);
        }
        foreach (ShapeBinding shape in shapes)
            secondaryFace.SetBlendShapeWeight(shape.destination,
                primaryFace.GetBlendShapeWeight(shape.source) + shape.baselineOffset);
    }

    private void ApplyVisibility()
    {
        foreach (Renderer renderer in primaryRenderers) renderer.enabled = selectedModel == 1;
        foreach (Renderer renderer in secondaryRenderers) renderer.enabled = selectedModel == 2;
    }

    private void Publish()
    {
#if UNITY_WEBGL && !UNITY_EDITOR
        SkinnedMeshRenderer visibleFace = selectedModel == 2 ? secondaryFace : primaryFace;
        int aa = visibleFace.sharedMesh.GetBlendShapeIndex("vrc.v_aa");
        int blink = visibleFace.sharedMesh.GetBlendShapeIndex("eye_close");
        NanaAvatarModelState(JsonUtility.ToJson(new State {
            ready = true, selected = selectedModel, count = 2,
            bone_bindings = bones.Length, shape_bindings = shapes.Length,
            head_error_degrees = selectedModel == 2 ? Quaternion.Angle(primaryHead.rotation, secondaryHead.rotation) : 0,
            mouth_weight = aa >= 0 ? visibleFace.GetBlendShapeWeight(aa) : -1,
            blink_weight = blink >= 0 ? visibleFace.GetBlendShapeWeight(blink) : -1,
        }));
#endif
    }

    [Serializable]
    private sealed class State
    {
        public bool ready;
        public int selected;
        public int count;
        public int bone_bindings;
        public int shape_bindings;
        public float head_error_degrees;
        public float mouth_weight;
        public float blink_weight;
    }
}
