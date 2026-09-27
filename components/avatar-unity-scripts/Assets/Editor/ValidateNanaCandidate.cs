using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class ValidateNanaCandidate
{
    public static void Run()
    {
        const string prefabPath = "Assets/Nana/Prefabs/NanaAvatar_TargetDriven.prefab";
        const string scenePath = "Assets/Nana/Scenes/NanaAvatar_TargetDriven_Test.unity";

        var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
        if (prefab == null)
        {
            Debug.LogError("FAIL candidate prefab did not import");
            EditorApplication.Exit(1);
            return;
        }

        GameObject contents = null;
        try
        {
            contents = PrefabUtility.LoadPrefabContents(prefabPath);
            var animator = contents.GetComponentInChildren<Animator>(true);
            if (animator == null || animator.runtimeAnimatorController == null ||
                animator.runtimeAnimatorController.name != "Nana_TargetDriven")
            {
                Debug.LogError("FAIL candidate prefab does not use Nana_TargetDriven controller");
                EditorApplication.Exit(1);
                return;
            }
        }
        finally
        {
            if (contents != null)
                PrefabUtility.UnloadPrefabContents(contents);
        }

        var scene = EditorSceneManager.OpenScene(scenePath, OpenSceneMode.Single);
        var controller = Object.FindObjectOfType<NanaTargetDrivenAvatarController>();
        if (controller == null)
        {
            Debug.LogError("FAIL target-driven test scene is missing runtime controller");
            EditorApplication.Exit(1);
            return;
        }

        Debug.Log("PASS candidate prefab and test scene import cleanly");
        Debug.Log("Scene loaded: " + scene.path + " | controller=" + controller.name);
        EditorApplication.Exit(0);
    }
}
