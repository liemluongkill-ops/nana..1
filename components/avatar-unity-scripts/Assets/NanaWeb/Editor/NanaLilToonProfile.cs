using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using UnityEditor;
using UnityEngine;

// Specialize the installed lilToon source; its lighting and material functions stay intact.
public static class NanaLilToonProfile
{
    private const string Root = "Assets/NanaWeb/Generated/Fidelity";
    private const string Includes = "Packages/jp.lilxyzw.liltoon/Shader/Includes/";
    private static readonly Regex Feature = new Regex(@"(?m)^\s*#define LIL_(?:FEATURE|OPTIMIZE)_\w+[^\r\n]*\r?$");
    private static readonly Regex UsePass = new Regex("UsePass\\s+\"([^\"]+)\"");

    public static void Apply(GameObject avatar) => Apply(new[] { avatar });

    public static void Apply(GameObject[] avatars)
    {
        Directory.CreateDirectory(Root);
        AssetDatabase.Refresh();
        var materials = avatars.SelectMany(a => a.GetComponentsInChildren<Renderer>(true))
            .SelectMany(r => r.sharedMaterials).Where(m => m != null && m.shader.name.Contains("lilToon"))
            .Distinct().ToArray();
        var clips = avatars.SelectMany(a => a.GetComponentsInChildren<Animator>(true))
            .Where(a => a.runtimeAnimatorController != null)
            .SelectMany(a => a.runtimeAnimatorController.animationClips).Distinct().ToArray();
        var replacements = new Dictionary<Material, Material>();
        foreach (Material original in materials)
            replacements.Add(original, Specialize(original, clips));
        foreach (Renderer renderer in avatars.SelectMany(a => a.GetComponentsInChildren<Renderer>(true)))
            renderer.sharedMaterials = renderer.sharedMaterials
                .Select(m => m != null && replacements.TryGetValue(m, out Material replacement) ? replacement : m)
                .ToArray();
        AssetDatabase.SaveAssets();
        Debug.Log($"NANA_LILTOON_PROFILE materials={replacements.Count} clips={clips.Length}");
    }

    private static Material Specialize(Material original, AnimationClip[] clips)
    {
        var source = new Material(original);
        if (source.shader.name.Contains("Multi"))
        {
            if (source.GetFloat("_TransparentMode") != 0)
                throw new InvalidOperationException("Non-opaque Multi material needs explicit conversion: " + original.name);
            source.shader = Shader.Find(source.shader.name.Contains("Outline") ? "Hidden/lilToonOutline" : "lilToon");
            source.shaderKeywords = Array.Empty<string>();
        }

        string id = AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(original));
        if (string.IsNullOrEmpty(id)) throw new InvalidOperationException("Material has no persistent GUID: " + original.name);
        string directory = Root + "/" + id;
        Directory.CreateDirectory(directory);

        // The package optimizer retains properties referenced by clips instead of baking them to constants.
        Type settings = AppDomain.CurrentDomain.GetAssemblies()
            .Select(a => a.GetType("lilToonSetting")).FirstOrDefault(t => t != null);
        MethodInfo optimize = settings?.GetMethod("GetOptimizedSetting", BindingFlags.Static | BindingFlags.NonPublic);
        if (optimize == null) throw new MissingMethodException("Installed lilToon GetOptimizedSetting is unavailable");
        object[] arguments = { new[] { source }, clips, null, null, null };
        optimize.Invoke(null, arguments);
        string constants = arguments[3] as string;
        string features = arguments[4] as string;
        if (string.IsNullOrWhiteSpace(constants) || string.IsNullOrWhiteSpace(features))
            throw new InvalidOperationException("lilToon optimizer returned an empty profile: " + original.name);
        // These are container-importer directives, not HLSL pragmas. Keep the standard
        // multi_compile variants in these standalone copies instead of passing them to Unity.
        features = Regex.Replace(features, @"(?m)^#pragma lil_skip_variants_\w+[^\r\n]*\r?$", "");
        string inputPath = directory + "/input.hlsl";
        File.WriteAllText(inputPath, constants, new UTF8Encoding(false));

        var generated = new Dictionary<string, string>();
        string shaderName = GenerateShader(source.shader, id, directory, inputPath, features, generated);
        AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
        Shader shader = Shader.Find(shaderName);
        if (shader == null || ShaderUtil.ShaderHasError(shader))
            throw new InvalidOperationException("Specialized shader failed: " + shaderName);
        string materialPath = directory + "/material.mat";
        Material material = AssetDatabase.LoadAssetAtPath<Material>(materialPath);
        if (material == null)
        {
            material = new Material(source);
            AssetDatabase.CreateAsset(material, materialPath);
        }
        else
        {
            EditorUtility.CopySerialized(source, material);
        }
        material.name = original.name + " (lilToon WebGL)";
        material.shader = shader;
        EditorUtility.SetDirty(material);
        Debug.Log($"NANA_LILTOON_MATERIAL name={original.name} source={original.shader.name} specialized={shaderName} " +
                  $"features={Regex.Matches(features, @"#define LIL_FEATURE_").Count} shaders={generated.Count}");
        UnityEngine.Object.DestroyImmediate(source);
        return material;
    }

    private static string GenerateShader(Shader source, string id, string directory, string inputPath,
        string features, Dictionary<string, string> generated)
    {
        if (generated.TryGetValue(source.name, out string known)) return known;
        string path = AssetDatabase.GetAssetPath(source);
        string name = "Nana/Fidelity/" + id + "/" + Path.GetFileNameWithoutExtension(path);
        generated.Add(source.name, name);
        string text = File.ReadAllText(path);
        text = Regex.Replace(text, "^Shader\\s+\"[^\"]+\"", "Shader \"" + name + "\"");
        text = UsePass.Replace(text, match =>
        {
            string qualified = match.Groups[1].Value;
            int slash = qualified.LastIndexOf('/');
            Shader dependency = Shader.Find(qualified.Substring(0, slash));
            if (dependency == null) throw new InvalidOperationException("Missing UsePass: " + qualified);
            string dependencyName = GenerateShader(dependency, id, directory, inputPath, features, generated);
            return "UsePass \"" + dependencyName + qualified.Substring(slash) + "\"";
        });
        if (Feature.IsMatch(text))
        {
            text = Feature.Replace(text, "");
            text = new Regex("HLSLINCLUDE").Replace(text, "HLSLINCLUDE\n" + features, 1);
        }
        text = text.Replace("\"Includes/", "\"" + Includes);
        string pipeline = "#include \"" + Includes + "lil_pipeline_brp.hlsl\"";
        text = Regex.Replace(text, @"(?m)^[ \t]*" + Regex.Escape(pipeline), match =>
            match.Value + "\nCBUFFER_START(UnityPerMaterial)\n#include \"" +
            inputPath + "\"\nCBUFFER_END\n");
        // A failed pass should be visible during verification, never silently rendered unlit.
        text = Regex.Replace(text, "Fallback\\s+\"[^\"]+\"", "Fallback Off");
        string output = directory + "/" + Path.GetFileName(path);
        File.WriteAllText(output, text, new UTF8Encoding(false));
        return name;
    }
}
