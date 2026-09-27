using System;
using System.Collections.Generic;
using UnityEditor;
using UnityEngine;

public static class NanaFaceCatalogBuild
{
    public const string CatalogPath = "Assets/Nana/Expressions/seven_faces_v1/seven_faces.json";

    public static TextAsset LoadAndValidate(params SkinnedMeshRenderer[] faces)
    {
        var source = AssetDatabase.LoadAssetAtPath<TextAsset>(CatalogPath);
        if (source == null) throw new InvalidOperationException("Missing seven-face catalog");
        var catalog = JsonUtility.FromJson<NanaAvatarEventController.FacePresetCatalog>(source.text);
        if (catalog == null || catalog.id != "seven_faces_v1" || catalog.presets == null || catalog.presets.Length != 7)
            throw new InvalidOperationException("Expected seven_faces_v1 catalog");
        var ids = new HashSet<string>();
        foreach (var preset in catalog.presets)
        {
            if (preset == null || !NanaAvatarEventController.IsCatalogExpression(preset.action) ||
                preset.id != preset.action || !ids.Add(preset.id) ||
                !Finite(preset.enter) || !Finite(preset.exit) || !Finite(preset.duration) ||
                preset.enter <= 0f || preset.enter > 5f || preset.exit <= 0f || preset.exit > 1f ||
                Mathf.Abs(preset.duration - preset.enter - preset.exit - 1f) > .001f ||
                preset.channels == null || preset.channels.Length == 0 || preset.channels.Length > 32 ||
                !Finite(preset.head.x) || !Finite(preset.head.y) || !Finite(preset.head.z) ||
                Mathf.Abs(preset.head.x) > 15f || Mathf.Abs(preset.head.y) > 30f || Mathf.Abs(preset.head.z) > 12f)
                throw new InvalidOperationException("Invalid face preset: " + preset?.id);
            var channels = new HashSet<string>();
            foreach (var channel in preset.channels)
            {
                if (channel == null || string.IsNullOrWhiteSpace(channel.shape) || !channels.Add(channel.shape) ||
                    Reserved(channel.shape) || !Finite(channel.weight) || channel.weight <= 0f || channel.weight > 100f ||
                    !Finite(channel.delay) || channel.delay < 0f || channel.delay >= 1f)
                    throw new InvalidOperationException("Invalid/reserved face channel: " + channel?.shape);
                foreach (var face in faces)
                    if (face == null || face.sharedMesh == null || face.sharedMesh.GetBlendShapeIndex(channel.shape) < 0)
                        throw new InvalidOperationException("Missing shape on model: " + channel.shape);
            }
        }
        Debug.Log("NANA_PRODUCTION_FACE_CATALOG_VALID presets=7 models=" + faces.Length);
        return source;
    }

    private static bool Reserved(string shape) => shape.StartsWith("eye_close", StringComparison.Ordinal) ||
        shape.StartsWith("eye_look_", StringComparison.Ordinal) || shape.StartsWith("vrc.v_", StringComparison.Ordinal) ||
        shape == "mouth_a1" || shape == "mouth_i1" || shape == "mouth_o1" || shape == "mouth_0";
    private static bool Finite(float value) => !float.IsNaN(value) && !float.IsInfinity(value);
}
