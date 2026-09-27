using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;
using nadena.dev.ndmf.platform;

public static class NanaWebBuild
{
    private const string SourceScene =
        "Assets/Nana/Scenes/NanaAvatar_TargetDriven_Test.unity";
    private const string RuntimeScene =
        "Assets/NanaWeb/NanaWebRuntime.unity";
    private const string GeneratedRoot = "Assets/NanaWeb/Generated";
    private const string GeneratedMaterials = GeneratedRoot + "/Materials";
    private static bool UseLilToon => Environment.GetEnvironmentVariable("NANA_WEB_MATERIAL_PROFILE") != "legacy";

    public static void BuildWebGL()
    {
        try
        {
            ConfigurePlayer();
            if (UseLilToon && SystemInfo.graphicsDeviceType == GraphicsDeviceType.Null)
                throw new InvalidOperationException(
                    "Fidelity builds need a graphics device for skybox lighting. Omit -nographics and use -force-d3d11.");
            PrepareRuntimeScene();

            string output = Environment.GetEnvironmentVariable("NANA_WEBGL_OUTPUT");
            if (string.IsNullOrWhiteSpace(output))
            {
                output = Path.GetFullPath(
                    Path.Combine(Application.dataPath, UseLilToon
                        ? "../../web/public/unity-fidelity" : "../../web/public/unity"));
            }
            Directory.CreateDirectory(output);

            BuildPlayerOptions options = new BuildPlayerOptions
            {
                scenes = new[] { RuntimeScene },
                locationPathName = output,
                target = BuildTarget.WebGL,
                options = BuildOptions.None,
            };

            BuildReport report = BuildPipeline.BuildPlayer(options);
            BuildSummary summary = report.summary;
            Debug.Log(
                $"NANA_WEBGL_BUILD result={summary.result} " +
                $"size={summary.totalSize} errors={summary.totalErrors} " +
                $"warnings={summary.totalWarnings} output={output}");
            EditorApplication.Exit(summary.result == BuildResult.Succeeded ? 0 : 1);
        }
        catch (Exception exception)
        {
            Debug.LogException(exception);
            EditorApplication.Exit(1);
        }
    }

    private static void PrepareRuntimeScene()
    {
        Scene scene = EditorSceneManager.OpenScene(SourceScene, OpenSceneMode.Single);
        GameObject primary = scene.GetRootGameObjects().Single(o => o.name == "Shinano_kisekae");
        GameObject secondary = scene.GetRootGameObjects().Single(o => o.name == "Shinano_kisekae (1)");
        primary.SetActive(true);
        secondary.SetActive(true);
        NanaTargetDrivenAvatarController controller = primary.GetComponentInChildren<NanaTargetDrivenAvatarController>(true);
        if (controller == null)
            throw new InvalidOperationException("Target-driven avatar controller is missing");
        controller.animator = primary.GetComponent<Animator>();
        BakeModularAvatar(controller);
        var secondaryController = secondary.AddComponent<NanaTargetDrivenAvatarController>();
        secondaryController.animator = secondary.GetComponent<Animator>();
        BakeModularAvatar(secondaryController);
        Animator secondaryAnimator = secondaryController.animator;
        SkinnedMeshRenderer secondaryFace = secondaryController.face;
        UnityEngine.Object.DestroyImmediate(secondaryController);

        // Makeup must participate in shader specialization, while eye slots stay authored.
        NanaMakeupProfile.Apply(primary);
        NanaMakeupProfile.Apply(secondary);
        if (UseLilToon)
            NanaLilToonProfile.Apply(new[] { primary, secondary });
        else
        {
            ApplyWebMaterialProfile(controller.animator.gameObject);
            ApplyWebMaterialProfile(secondary);
        }
        StripScenePlatformComponents();
        NanaModelVariantBuild.Configure(controller, secondaryAnimator, secondaryFace);
        NanaHairSecondaryBuild.Configure();
        PrepareEvents(controller);
        controller.events.referenceFaceCatalog = NanaFaceCatalogBuild.LoadAndValidate(controller.face, secondaryFace);

        controller.idleMotionEnabled = true;
        controller.blinkEnabled = true;
        controller.gatewayPollingEnabled = false;
        controller.gatewayUrl = "/avatar-api";
        controller.gatewayToken = string.Empty;
        controller.gatewayPollInterval = 0.1f;
        controller.runRetargetDemoOnPlay = false;

        Camera camera = Camera.main ?? UnityEngine.Object.FindObjectOfType<Camera>();
        if (camera == null)
            throw new InvalidOperationException("Main camera is missing");
        camera.fieldOfView = 42f;
        camera.clearFlags = CameraClearFlags.SolidColor;
        camera.backgroundColor = new Color(0.035f, 0.045f, 0.052f, 1f);
        camera.allowHDR = false;
        camera.allowMSAA = true;
        camera.transform.localScale = Vector3.one;

        GameObject cameraRuntime = new GameObject("NanaWebCameraRuntime");
        NanaWebCameraController cameraController =
            cameraRuntime.AddComponent<NanaWebCameraController>();
        cameraController.targetCamera = camera;
        cameraController.avatarRoot = controller.animator.transform;
        cameraController.zoom = 0.28f;
        cameraController.height = 0.88f;
        cameraController.yaw = 0f;
        cameraController.pitch = 0f;
        cameraController.fieldOfView = 50f;

        Light light = UnityEngine.Object.FindObjectOfType<Light>();
        if (light != null)
        {
            light.color = new Color(1f, 0.95686275f, 0.8392157f, 1f);
            light.intensity = 1f;
        }

        EditorSceneManager.MarkSceneDirty(scene);
        Directory.CreateDirectory(Path.GetDirectoryName(RuntimeScene));
        if (!EditorSceneManager.SaveScene(scene, RuntimeScene, true))
            throw new IOException("Could not save Nana WebGL runtime scene");
        AssetDatabase.SaveAssets();
    }

    private static void PrepareEvents(NanaTargetDrivenAvatarController gaze)
    {
        // Clone the candidate Animator; the accepted standing/base assets stay untouched.
        const string path = "Assets/NanaWeb/Generated/Nana_Events.controller";
        AssetDatabase.DeleteAsset(path);
        if (!AssetDatabase.CopyAsset("Assets/Nana/Animations/Nana_TargetDriven.controller", path))
            throw new InvalidOperationException("Could not clone event Animator");
        var controller = AssetDatabase.LoadAssetAtPath<UnityEditor.Animations.AnimatorController>(path);
        var layers = controller.layers;
        layers[0].iKPass = true;
        controller.layers = layers;
        AddFingerLayer(controller, "Event Left Grip", AvatarMaskBodyPart.LeftFingers, "proxy_hands_fist");
        AddFingerLayer(controller, "Event Right Grip", AvatarMaskBodyPart.RightFingers, "proxy_hands_fist");
        AddFingerLayer(controller, "Event Wave Fingers", AvatarMaskBodyPart.RightFingers, "proxy_hands_open");
        EditorUtility.SetDirty(controller);
        gaze.animator.runtimeAnimatorController = controller;
        gaze.animator.applyRootMotion = false;
        gaze.animator.cullingMode = AnimatorCullingMode.AlwaysAnimate;
        var events = gaze.animator.gameObject.AddComponent<NanaAvatarEventController>();
        events.gaze = gaze;
        events.animator = gaze.animator;
        gaze.events = events;
        var mouth = gaze.animator.gameObject.AddComponent<NanaAvatarMouthController>();
        mouth.face = gaze.face;
        gaze.mouth = mouth;
    }

    private static void AddFingerLayer(UnityEditor.Animations.AnimatorController controller, string name, AvatarMaskBodyPart part, string clipName)
    {
        string folder = "Packages/com.vrchat.avatars/Samples/AV3 Demo Assets/Animation/ProxyAnim/";
        AnimationClip clip = AssetDatabase.LoadAssetAtPath<AnimationClip>(folder + clipName + ".anim");
        if (clip == null) throw new InvalidOperationException("Missing finger pose: " + clipName);
        AvatarMask mask = new AvatarMask { name = name + " Mask" };
        for (int i = 0; i < (int)AvatarMaskBodyPart.LastBodyPart; i++) mask.SetHumanoidBodyPartActive((AvatarMaskBodyPart)i, false);
        mask.SetHumanoidBodyPartActive(part, true);
        AssetDatabase.AddObjectToAsset(mask, controller);
        controller.AddLayer(name);
        var layers = controller.layers;
        var layer = layers[layers.Length - 1];
        layer.avatarMask = mask;
        layer.defaultWeight = 0;
        var state = layer.stateMachine.AddState("Finger Pose");
        state.motion = clip;
        state.writeDefaultValues = false;
        layer.stateMachine.defaultState = state;
        controller.layers = layers;
    }

    private static void BakeModularAvatar(NanaTargetDrivenAvatarController controller)
    {
        if (controller.animator == null)
        {
            foreach (Animator candidate in UnityEngine.Object.FindObjectsOfType<Animator>(true))
            {
                if (!candidate.isHuman)
                    continue;
                controller.animator = candidate;
                break;
            }
        }
        if (controller.animator == null)
            throw new InvalidOperationException("Humanoid Avatar Animator was not found");

        GameObject avatarRoot = controller.animator.gameObject;
        if (PrefabUtility.IsPartOfPrefabInstance(avatarRoot))
        {
            GameObject outermost = PrefabUtility.GetOutermostPrefabInstanceRoot(avatarRoot);
            PrefabUtility.UnpackPrefabInstance(
                outermost,
                PrefabUnpackMode.Completely,
                InteractionMode.AutomatedAction);
            avatarRoot = controller.animator.gameObject;
        }

        // Added outfit prefabs can remain nested even after the model root is unpacked.
        foreach (Transform child in avatarRoot.GetComponentsInChildren<Transform>(true))
        {
            if (child != null && PrefabUtility.IsAnyPrefabInstanceRoot(child.gameObject))
                PrefabUtility.UnpackPrefabInstance(child.gameObject, PrefabUnpackMode.Completely,
                    InteractionMode.AutomatedAction);
        }

        INDMFPlatformProvider platform =
            PlatformRegistry.GetPrimaryPlatformForAvatar(avatarRoot) ?? GenericPlatform.Instance;
        int renderersBefore = avatarRoot.GetComponentsInChildren<SkinnedMeshRenderer>(true).Length;
        int componentsBefore = avatarRoot.GetComponentsInChildren<MonoBehaviour>(true).Length;

        nadena.dev.ndmf.AvatarProcessor.ProcessAvatar(avatarRoot, platform);

        controller.animator = avatarRoot.GetComponent<Animator>() ??
            avatarRoot.GetComponentInChildren<Animator>(true);
        if (controller.animator == null || !controller.animator.isHuman)
            throw new InvalidOperationException("Baked avatar lost its humanoid Animator");

        controller.neck = controller.animator.GetBoneTransform(HumanBodyBones.Neck);
        controller.head = controller.animator.GetBoneTransform(HumanBodyBones.Head);
        controller.face = FindFaceRenderer(avatarRoot);
        if (controller.head == null || controller.face == null)
            throw new InvalidOperationException("Baked avatar lost head or face bindings");

        StripEditorAndPlatformComponents(avatarRoot);

        int renderersAfter = avatarRoot.GetComponentsInChildren<SkinnedMeshRenderer>(true).Length;
        int componentsAfter = avatarRoot.GetComponentsInChildren<MonoBehaviour>(true).Length;
        Debug.Log(
            $"NANA_WEBGL_BAKE platform={platform.QualifiedName} " +
            $"renderers={renderersBefore}->{renderersAfter} " +
            $"behaviours={componentsBefore}->{componentsAfter} " +
            $"head={controller.head.name} face={controller.face.name}");
    }

    private static void ApplyWebMaterialProfile(GameObject avatarRoot)
    {
        EnsureAssetFolder("Assets/NanaWeb", "Generated");
        EnsureAssetFolder(GeneratedRoot, "Materials");

        var replacements = new Dictionary<Material, Material>();
        int hairSlots = 0;
        int faceSlots = 0;
        foreach (Renderer renderer in avatarRoot.GetComponentsInChildren<Renderer>(true))
        {
            Material[] materials = renderer.sharedMaterials;
            bool changed = false;
            for (int slot = 0; slot < materials.Length; slot++)
            {
                Material source = materials[slot];
                if (source == null)
                    continue;

                bool isHair = source.name == "Blue 1" || source.name == "Shinano_hair";
                bool isFaceEyes = source.name == "Shinano SU001";
                if (!isHair && !isFaceEyes)
                    continue;

                if (!replacements.TryGetValue(source, out Material replacement))
                {
                    replacement = isHair
                        ? CreateHairMaterial(source)
                        : CreateFaceEyesMaterial(source);
                    replacements[source] = replacement;
                }
                materials[slot] = replacement;
                changed = true;
                if (isHair) hairSlots++;
                if (isFaceEyes) faceSlots++;
            }
            if (changed)
                renderer.sharedMaterials = materials;
        }

        AssetDatabase.SaveAssets();
        Debug.Log(
            $"NANA_WEB_MATERIAL_PROFILE hairSlots={hairSlots} faceSlots={faceSlots} " +
            $"materials={replacements.Count}");
    }

    private static Material CreateHairMaterial(Material source)
    {
        Shader shader = Shader.Find("Nana/WebGL/HairToon");
        if (shader == null)
            throw new InvalidOperationException("Nana WebGL hair shader is missing");

        Material material = CreateGeneratedMaterial(source, shader, "Hair");
        CopyTexture(source, material, "_MainTex", "_MainTex");
        CopyTextureTransform(source, material, "_MainTex");
        CopyColor(source, material, "_Color", "_Color", Color.white);
        CopyColor(source, material, "_ShadowColor", "_ShadeColor",
            new Color(0.62f, 0.68f, 0.82f, 1f));
        material.SetFloat("_ShadowStep", 0.48f);
        material.SetFloat("_ShadowSoftness", 0.2f);

        CopyTexture(source, material, "_MatCapTex", "_MatCapTex");
        CopyTexture(source, material, "_MatCapBlendMask", "_MatCapMask");
        CopyColor(source, material, "_MatCapColor", "_MatCapColor", Color.white);
        material.SetFloat("_MatCapStrength", source.name == "Blue 1" ? 0.48f : 0.34f);

        CopyTexture(source, material, "_MatCap2ndTex", "_MatCap2Tex");
        CopyTexture(source, material, "_MatCap2ndBlendMask", "_MatCap2Mask");
        CopyColor(source, material, "_MatCap2ndColor", "_MatCap2Color", Color.white);
        material.SetFloat("_MatCap2Strength",
            source.HasProperty("_UseMatCap2nd") && source.GetFloat("_UseMatCap2nd") > 0.5f
                ? 0.2f
                : 0f);

        CopyTexture(source, material, "_RimColorTex", "_RimMask");
        CopyColor(source, material, "_RimColor", "_RimColor",
            new Color(0.5f, 0.75f, 1f, 1f));
        material.SetFloat("_RimPower", 2.2f);
        material.SetFloat("_RimStrength", 0.14f);
        CopyColor(source, material, "_OutlineColor", "_OutlineColor",
            new Color(0.18f, 0.22f, 0.32f, 1f));
        material.SetFloat("_OutlineWidth", 0.00065f);
        material.SetFloat("_Cull", source.HasProperty("_Cull") ? source.GetFloat("_Cull") : 2f);
        EditorUtility.SetDirty(material);
        return material;
    }

    private static Material CreateFaceEyesMaterial(Material source)
    {
        Shader shader = Shader.Find("Nana/WebGL/FaceEyes");
        if (shader == null)
            throw new InvalidOperationException("Nana WebGL face/eye shader is missing");

        Material material = CreateGeneratedMaterial(source, shader, "FaceEyes");
        CopyTexture(source, material, "_MainTex", "_MainTex");
        CopyTextureTransform(source, material, "_MainTex");
        CopyTexture(source, material, "_Main2ndTex", "_EyeTex");
        CopyTexture(source, material, "_AlphaMask", "_AlphaMask");
        CopyColor(source, material, "_Color", "_Color", Color.white);
        CopyColor(source, material, "_ShadowColor", "_ShadeColor",
            new Color(1f, 0.88f, 0.92f, 1f));
        material.SetFloat("_ShadeStrength", 0.12f);
        CopyTexture(source, material, "_MatCapTex", "_MatCapTex");
        CopyTexture(source, material, "_MatCapBlendMask", "_MatCapMask");
        CopyColor(source, material, "_MatCapColor", "_MatCapColor", Color.white);
        material.SetFloat("_MatCapStrength", 0.34f);
        CopyTexture(source, material, "_EmissionMap", "_EmissionMap");
        CopyColor(source, material, "_EmissionColor", "_EmissionColor",
            new Color(0.8f, 0.65f, 1.5f, 1f));
        material.SetFloat("_EmissionStrength", 0.62f);
        material.SetFloat("_Cull", source.HasProperty("_Cull") ? source.GetFloat("_Cull") : 2f);
        EditorUtility.SetDirty(material);
        return material;
    }

    private static Material CreateGeneratedMaterial(Material source, Shader shader, string role)
    {
        string safeName = source.name.Replace(" ", "_").Replace("/", "_");
        string path = $"{GeneratedMaterials}/Web_{role}_{safeName}.mat";
        AssetDatabase.DeleteAsset(path);
        Material material = new Material(shader)
        {
            name = $"Web {role} {source.name}",
            renderQueue = source.renderQueue,
        };
        AssetDatabase.CreateAsset(material, path);
        return material;
    }

    private static void CopyTexture(
        Material source,
        Material destination,
        string sourceProperty,
        string destinationProperty)
    {
        if (!source.HasProperty(sourceProperty) || !destination.HasProperty(destinationProperty))
            return;
        Texture texture = source.GetTexture(sourceProperty);
        if (texture != null)
            destination.SetTexture(destinationProperty, texture);
    }

    private static void CopyTextureTransform(Material source, Material destination, string property)
    {
        if (!source.HasProperty(property) || !destination.HasProperty(property))
            return;
        destination.SetTextureScale(property, source.GetTextureScale(property));
        destination.SetTextureOffset(property, source.GetTextureOffset(property));
    }

    private static void CopyColor(
        Material source,
        Material destination,
        string sourceProperty,
        string destinationProperty,
        Color fallback)
    {
        destination.SetColor(
            destinationProperty,
            source.HasProperty(sourceProperty) ? source.GetColor(sourceProperty) : fallback);
    }

    private static void EnsureAssetFolder(string parent, string child)
    {
        string path = $"{parent}/{child}";
        if (!AssetDatabase.IsValidFolder(path))
            AssetDatabase.CreateFolder(parent, child);
    }

    private static void StripScenePlatformComponents()
    {
        int removedBehaviours = 0;
        int removedMissing = 0;
        int removedHelpers = 0;
        foreach (MonoBehaviour behaviour in
                 UnityEngine.Object.FindObjectsOfType<MonoBehaviour>(true))
        {
            if (behaviour == null)
                continue;
            string typeName = behaviour.GetType().FullName ?? string.Empty;
            if (!typeName.StartsWith("VRC.", StringComparison.Ordinal) &&
                !typeName.StartsWith("nadena.dev.", StringComparison.Ordinal))
                continue;
            UnityEngine.Object.DestroyImmediate(behaviour);
            removedBehaviours++;
        }

        foreach (Transform transform in UnityEngine.Object.FindObjectsOfType<Transform>(true))
        {
            if (transform == null)
                continue;
            removedMissing += GameObjectUtility.RemoveMonoBehavioursWithMissingScript(
                transform.gameObject);
            if (transform.name.StartsWith("nadena.dev.ndmf__", StringComparison.Ordinal))
            {
                UnityEngine.Object.DestroyImmediate(transform.gameObject);
                removedHelpers++;
            }
        }
        Debug.Log(
            $"NANA_WEB_SCENE_STRIP behaviours={removedBehaviours} " +
            $"missing={removedMissing} helpers={removedHelpers}");
    }

    private static SkinnedMeshRenderer FindFaceRenderer(GameObject avatarRoot)
    {
        SkinnedMeshRenderer best = null;
        int bestScore = -1;
        foreach (SkinnedMeshRenderer renderer in
                 avatarRoot.GetComponentsInChildren<SkinnedMeshRenderer>(true))
        {
            Mesh mesh = renderer.sharedMesh;
            if (mesh == null)
                continue;

            int score = 0;
            for (int index = 0; index < mesh.blendShapeCount; index++)
            {
                string shape = mesh.GetBlendShapeName(index).ToLowerInvariant();
                if (shape.Contains("eye_close") || shape.Contains("blink")) score += 4;
                if (shape.Contains("eye_look")) score += 2;
                if (shape.Contains("mouth") || shape.Contains("vrc.v_")) score += 1;
            }
            if (renderer.name.Equals("Body", StringComparison.OrdinalIgnoreCase)) score += 3;
            if (score <= bestScore)
                continue;
            best = renderer;
            bestScore = score;
        }
        return best;
    }

    private static void StripEditorAndPlatformComponents(GameObject avatarRoot)
    {
        foreach (MonoBehaviour behaviour in
                 avatarRoot.GetComponentsInChildren<MonoBehaviour>(true))
        {
            if (behaviour == null)
                continue;
            string typeName = behaviour.GetType().FullName ?? string.Empty;
            if (typeName.StartsWith("VRC.", StringComparison.Ordinal) ||
                typeName.StartsWith("nadena.dev.", StringComparison.Ordinal))
            {
                UnityEngine.Object.DestroyImmediate(behaviour);
            }
        }

        foreach (Collider collider in avatarRoot.GetComponentsInChildren<Collider>(true))
            UnityEngine.Object.DestroyImmediate(collider);

        foreach (Transform transform in avatarRoot.GetComponentsInChildren<Transform>(true))
            GameObjectUtility.RemoveMonoBehavioursWithMissingScript(transform.gameObject);
    }

    private static void ConfigurePlayer()
    {
        EditorUserBuildSettings.SwitchActiveBuildTarget(
            BuildTargetGroup.WebGL,
            BuildTarget.WebGL);
        PlayerSettings.companyName = "Nana Local";
        PlayerSettings.productName = "Nana Avatar";
        PlayerSettings.runInBackground = true;
        PlayerSettings.defaultScreenWidth = 1280;
        PlayerSettings.defaultScreenHeight = 720;
        PlayerSettings.stripEngineCode = false;
        PlayerSettings.colorSpace = ColorSpace.Linear;
        PlayerSettings.WebGL.compressionFormat = WebGLCompressionFormat.Disabled;
        PlayerSettings.WebGL.dataCaching = true;
        PlayerSettings.WebGL.exceptionSupport = WebGLExceptionSupport.None;
        PlayerSettings.SetGraphicsAPIs(
            BuildTarget.WebGL,
            new[] { GraphicsDeviceType.OpenGLES3 });
    }
}
