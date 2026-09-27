using System;
using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using VRC.Dynamics;
using VRC.SDK3.Dynamics.PhysBone.Components;

// Reconstruct only the approved custom hair after baking and platform stripping.
public static class NanaHairSecondaryBuild
{
    private const string RuntimeName = "NanaHairSecondaryRuntime";
    private const string ColliderRuntimeName = "NanaHairColliderRuntime";
    private const string RollTwinPrefab = "Assets/#MARIYURI/Twinkle Cat/Shinano/Shinano_Roll Twin.prefab";
    private const string DivinePrefab = "Assets/WishList/Divine/Shinano/Prefab/Divine_Shinano 6.prefab";

    public static void Configure()
    {
        NanaModelVariantController variants = UnityEngine.Object.FindObjectOfType<NanaModelVariantController>();
        if (variants == null) throw new InvalidOperationException("NanaModelVariantController missing");

        RemovePreviousCandidate(variants);
        GameObject owner = new GameObject(RuntimeName);
        GameObject colliderOwner = new GameObject(ColliderRuntimeName);
        NanaHairSecondaryMotionController controller = owner.AddComponent<NanaHairSecondaryMotionController>();
        controller.variants = variants;
        controller.secondaryMotionEnabled = true;
        controller.collisionEnabled = true;
        controller.windEnabled = true;
        controller.lag = .5f;
        controller.windStrength = .15f;
        controller.collisionClearanceScale = 1f;
        controller.model1 = BuildModel(1, variants, colliderOwner.transform,
            variants.primaryRoot, variants.primaryHead, RollTwinPrefab, "MariYuri_Hair",
            new[] { "==Bang==", "==Side==", "==Twintail==", "==Ahoge==" });
        controller.model2 = BuildModel(2, variants, colliderOwner.transform,
            variants.secondaryRoot, variants.secondaryHead, DivinePrefab, "Divine_Root",
            new[] { "Divine_Back_Root", "Divine_Front_Root", "Divine_Side_Down_Root",
                "Divine_Side_Ears_Root", "Divine_Side_Root" });

        NanaHairSecondarySchedulerHost scheduler = colliderOwner.AddComponent<NanaHairSecondarySchedulerHost>();
        scheduler.controller = controller;

        ValidateCountDefinitions(controller);
        ValidateNoDoubleSolver(variants, controller);
        EditorUtility.SetDirty(controller);
        EditorSceneManager.MarkSceneDirty(EditorSceneManager.GetActiveScene());
        Debug.Log(string.Format(
            "NANA_WEB_HAIR solver=VRCPhysBone model1_pb={0} model1_colliders={1} model1_structural={2} " +
            "model1_solver={3} model2_pb={4} model2_colliders={5} model2_structural={6} model2_solver={7} " +
            "model2_structural_only={8} overlap=0 runtime_collider=false",
            controller.model1.physBones.Length, controller.model1.colliders.Length,
            controller.model1.structuralTransformUnionCount, controller.model1.expectedSolverBoneCount,
            controller.model2.physBones.Length, controller.model2.colliders.Length,
            controller.model2.structuralTransformUnionCount, controller.model2.expectedSolverBoneCount,
            string.Join("|", controller.model2.structuralOnlyTransforms)));
    }

    private static NanaHairSecondaryMotionController.ModelBindings BuildModel(
        int model,
        NanaModelVariantController variants,
        Transform owner,
        Transform modelRoot,
        Transform head,
        string sourcePath,
        string customPrefix,
        string[] allowedGroups)
    {
        GameObject sourcePrefab = AssetDatabase.LoadAssetAtPath<GameObject>(sourcePath);
        if (sourcePrefab == null) throw new InvalidOperationException("Hair source prefab missing: " + sourcePath);
        Transform sourceCustom = FindExact(sourcePrefab.transform, customPrefix);
        Transform destinationCustom = FindStartsWith(head, customPrefix);
        if (sourceCustom == null || destinationCustom == null)
            throw new InvalidOperationException("Hair custom root mapping failed for model " + model);

        VRCPhysBoneCollider[] colliders = CreateColliders(model, owner, modelRoot);
        var physBones = new List<VRCPhysBone>(48);
        foreach (VRCPhysBone source in sourceCustom.GetComponentsInChildren<VRCPhysBone>(true))
        {
            string relative = AnimationUtility.CalculateTransformPath(source.transform, sourceCustom);
            if (!Allowed(relative, allowedGroups)) continue;
            Transform destination = relative.Length == 0 ? destinationCustom : destinationCustom.Find(relative);
            if (destination == null)
                throw new InvalidOperationException("PhysBone chain missing after bake: m" + model + "/" + relative);

            VRCPhysBone target = destination.gameObject.AddComponent<VRCPhysBone>();
            EditorUtility.CopySerialized(source, target);
            target.rootTransform = Remap(source.rootTransform, sourceCustom, destinationCustom);
            target.ignoreTransforms = Remap(source.ignoreTransforms, sourceCustom, destinationCustom);
            target.colliders = colliders.Cast<VRCPhysBoneColliderBase>().ToList();
            target.allowCollision = VRCPhysBoneBase.AdvancedBool.True;
            target.allowGrabbing = VRCPhysBoneBase.AdvancedBool.False;
            target.allowPosing = VRCPhysBoneBase.AdvancedBool.False;
            target.parameter = string.Empty;
            target.resetWhenDisabled = true;
            target.enabled = false;
            EditorUtility.SetDirty(target);
            physBones.Add(target);
        }
        if (physBones.Count < 4)
            throw new InvalidOperationException("Too few mapped PhysBones for model " + model);

        Transform[] windRoots = allowedGroups.Select(group => FindExact(destinationCustom, group))
            .Where(transform => transform != null).Distinct().ToArray();
        VRCPhysBone[] representatives = RepresentativePhysBones(model, physBones);
        Transform[] tips = representatives.Select(DeepestContinuation).ToArray();
        string[] ids = representatives.Select(physBone => "m" + model + ":" + physBone.transform.name).ToArray();
        HashSet<Transform> simulated = SimulatedTransforms(physBones);
        SolverDefinition solver = SolverDefinitionFor(physBones, simulated, destinationCustom);
        int overlap = CountVariantOverlap(simulated, variants);
        if (overlap != 0)
            throw new InvalidOperationException("Hair candidate overlaps model-copy transforms: model=" + model + " count=" + overlap);

        return new NanaHairSecondaryMotionController.ModelBindings {
            model = model,
            head = head,
            physBones = physBones.ToArray(),
            colliders = colliders,
            windRoots = windRoots,
            representativeTips = tips,
            representativeIds = ids,
            structuralTransformUnionCount = simulated.Count,
            expectedSolverBoneCount = solver.boneCount,
            structuralOnlyTransforms = solver.structuralOnlyPaths,
            variantOverlap = overlap
        };
    }

    private static VRCPhysBone[] RepresentativePhysBones(int model, List<VRCPhysBone> all)
    {
        string[] preferred = model == 1
            ? new[] { "Twintail_L", "Twintail_R", "Side roll_L", "Side roll_R", "Bang_L", "Bang_R" }
            : new[] { "Divine_Back_L", "Divine_Back_R", "Divine_Back_B", "Divine_Side_Down_L",
                "Divine_Side_Down_R", "Divine_Side_L", "Divine_Side_R", "Divine_Front", "Divine_Front.002" };
        var result = new List<VRCPhysBone>();
        foreach (string name in preferred)
        {
            VRCPhysBone match = all.FirstOrDefault(item => item.transform.name == name);
            if (match != null && !result.Contains(match)) result.Add(match);
        }
        if (result.Count == 0) result.AddRange(all.Take(Mathf.Min(6, all.Count)));
        return result.ToArray();
    }

    private static VRCPhysBoneCollider[] CreateColliders(int model, Transform owner, Transform root)
    {
        Animator animator = root.GetComponent<Animator>();
        Transform head = RequiredBone(animator, HumanBodyBones.Head);
        Transform neck = RequiredBone(animator, HumanBodyBones.Neck);
        Transform chest = RequiredBone(animator, HumanBodyBones.Chest);
        Transform spine = RequiredBone(animator, HumanBodyBones.Spine);
        Transform leftShoulder = RequiredBone(animator, HumanBodyBones.LeftShoulder);
        Transform rightShoulder = RequiredBone(animator, HumanBodyBones.RightShoulder);
        Transform leftArm = RequiredBone(animator, HumanBodyBones.LeftUpperArm);
        Transform rightArm = RequiredBone(animator, HumanBodyBones.RightUpperArm);
        var colliders = new List<VRCPhysBoneCollider>(8);
        if (model == 1)
        {
            colliders.Add(CreateCollider(owner, model, "Head", head,
                VRCPhysBoneColliderBase.ShapeType.Sphere, .062f, .124f, new Vector3(0, .060f, .015f)));
            colliders.Add(CreateCollider(owner, model, "Neck", neck,
                VRCPhysBoneColliderBase.ShapeType.Capsule, .030f, .090f, new Vector3(0, .010f, -.003f)));
            colliders.Add(CreateCollider(owner, model, "ChestLow", chest,
                VRCPhysBoneColliderBase.ShapeType.Capsule, .055f, .200f, new Vector3(0, .070f, .010f)));
            colliders.Add(CreateCollider(owner, model, "ChestUpper", chest,
                VRCPhysBoneColliderBase.ShapeType.Capsule, .050f, .200f, new Vector3(0, .130f, .015f)));
            colliders.Add(CreateCollider(owner, model, "ShoulderL", leftArm,
                VRCPhysBoneColliderBase.ShapeType.Capsule, .030f, .210f, new Vector3(.003f, .100f, -.005f)));
            colliders.Add(CreateCollider(owner, model, "ShoulderR", rightArm,
                VRCPhysBoneColliderBase.ShapeType.Capsule, .030f, .210f, new Vector3(-.003f, .100f, -.005f)));
        }
        else
        {
            colliders.Add(CreateCollider(owner, model, "Head", head,
                VRCPhysBoneColliderBase.ShapeType.Sphere, .066f, .132f, new Vector3(0, .060f, .015f)));
            colliders.Add(CreateCollider(owner, model, "Neck", neck,
                VRCPhysBoneColliderBase.ShapeType.Capsule, .034f, .100f, new Vector3(0, .012f, .002f)));
            colliders.Add(CreateCollider(owner, model, "Spine", spine,
                VRCPhysBoneColliderBase.ShapeType.Capsule, .115f, .380f, new Vector3(0, -.080f, .010f)));
            colliders.Add(CreateCollider(owner, model, "Chest", chest,
                VRCPhysBoneColliderBase.ShapeType.Capsule, .086f, .250f, new Vector3(0, .030f, .020f)));
            colliders.Add(CreateCollider(owner, model, "ShoulderL", leftShoulder,
                VRCPhysBoneColliderBase.ShapeType.Capsule, .035f, .140f, Vector3.zero));
            colliders.Add(CreateCollider(owner, model, "ShoulderR", rightShoulder,
                VRCPhysBoneColliderBase.ShapeType.Capsule, .035f, .140f, Vector3.zero));
            colliders.Add(CreateCollider(owner, model, "UpperArmL", leftArm,
                VRCPhysBoneColliderBase.ShapeType.Capsule, .030f, .200f, new Vector3(0, .110f, -.0045f)));
            colliders.Add(CreateCollider(owner, model, "UpperArmR", rightArm,
                VRCPhysBoneColliderBase.ShapeType.Capsule, .030f, .200f, new Vector3(0, .110f, -.0045f)));
        }
        return colliders.ToArray();
    }

    private static VRCPhysBoneCollider CreateCollider(
        Transform owner,
        int model,
        string id,
        Transform anchor,
        VRCPhysBoneColliderBase.ShapeType shape,
        float radius,
        float height,
        Vector3 position)
    {
        var gameObject = new GameObject("HairCollider_M" + model + "_" + id);
        gameObject.transform.SetParent(owner, false);
        VRCPhysBoneCollider collider = gameObject.AddComponent<VRCPhysBoneCollider>();
        collider.rootTransform = anchor;
        collider.shapeType = shape;
        collider.radius = radius;
        collider.height = Mathf.Max(height, radius * 2f);
        collider.position = position;
        collider.rotation = Quaternion.identity;
        collider.insideBounds = false;
        collider.bonesAsSpheres = false;
        collider.enabled = false;
        EditorUtility.SetDirty(collider);
        return collider;
    }

    private static Transform RequiredBone(Animator animator, HumanBodyBones bone)
    {
        Transform transform = animator != null && animator.isHuman ? animator.GetBoneTransform(bone) : null;
        if (transform == null) throw new InvalidOperationException("Humanoid collider anchor missing: " + bone);
        return transform;
    }

    private static void ValidateNoDoubleSolver(
        NanaModelVariantController variants,
        NanaHairSecondaryMotionController controller)
    {
        VRCPhysBone[] all = UnityEngine.Object.FindObjectsOfType<VRCPhysBone>(true);
        int expected = controller.model1.physBones.Length + controller.model2.physBones.Length;
        if (all.Length != expected)
            throw new InvalidOperationException("Unexpected PhysBone component outside candidate ownership");
        int wrappers = UnityEngine.Object.FindObjectsOfType<NanaHairSecondaryMotionController>(true).Length;
        if (wrappers != 1) throw new InvalidOperationException("Hair candidate must have exactly one runtime owner");
        GameObject[] colliderRoots = SceneObjectsNamed(ColliderRuntimeName);
        GameObject colliderOwner = colliderRoots.Length == 1 ? colliderRoots[0] : null;
        if (colliderOwner == null || colliderOwner.transform.parent != null || !colliderOwner.activeSelf ||
            colliderOwner.GetComponentsInChildren<VRCPhysBoneCollider>(true).Length !=
                controller.model1.colliders.Length + controller.model2.colliders.Length)
            throw new InvalidOperationException("Candidate colliders require one independent always-active root");
        var candidateColliders = new HashSet<VRCPhysBoneCollider>(
            controller.model1.colliders.Concat(controller.model2.colliders));
        VRCPhysBoneCollider[] sceneColliders = Resources.FindObjectsOfTypeAll<VRCPhysBoneCollider>()
            .Where(item => item != null && item.gameObject.scene.IsValid()).ToArray();
        if (sceneColliders.Length != candidateColliders.Count ||
            sceneColliders.Any(item => !candidateColliders.Contains(item)))
            throw new InvalidOperationException("Unexpected PhysBone collider outside candidate ownership");
        NanaHairSecondarySchedulerHost[] schedulers = Resources.FindObjectsOfTypeAll<NanaHairSecondarySchedulerHost>()
            .Where(item => item != null && item.gameObject.scene.IsValid()).ToArray();
        NanaHairSecondarySchedulerHost scheduler = schedulers.Length == 1 ? schedulers[0] : null;
        if (scheduler == null || scheduler.gameObject != colliderOwner || scheduler.controller != controller)
            throw new InvalidOperationException("Candidate requires one scheduler host on the independent root");
        if (variants.primaryRoot.GetComponentsInChildren<Collider>(true).Length != 0 ||
            variants.secondaryRoot.GetComponentsInChildren<Collider>(true).Length != 0)
            throw new InvalidOperationException("Candidate must not depend on Unity Collider components");
    }

    private static void ValidateCountDefinitions(NanaHairSecondaryMotionController controller)
    {
        bool model1 = controller.model1.physBones.Length == 31 &&
            controller.model1.colliders.Length == 6 &&
            controller.model1.structuralTransformUnionCount == 136 &&
            controller.model1.expectedSolverBoneCount == 136 &&
            controller.model1.structuralOnlyTransforms.Length == 0;
        bool model2Pin = controller.model2.structuralOnlyTransforms.Length == 1 &&
            (controller.model2.structuralOnlyTransforms[0] == "Divine_Pin" ||
             controller.model2.structuralOnlyTransforms[0].EndsWith("/Divine_Pin", StringComparison.Ordinal));
        bool model2 = controller.model2.physBones.Length == 30 &&
            controller.model2.colliders.Length == 8 &&
            controller.model2.structuralTransformUnionCount == 168 &&
            controller.model2.expectedSolverBoneCount == 167 && model2Pin;
        if (!model1 || !model2)
            throw new InvalidOperationException(string.Format(
                "Hair solver count definition changed: m1={0}/{1}/{2} m2={3}/{4}/{5}",
                controller.model1.structuralTransformUnionCount,
                controller.model1.expectedSolverBoneCount,
                string.Join("|", controller.model1.structuralOnlyTransforms),
                controller.model2.structuralTransformUnionCount,
                controller.model2.expectedSolverBoneCount,
                string.Join("|", controller.model2.structuralOnlyTransforms)));
    }

    private static void RemovePreviousCandidate(NanaModelVariantController variants)
    {
        foreach (NanaHairSecondaryMotionController controller in
            UnityEngine.Object.FindObjectsOfType<NanaHairSecondaryMotionController>(true))
            UnityEngine.Object.DestroyImmediate(controller.gameObject);
        foreach (NanaHairSecondarySchedulerHost scheduler in
            UnityEngine.Object.FindObjectsOfType<NanaHairSecondarySchedulerHost>(true))
            UnityEngine.Object.DestroyImmediate(scheduler);
        foreach (GameObject root in SceneObjectsNamed(ColliderRuntimeName))
            UnityEngine.Object.DestroyImmediate(root);
        foreach (Transform head in new[] { variants.primaryHead, variants.secondaryHead })
            foreach (VRCPhysBone physBone in head.GetComponentsInChildren<VRCPhysBone>(true))
                UnityEngine.Object.DestroyImmediate(physBone);
    }

    private static bool Allowed(string relative, string[] groups)
    {
        return groups.Any(group => relative == group ||
            relative.StartsWith(group + "/", StringComparison.Ordinal));
    }

    private static Transform Remap(Transform source, Transform sourceRoot, Transform destinationRoot)
    {
        if (source == null) return null;
        if (source != sourceRoot && !source.IsChildOf(sourceRoot)) return null;
        string relative = AnimationUtility.CalculateTransformPath(source, sourceRoot);
        Transform mapped = relative.Length == 0 ? destinationRoot : destinationRoot.Find(relative);
        if (mapped == null) throw new InvalidOperationException("PhysBone transform reference missing after bake: " + relative);
        return mapped;
    }

    private static List<Transform> Remap(List<Transform> source, Transform sourceRoot, Transform destinationRoot)
    {
        var mapped = new List<Transform>();
        if (source == null) return mapped;
        foreach (Transform transform in source)
        {
            Transform result = Remap(transform, sourceRoot, destinationRoot);
            if (result != null) mapped.Add(result);
        }
        return mapped;
    }

    private static HashSet<Transform> SimulatedTransforms(IEnumerable<VRCPhysBone> physBones)
    {
        var result = new HashSet<Transform>();
        foreach (VRCPhysBone physBone in physBones)
        {
            Transform root = physBone.rootTransform != null ? physBone.rootTransform : physBone.transform;
            foreach (Transform transform in root.GetComponentsInChildren<Transform>(true)) result.Add(transform);
        }
        return result;
    }

    private struct SolverDefinition
    {
        public int boneCount;
        public string[] structuralOnlyPaths;
    }

    private static SolverDefinition SolverDefinitionFor(
        IEnumerable<VRCPhysBone> physBones,
        HashSet<Transform> structural,
        Transform customRoot)
    {
        var managed = new HashSet<Transform>();
        int entries = 0;
        foreach (VRCPhysBone physBone in physBones)
        {
            physBone.InitTransforms(true);
            entries += physBone.bones.Count;
            foreach (VRCPhysBoneBase.Bone bone in physBone.bones)
                if (bone.transform != null) managed.Add(bone.transform);
        }
        var structuralOnly = new HashSet<Transform>(structural);
        structuralOnly.ExceptWith(managed);
        string[] paths = structuralOnly
            .Select(transform => AnimationUtility.CalculateTransformPath(transform, customRoot))
            .OrderBy(path => path, StringComparer.Ordinal).ToArray();
        return new SolverDefinition { boneCount = entries, structuralOnlyPaths = paths };
    }

    private static int CountVariantOverlap(HashSet<Transform> simulated, NanaModelVariantController variants)
    {
        if (variants.bones == null) return 0;
        int count = 0;
        foreach (Transform transform in simulated)
            if (variants.bones.Any(binding => binding != null &&
                (binding.source == transform || binding.destination == transform))) count++;
        return count;
    }

    private static Transform DeepestContinuation(VRCPhysBone physBone)
    {
        Transform current = physBone.rootTransform != null ? physBone.rootTransform : physBone.transform;
        int guard = 0;
        while (current.childCount > 0 && guard++ < 32)
            current = BestContinuation(current);
        return current;
    }

    private static Transform BestContinuation(Transform parent)
    {
        if (parent.childCount == 1) return parent.GetChild(0);
        Transform best = parent.GetChild(0);
        int bestCount = DescendantCount(best);
        for (int index = 1; index < parent.childCount; index++)
        {
            Transform candidate = parent.GetChild(index);
            int count = DescendantCount(candidate);
            if (count > bestCount) { best = candidate; bestCount = count; }
        }
        return best;
    }

    private static int DescendantCount(Transform root)
    {
        int count = 0;
        for (int index = 0; index < root.childCount; index++) count += 1 + DescendantCount(root.GetChild(index));
        return count;
    }

    private static Transform FindStartsWith(Transform root, string prefix)
    {
        foreach (Transform transform in root.GetComponentsInChildren<Transform>(true))
            if (transform.name.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) return transform;
        return null;
    }

    private static Transform FindExact(Transform root, string name)
    {
        foreach (Transform transform in root.GetComponentsInChildren<Transform>(true))
            if (string.Equals(transform.name, name, StringComparison.OrdinalIgnoreCase)) return transform;
        return null;
    }

    private static GameObject[] SceneObjectsNamed(string name)
    {
        return Resources.FindObjectsOfTypeAll<GameObject>()
            .Where(gameObject => gameObject != null && gameObject.scene.IsValid() &&
                string.Equals(gameObject.name, name, StringComparison.Ordinal))
            .ToArray();
    }
}
