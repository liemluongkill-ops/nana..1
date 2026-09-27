using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class NanaFidelityReference
{
    private static readonly string Output = Path.GetFullPath(
        Path.Combine(Application.dataPath, "..", "output", "playwright"));

    public static void Capture()
    {
        EditorSceneManager.OpenScene("Assets/NanaWeb/NanaWebRuntime.unity", OpenSceneMode.Single);
        DynamicGI.UpdateEnvironment();
        double readyAt = EditorApplication.timeSinceStartup + 4;
        EditorApplication.CallbackFunction wait = null;
        wait = () =>
        {
            if (EditorApplication.timeSinceStartup < readyAt) return;
            EditorApplication.update -= wait;
            CaptureReady();
        };
        EditorApplication.update += wait;
    }

    private static void CaptureReady()
    {
        try
        {
            var controller = UnityEngine.Object.FindObjectOfType<NanaTargetDrivenAvatarController>();
            controller.enabled = false;
            var animator = controller.animator;
            animator.Rebind();
            animator.Update(0f);
            var cameraController = UnityEngine.Object.FindObjectOfType<NanaWebCameraController>();
            cameraController.enabled = false;
            foreach (ParticleSystem particles in animator.GetComponentsInChildren<ParticleSystem>())
                particles.Stop(true, ParticleSystemStopBehavior.StopEmittingAndClear);

            var originals = new Dictionary<Renderer, Material[]>();
            foreach (Renderer renderer in animator.GetComponentsInChildren<Renderer>(true))
                originals.Add(renderer, renderer.sharedMaterials);
            var camera = cameraController.targetCamera;
            camera.aspect = 1280f / 800f;
            camera.allowMSAA = false;
            ShaderUtil.allowAsyncCompilation = false;
            Directory.CreateDirectory(Output);
            foreach (string preset in new[] { "portrait", "waist", "full", "fabric", "eye" })
            {
                cameraController.SetPreset(preset == "fabric" || preset == "eye" ? "portrait" : preset);
                if (preset == "fabric") { cameraController.zoom = 0; cameraController.height = 0.64f; cameraController.yaw = 35; }
                if (preset == "eye") { cameraController.zoom = 0; cameraController.height = 0.9f; cameraController.yaw = 0; }
                typeof(NanaWebCameraController).GetMethod("RefreshBounds", BindingFlags.Instance | BindingFlags.NonPublic)
                    .Invoke(cameraController, null);
                typeof(NanaWebCameraController).GetMethod("ApplyPose", BindingFlags.Instance | BindingFlags.NonPublic)
                    .Invoke(cameraController, new object[] { true });
                foreach (var pair in originals) pair.Key.sharedMaterials = pair.Value;
                Save(camera, "native-specialized-" + preset);
                foreach (var pair in originals)
                    pair.Key.sharedMaterials = pair.Value.Select(Original).ToArray();
                Save(camera, "native-original-" + preset);
            }
            Debug.Log("NANA_FIDELITY_REFERENCE saved=10");
            EditorApplication.Exit(0);
        }
        catch (Exception error)
        {
            Debug.LogException(error);
            EditorApplication.Exit(1);
        }
    }

    private static Material Original(Material material)
    {
        if (material == null) return null;
        string path = AssetDatabase.GetAssetPath(material);
        if (!path.StartsWith("Assets/NanaWeb/Generated/Fidelity/")) return material;
        string guid = Path.GetFileName(Path.GetDirectoryName(path));
        Material original = AssetDatabase.LoadAssetAtPath<Material>(AssetDatabase.GUIDToAssetPath(guid));
        if (original == null) throw new InvalidOperationException("Reference material missing: " + guid);
        return original;
    }

    private static void Save(Camera camera, string name)
    {
        var target = RenderTexture.GetTemporary(1280, 800, 24, RenderTextureFormat.ARGB32);
        camera.targetTexture = target;
        camera.Render();
        camera.Render();
        RenderTexture previous = RenderTexture.active;
        RenderTexture.active = target;
        var image = new Texture2D(1280, 800, TextureFormat.RGB24, false);
        image.ReadPixels(new Rect(0, 0, 1280, 800), 0, 0);
        image.Apply();
        File.WriteAllBytes(Output + "/" + name + ".png", image.EncodeToPNG());
        UnityEngine.Object.DestroyImmediate(image);
        RenderTexture.active = previous;
        camera.targetTexture = null;
        RenderTexture.ReleaseTemporary(target);
    }
}
