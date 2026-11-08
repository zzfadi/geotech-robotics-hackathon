"""Unreal/URDF Maker Arm model <-> MakerMods SDK motor frame, plus Table 10 kinematics helpers.

Pure Python (no ``unreal`` import) so the editor link and the SDK bridge share one mapping.

Joint mapping: the SDK's gravity model (maker-arm-sdk ``maker_arm/gravity.py`` at 0245316) states
its chain is "the URDF's joint origins ... with the wrist-yaw axis flipped: raw motor sign", and
the SDK profile zeroes the motors in the folded pose, which is the URDF zero. So a motor angle
equals the URDF angle for every arm joint except wrist yaw, whose sign is inverted. Both URDFs
(SDK and SourceAssets/MakerArm) are identical at that revision.

Gripper: the SDK gripper runs from 0 deg (fully closed) to -96 deg (open bound); the model's
jaw gap runs 0..104.825 mm. The linkage between those endpoints is unmeasured upstream, so the
gap mapping is a linear approximation. The six arm joints are exact.
"""

import math

from maker_arm_model import MODEL, mounted_frames
from yam_kinematics import ue_pose
import xml.etree.ElementTree as ET

JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_yaw", "wrist_roll")
SIGN = (1, 1, 1, 1, -1, 1)
CHAIN = ("base_link", "link_002", "link_003", "link_004", "link_005", "link_006", "link_007")

# SDK profile maker_arm_v1.yaml, motor degrees (after zeroing in the folded pose).
MOTOR_LIMITS_DEG = ((-158.2, 156.1), (-175.3, -3.2), (2.8, 236.1),
                    (-65.2, 104.0), (-97.6, 78.0), (-153.2, 152.0))
GRIPPER_LIMITS_DEG = (-96.0, -2.5)
GRIPPER_CLOSED_DEG = 0.0
GRIPPER_OPEN_DEG = -96.0
JAW_MAX_M = 0.104825


def _urdf_joints():
    root = ET.parse(MODEL).getroot()
    out = []
    for j in root.findall("joint"):
        if j.get("type") == "revolute":
            lim = j.find("limit")
            out.append((j.get("name"), j.find("parent").get("link"), j.find("child").get("link"),
                        j.find("origin"), [float(v) for v in j.find("axis").get("xyz").split()],
                        (float(lim.get("lower")), float(lim.get("upper")))))
    return out


_JOINTS = _urdf_joints()
URDF_LIMITS = tuple(j[5] for j in _JOINTS)


def urdf_to_motor(q):
    """Six URDF radians -> six SDK joint radians (motor frame)."""
    return [s * v for s, v in zip(SIGN, q)]


def motor_to_urdf(m):
    return [s * v for s, v in zip(SIGN, m)]


def drive_limits(margin_deg=1.0):
    """URDF-frame limits valid for both the model and the real motors, shrunk by a margin."""
    out = []
    margin = math.radians(margin_deg)
    for (ulo, uhi), (mlo, mhi), s in zip(URDF_LIMITS, MOTOR_LIMITS_DEG, SIGN):
        mlo, mhi = math.radians(mlo), math.radians(mhi)
        if s < 0:
            mlo, mhi = -mhi, -mlo
        lo, hi = max(ulo, mlo) + margin, min(uhi, mhi) - margin
        if lo >= hi:
            raise ValueError("empty joint range")
        out.append((lo, hi))
    return tuple(out)


def clamp(q, limits):
    return [min(hi, max(lo, v)) for v, (lo, hi) in zip(q, limits)]


def gap_to_gripper(gap_m):
    """Model jaw gap (m) -> SDK gripper joint radians. Linear approximation (see module doc)."""
    frac = min(1.0, max(0.0, gap_m / JAW_MAX_M))
    return math.radians(GRIPPER_CLOSED_DEG + frac * (GRIPPER_OPEN_DEG - GRIPPER_CLOSED_DEG))


def gripper_to_gap(rad):
    frac = (math.degrees(rad) - GRIPPER_CLOSED_DEG) / (GRIPPER_OPEN_DEG - GRIPPER_CLOSED_DEG)
    return JAW_MAX_M * min(1.0, max(0.0, frac))


# ---- rotations in the Unreal convention used by yam_kinematics.ue_pose -----------------------
S = (1, -1, 1)


def quat_to_matrix(q):
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    return [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]


def _t(m):
    return [[m[j][i] for j in range(3)] for i in range(3)]


def _mm(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _rot_vector(m):
    """Axis-angle vector (rad) of a 3x3 rotation."""
    c = max(-1.0, min(1.0, (m[0][0] + m[1][1] + m[2][2] - 1) / 2))
    angle = math.acos(c)
    v = [m[2][1] - m[1][2], m[0][2] - m[2][0], m[1][0] - m[0][1]]
    s = math.sqrt(sum(x * x for x in v))
    if s < 1e-9:
        return [0.0, 0.0, 0.0]
    return [angle * x / s for x in v]


def fk_ue(q, gap, yaw, base):
    """Every visual link's Unreal pose: {link: (position_cm, quat_xyzw)}."""
    out = {}
    for link, t in mounted_frames(q, gap, yaw=yaw).items():
        p, r = ue_pose(t)
        out[link] = ([p[i] + base[i] for i in range(3)], r)
    return out


def extract(poses, yaw, base, gap_default=0.0):
    """Recover (q, gap, residual_cm) from Unreal link poses {link: (p_cm, quat)}.

    Exact for an arm posed by forward kinematics; ``residual_cm`` is the worst link position
    difference between the given poses and FK of the recovered joints (large = not a FK pose).
    """
    from yam_kinematics import origin as urdf_origin
    q = []
    for name, parent, child, origin_el, axis, _ in _JOINTS:
        rp, rc = quat_to_matrix(poses[parent][1]), quat_to_matrix(poses[child][1])
        rel = _mm(_t(rp), rc)
        rel = [[S[i] * rel[i][j] * S[j] for j in range(3)] for i in range(3)]
        o = urdf_origin(origin_el)
        o_r = [row[:3] for row in o[:3]]
        m = _mm(_t(o_r), rel)
        angle = math.atan2(m[1][0], m[0][0])
        q.append(angle if axis[2] > 0 else -angle)
    gap = gap_default
    if "gripper_left" in poses and "link_007" in poses:
        r7 = quat_to_matrix(poses["link_007"][1])
        d = [(poses["gripper_left"][0][i] - poses["link_007"][0][i]) / 100.0 for i in range(3)]
        local = [S[i] * sum(r7[k][i] * d[k] for k in range(3)) for i in range(3)]
        gap = max(0.0, min(JAW_MAX_M, 2 * local[1]))
    q = clamp(q, URDF_LIMITS)
    fk = fk_ue(q, gap, yaw, base)
    residual = max(math.dist(fk[link][0], poses[link][0]) for link in poses if link in fk)
    return q, gap, residual


def _solve(a, b):
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        if abs(m[c][c]) < 1e-12:
            raise ZeroDivisionError("singular")
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    return [m[i][n] / m[i][i] for i in range(n)]


def _ee(q, gap, yaw, base):
    p, r = fk_ue(q, gap, yaw, base)["link_007"]
    return p, quat_to_matrix(r)


def _error(q, gap, yaw, base, target_p, target_r, w_rot):
    p, r = _ee(q, gap, yaw, base)
    e_pos = [(target_p[i] - p[i]) / 100.0 for i in range(3)]
    e_rot = _rot_vector(_mm(target_r, _t(r)))
    return e_pos + [w_rot * v for v in e_rot], e_pos, e_rot


def ik(q0, gap, yaw, base, target_p_cm, target_quat, limits, iterations=15, w_rot=0.05,
       damping=0.02, max_step=0.15):
    """Damped least squares for link_007 to an Unreal pose. Returns (q, pos_err_mm, rot_err_deg)."""
    q = clamp(list(q0), limits)
    target_r = quat_to_matrix(target_quat)
    h = 1e-4
    e, ep, er = _error(q, gap, yaw, base, target_p_cm, target_r, w_rot)
    for _ in range(iterations):
        if math.sqrt(sum(v * v for v in ep)) < 3e-4 and math.sqrt(sum(v * v for v in er)) < math.radians(0.3):
            break
        cols = []
        for j in range(6):
            dq = list(q)
            dq[j] += h
            e2, _, _ = _error(dq, gap, yaw, base, target_p_cm, target_r, w_rot)
            cols.append([(e[i] - e2[i]) / h for i in range(6)])  # d(pose)/dq = -d(error)/dq
        jac = [[cols[j][i] for j in range(6)] for i in range(6)]
        jjt = [[sum(jac[i][k] * jac[r][k] for k in range(6)) + (damping ** 2 if i == r else 0.0)
                for r in range(6)] for i in range(6)]
        try:
            y = _solve(jjt, e)
        except ZeroDivisionError:
            break
        step = [sum(jac[i][j] * y[i] for i in range(6)) for j in range(6)]
        biggest = max(abs(v) for v in step)
        if biggest > max_step:
            step = [v * max_step / biggest for v in step]
        q = clamp([q[j] + step[j] for j in range(6)], limits)
        e, ep, er = _error(q, gap, yaw, base, target_p_cm, target_r, w_rot)
    return q, 1000 * math.sqrt(sum(v * v for v in ep)), math.degrees(math.sqrt(sum(v * v for v in er)))
