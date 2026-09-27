using System;
using UnityEditor;
using UnityEngine;

public static class NanaMakeupProfile
{
    private const string MakeupFolder = "Assets/NanaWeb/ImportedMakeup/MAKEUP/";

    public static void Apply(GameObject avatarRoot)
    {
        Material faceMakeup = AssetDatabase.LoadAssetAtPath<Material>(MakeupFolder + "Face.mat");
        Material bodyMakeup = AssetDatabase.LoadAssetAtPath<Material>(MakeupFolder + "Body.mat");
        if (faceMakeup == null || bodyMakeup == null)
            throw new InvalidOperationException("Imported Shinano makeup materials were not found");

        int faceReplaced = 0;
        int bodyReplaced = 0;
        int eyePreserved = 0;
        foreach (Renderer renderer in avatarRoot.GetComponentsInChildren<Renderer>(true))
        {
            Material[] materials = renderer.sharedMaterials;
            bool changed = false;
            for (int slot = 0; slot < materials.Length; slot++)
            {
                Material current = materials[slot];
                string name = current != null ? current.name : string.Empty;
                if (IsEyeMaterial(name))
                {
                    eyePreserved++;
                    continue;
                }
                if (IsFaceMaterial(name))
                {
                    materials[slot] = faceMakeup;
                    faceReplaced++;
                    changed = true;
                }
                else if (IsBodyMaterial(renderer, name))
                {
                    materials[slot] = bodyMakeup;
                    bodyReplaced++;
                    changed = true;
                }
            }
            if (changed)
                renderer.sharedMaterials = materials;
        }

        if (faceReplaced == 0)
            throw new InvalidOperationException("Could not find Shinano face material slot");
        Debug.Log($"NANA_MAKEUP_PROFILE faceSlots={faceReplaced} bodySlots={bodyReplaced} eyeSlotsPreserved={eyePreserved}");
    }

    private static bool IsEyeMaterial(string name)
    {
        string value = (name ?? string.Empty).ToLowerInvariant();
        return value.Contains("su001") || value.Contains("su002") || value.Contains("su003") ||
               value.Contains("su004") || value.Contains("su005") || value.Contains("su006") ||
               value.Contains("su007") || value.Contains("su008") || value.Contains("su009") ||
               value.Contains("su010") || value.Contains("eye");
    }

    private static bool IsFaceMaterial(string name)
    {
        string value = (name ?? string.Empty).ToLowerInvariant();
        return value.Contains("face") && !value.Contains("eye");
    }

    private static bool IsBodyMaterial(Renderer renderer, string name)
    {
        string value = (name ?? string.Empty).ToLowerInvariant();
        string rendererName = (renderer != null ? renderer.name : string.Empty).ToLowerInvariant();
        return value.Contains("body") || rendererName == "body_base";
    }
}
