using System;
using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class NanaWebMaterialAudit
{
    private const string RuntimeScene = "Assets/NanaWeb/NanaWebRuntime.unity";

    public static void Run()
    {
        try
        {
            EditorSceneManager.OpenScene(RuntimeScene, OpenSceneMode.Single);
            var seen = new HashSet<Material>();
            foreach (Renderer renderer in UnityEngine.Object.FindObjectsOfType<Renderer>(true))
            {
                string rendererPath = GetPath(renderer.transform);
                if (renderer.name == "Body" || renderer.name == "Body_base" ||
                    renderer.name == "Shoes" || renderer.name.StartsWith("Hair_", StringComparison.Ordinal))
                {
                    Debug.Log(
                        $"NANA_WEB_RENDERER_BOUNDS renderer={rendererPath} " +
                        $"center={renderer.bounds.center:F3} size={renderer.bounds.size:F3}");
                }
                string meshName = renderer is SkinnedMeshRenderer skinned && skinned.sharedMesh != null
                    ? skinned.sharedMesh.name
                    : "";
                for (int slot = 0; slot < renderer.sharedMaterials.Length; slot++)
                {
                    Material material = renderer.sharedMaterials[slot];
                    if (material == null)
                        continue;
                    Debug.Log(
                        $"NANA_WEB_MATERIAL_USE renderer={rendererPath} mesh={meshName} " +
                        $"slot={slot} material={material.name} shader={material.shader.name}");
                    if (!seen.Add(material))
                        continue;
                    DumpMaterial(material);
                }
            }

            foreach (SkinnedMeshRenderer renderer in
                     UnityEngine.Object.FindObjectsOfType<SkinnedMeshRenderer>(true))
            {
                if (renderer.sharedMesh == null || renderer.sharedMesh.blendShapeCount == 0)
                    continue;
                var shapes = Enumerable.Range(0, renderer.sharedMesh.blendShapeCount)
                    .Select(renderer.sharedMesh.GetBlendShapeName);
                Debug.Log(
                    $"NANA_WEB_BLENDSHAPES renderer={GetPath(renderer.transform)} " +
                    $"material={string.Join(",", renderer.sharedMaterials.Where(m => m != null).Select(m => m.name))} " +
                    $"shapes={string.Join("|", shapes)}");
            }
            Animator humanoid = UnityEngine.Object.FindObjectsOfType<Animator>(true)
                .FirstOrDefault(candidate => candidate.isHuman);
            if (humanoid != null)
            {
                foreach (HumanBodyBones bone in new[]
                         {
                             HumanBodyBones.Hips,
                             HumanBodyBones.Head,
                             HumanBodyBones.LeftFoot,
                             HumanBodyBones.RightFoot,
                         })
                {
                    Transform transform = humanoid.GetBoneTransform(bone);
                    Debug.Log($"NANA_WEB_BONE bone={bone} position={transform?.position:F3}");
                }
            }
            EditorApplication.Exit(0);
        }
        catch (Exception exception)
        {
            Debug.LogException(exception);
            EditorApplication.Exit(1);
        }
    }

    private static void DumpMaterial(Material material)
    {
        string assetPath = AssetDatabase.GetAssetPath(material);
        var textures = new List<string>();
        int propertyCount = ShaderUtil.GetPropertyCount(material.shader);
        for (int index = 0; index < propertyCount; index++)
        {
            if (ShaderUtil.GetPropertyType(material.shader, index) != ShaderUtil.ShaderPropertyType.TexEnv)
                continue;
            string property = ShaderUtil.GetPropertyName(material.shader, index);
            Texture texture = material.GetTexture(property);
            if (texture != null)
                textures.Add($"{property}:{texture.name}");
        }

        Debug.Log(
            $"NANA_WEB_MATERIAL_DEF material={material.name} path={assetPath} " +
            $"shader={material.shader.name} shaderProperties={propertyCount} " +
            $"color={ColorValue(material, "_Color")} main={TextureValue(material, "_MainTex")} " +
            $"shadow={FloatValue(material, "_UseShadow")} shade1={ColorValue(material, "_ShadowColor")} " +
            $"shade2={ColorValue(material, "_Shadow2ndColor")} " +
            $"matcap={FloatValue(material, "_UseMatCap")} matcapTex={TextureValue(material, "_MatCapTex")} " +
            $"matcapColor={ColorValue(material, "_MatCapColor")} matcapBlend={FloatValue(material, "_MatCapBlend")} " +
            $"rim={FloatValue(material, "_UseRim")} rimColor={ColorValue(material, "_RimColor")} " +
            $"emission={FloatValue(material, "_UseEmission")} emissionMap={TextureValue(material, "_EmissionMap")} " +
            $"emissionColor={ColorValue(material, "_EmissionColor")} glitter={FloatValue(material, "_UseGlitter")} " +
            $"textures=[{string.Join(",", textures)}]");
    }

    private static string FloatValue(Material material, string property)
    {
        return material.HasProperty(property) ? material.GetFloat(property).ToString("0.###") : "n/a";
    }

    private static string ColorValue(Material material, string property)
    {
        if (!material.HasProperty(property))
            return "n/a";
        Color value = material.GetColor(property);
        return $"{value.r:0.###}/{value.g:0.###}/{value.b:0.###}/{value.a:0.###}";
    }

    private static string TextureValue(Material material, string property)
    {
        if (!material.HasProperty(property))
            return "n/a";
        Texture value = material.GetTexture(property);
        return value == null ? "none" : value.name;
    }

    private static string GetPath(Transform transform)
    {
        var parts = new List<string>();
        while (transform != null)
        {
            parts.Add(transform.name);
            transform = transform.parent;
        }
        parts.Reverse();
        return string.Join("/", parts);
    }
}
