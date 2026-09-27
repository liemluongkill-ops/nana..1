using System;
using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEngine;

public static class NanaModelVariantBuild
{
    public static void Configure(NanaTargetDrivenAvatarController primary, Animator secondary, SkinnedMeshRenderer face)
    {
        var root = new GameObject("NanaModelRuntime");
        var variants = root.AddComponent<NanaModelVariantController>();
        variants.primaryRoot = primary.animator.transform;
        variants.secondaryRoot = secondary.transform;
        variants.primaryFace = primary.face;
        variants.secondaryFace = face;
        variants.primaryHead = primary.head;
        variants.secondaryHead = secondary.GetBoneTransform(HumanBodyBones.Head);
        variants.primaryRenderers = VisibleRenderers(variants.primaryRoot);
        variants.secondaryRenderers = VisibleRenderers(variants.secondaryRoot);
        variants.secondaryRoot.SetPositionAndRotation(variants.primaryRoot.position, variants.primaryRoot.rotation);
        variants.secondaryRoot.localScale = variants.primaryRoot.localScale;

        var bindings = new List<NanaModelVariantController.BoneBinding>();
        var mapped = new HashSet<Transform>();
        Transform armature = primary.animator.GetBoneTransform(HumanBodyBones.Hips).parent;
        foreach (Transform source in armature.GetComponentsInChildren<Transform>(true))
        {
            string path = AnimationUtility.CalculateTransformPath(source, variants.primaryRoot);
            Transform destination = variants.secondaryRoot.Find(path);
            if (destination == null) continue;
            mapped.Add(source);
            bindings.Add(new NanaModelVariantController.BoneBinding {
                source = source, destination = destination,
                sourcePosition = source.localPosition, destinationPosition = destination.localPosition,
                rotationOffset = destination.localRotation * Quaternion.Inverse(source.localRotation),
                scaleRatio = new Vector3(Ratio(destination.localScale.x, source.localScale.x),
                    Ratio(destination.localScale.y, source.localScale.y), Ratio(destination.localScale.z, source.localScale.z)),
            });
        }
        // This wardrobe is deliberately restricted to the same Shinano rig, not arbitrary retargeting.
        for (int i = 0; i < (int)HumanBodyBones.LastBone; i++)
        {
            Transform source = primary.animator.GetBoneTransform((HumanBodyBones)i);
            Transform destination = secondary.GetBoneTransform((HumanBodyBones)i);
            if (source == null && destination == null) continue;
            if (source == null || destination == null || !mapped.Contains(source) ||
                variants.secondaryRoot.Find(AnimationUtility.CalculateTransformPath(source, variants.primaryRoot)) != destination)
                throw new InvalidOperationException("Model 2 has an incompatible humanoid bone: " + (HumanBodyBones)i);
        }
        variants.bones = bindings.ToArray();
        var shapes = new List<NanaModelVariantController.ShapeBinding>();
        for (int i = 0; i < primary.face.sharedMesh.blendShapeCount; i++)
        {
            int target = face.sharedMesh.GetBlendShapeIndex(primary.face.sharedMesh.GetBlendShapeName(i));
            if (target < 0) continue;
            shapes.Add(new NanaModelVariantController.ShapeBinding {
                source = i, destination = target,
                baselineOffset = face.GetBlendShapeWeight(target) - primary.face.GetBlendShapeWeight(i),
            });
        }
        foreach (string required in new[] { "vrc.v_aa", "mouth_a1", "eye_close", "eye_look_left", "eye_look_right", "mouth_smile" })
            if (face.sharedMesh.GetBlendShapeIndex(required) < 0)
                throw new InvalidOperationException("Model 2 is missing facial channel: " + required);
        variants.shapes = shapes.ToArray();
        secondary.enabled = false;
        foreach (SkinnedMeshRenderer renderer in variants.primaryRoot.GetComponentsInChildren<SkinnedMeshRenderer>(true))
            renderer.updateWhenOffscreen = true;
        foreach (SkinnedMeshRenderer renderer in variants.secondaryRoot.GetComponentsInChildren<SkinnedMeshRenderer>(true))
            renderer.updateWhenOffscreen = true;
        Debug.Log($"NANA_MODEL_VARIANTS bones={bindings.Count} shapes={shapes.Count} renderers={variants.primaryRenderers.Length}/{variants.secondaryRenderers.Length} single_controller=true");
    }

    private static float Ratio(float target, float source)
    {
        if (Mathf.Abs(source) < .00001f) throw new InvalidOperationException("Zero bone scale cannot be mirrored");
        return target / source;
    }

    private static Renderer[] VisibleRenderers(Transform root) => root.GetComponentsInChildren<Renderer>(true)
        .Where(r => r.enabled && r.gameObject.activeInHierarchy).ToArray();
}
