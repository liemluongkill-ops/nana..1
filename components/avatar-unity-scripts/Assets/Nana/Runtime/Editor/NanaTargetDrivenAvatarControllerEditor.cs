using UnityEditor;
using UnityEngine;

[CustomEditor(typeof(NanaTargetDrivenAvatarController))]
public sealed class NanaTargetDrivenAvatarControllerEditor : Editor
{
    public override void OnInspectorGUI()
    {
        DrawDefaultInspector();

        NanaTargetDrivenAvatarController controller =
            (NanaTargetDrivenAvatarController)target;

        EditorGUILayout.Space(8f);
        EditorGUILayout.LabelField("Runtime test", EditorStyles.boldLabel);
        EditorGUILayout.HelpBox(
            Application.isPlaying
                ? "Commands below start from the current controller pose."
                : "Enter Play Mode before sending a target.",
            MessageType.Info);

        using (new EditorGUI.DisabledScope(!Application.isPlaying))
        {
            EditorGUILayout.BeginHorizontal();
            if (GUILayout.Button("Look Left"))
                controller.LookLeft();
            if (GUILayout.Button("Look Right"))
                controller.LookRight();
            if (GUILayout.Button("Center"))
                controller.LookCenter();
            EditorGUILayout.EndHorizontal();

            if (GUILayout.Button("Retarget Test: Left -> Right -> Center"))
                controller.StartCoroutine(RetargetTest(controller));
        }

        EditorGUILayout.Space(4f);
        EditorGUILayout.LabelField(
            "Phase: " + controller.CurrentPhase + " | Rig ready: " + controller.RigReady,
            EditorStyles.miniLabel);
    }

    private static System.Collections.IEnumerator RetargetTest(
        NanaTargetDrivenAvatarController controller)
    {
        controller.RequestLook(
            new NanaTargetDrivenAvatarController.LookTarget(-24f, -3f, 2f),
            0.8f,
            0.7f,
            0.8f,
            true);
        yield return new WaitForSecondsRealtime(0.35f);
        controller.RequestLook(
            new NanaTargetDrivenAvatarController.LookTarget(24f, -2f, -2f),
            0.55f,
            0.6f,
            0.7f,
            true);
        yield return new WaitForSecondsRealtime(1.1f);
        controller.LookCenter();
    }
}
