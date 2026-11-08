# Wrist optics QA after the failed cup run

Recorded run `20261107-185817-e98465` had correct left/right camera role names, but its first wrist images looked back at the arm base and did not show the cup workspace. The previous virtual sensor was flange-local (20,0,-12) cm, looking toward (0,0,-16.96) cm. That large forward offset with a backward aim was a concrete observation defect; matching camera names alone did not verify useful vision.

A bounded static render test compared five stand-off candidates at the same idle and near-grasp poses. Close candidate B was inside/occluded by the CAD wrist. Candidate A hid much of the view behind the motor. Lateral C improved idle coverage but strongly occluded the cup near grasp. High D produced a clear idle overhead view but still hid the cup near the wrist. Diagonal E with 90-degree HFOV clipped the idle cup; its final 110-degree version showed the full idle cup and the near cup/gripper region.

The selected **E110 virtual optics** use flange-local position (14,20,8) cm, aim (10,0,-25) cm and 110-degree HFOV. Camera rotation is formed in the flange frame, with local +Y defining the image-right reference, then composed with the flange quaternion. It no longer changes its relative roll according to the robot pose at setup time. Applied to the 18 wrist cameras of tables T00–T08; overhead cameras and physical meshes remain unchanged.

This 25.7 cm virtual stand-off is explicitly **uncalibrated and not a one-to-one physical camera model**. Some near-contact occlusion remains; visibility does not prove policy success or physical grasp. Selected images are [idle](validation/wrist-framing/E110-idle.jpg), [near grasp](validation/wrist-framing/E110-near.jpg), and the [previous idle view](validation/wrist-framing/previous-idle.jpg).

Task `t-0284` restored the original arm poses after rendering. Measured translation restoration error was zero and all original arm quaternions matched. No props were moved, no simulation ran, and no Dream call occurred during this optical QA. The near rendering pose came from bounded IK at a point 5 cm above the cup and was not a physical pickup.

## Frame contract and remaining verification

Fleet replicas translate without rotation/scaling. Roles map explicitly to LeftWrist/RightWrist/Overview, and the active archived configuration used zero TCP-back offset. The installed model uses the same Rz(pi) Rx(pi/2) mount and official grasp-center point. This source audit found no current left/right swap or extra table rotation.

`maker_arm_wrist_framing.audit_world_tcp` provides an independent numerical read: actual UE flange transform plus official local TCP, minus the replica base, with one Y reflection and centimetres-to-metres conversion, compared with reported-joint FK. The read-only live T00 audit after runtime restoration matched both arms within 5e-10 m: left world TCP (35,-40,91) cm and right (35,40,91) cm both convert to reported (.15,0,.15) m. This excludes a hidden double mount/reflection at the tested pose; it is not a full workspace calibration. See [numeric evidence](validation/wrist-framing/world-tcp-audit.json). Nonzero experimental TCP-back configurations would require matching the compiled IK TCP offset and are not covered by the zero-offset audit.
