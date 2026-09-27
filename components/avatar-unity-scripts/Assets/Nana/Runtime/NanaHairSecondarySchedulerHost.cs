using UnityEngine;
using VRC.Dynamics;

// Lab candidate host. It is a sibling of the controller so owner lifecycle tests
// can disable the controller without leaving queued PhysBone removals behind.
[DefaultExecutionOrder(1610)]
public sealed class NanaHairSecondarySchedulerHost : MonoBehaviour
{
    public NanaHairSecondaryMotionController controller;

    private void LateUpdate()
    {
        if (controller != null && controller.isActiveAndEnabled) return;
        if (PhysBoneManager.Inst != null)
            VRCDynamicsScheduler.UpdateConstraints(true);
    }
}
