"""Small portrait gestures shared by idle and speech; no lifecycle side effects."""
import math
import random
import unreal


def head_pose(seconds):
    phase = 2 * math.pi * seconds / 60
    return {
        'head_ctrl.Rotation.X': 1.2 * math.sin(3 * phase) + .3 * math.sin(7 * phase),
        'head_ctrl.Rotation.Y': 1.8 * math.sin(5 * phase) + .7 * math.sin(11 * phase),
        'head_ctrl.Rotation.Z': 3.2 * math.sin(2 * phase) + 1.1 * math.sin(7 * phase),
    }


def add_head_keys(channels, final, fps=60, return_to_neutral=False):
    for frame in sorted(set(range(0, final + 1, 10)) | {final}):
        # Ease speech gestures in/out so handing back to idle does not snap.
        envelope = min(1., frame / (fps * .6), (final-frame) / (fps * .6)) if return_to_neutral else 1.
        for name, value in head_pose(frame / fps).items():
            channels[name].add_key(unreal.FrameNumber(frame), value * max(0., envelope),
                                   interpolation=unreal.MovieSceneKeyInterpolation.LINEAR)


def add_face_keys(channels, final, fps=60):
    duration = final / fps
    rng = random.Random(41)
    times, t = [], 2.7
    while t < duration - .3:
        times.append(t)
        t += rng.uniform(2.8, 5.8)
    for name in ('CTRL_L_eye_blink', 'CTRL_R_eye_blink'):
        channel = channels[name]
        channel.add_key(unreal.FrameNumber(0), 0.)
        channel.add_key(unreal.FrameNumber(final), 0.)
        for t in times:
            for offset, value in ((-.08, 0.), (0., .97), (.045, .97), (.16, 0.)):
                channel.add_key(unreal.FrameNumber(round((t+offset)*fps)), value,
                                interpolation=unreal.MovieSceneKeyInterpolation.LINEAR)
    for frame in sorted(set(range(0, final + 1, 15)) | {final}):
        phase = 2 * math.pi * frame / fps / 60
        envelope = min(1., frame / fps, (final-frame) / fps)
        values = {'CTRL_C_eye.X': .025 * math.sin(4*phase),
                  'CTRL_C_eye.Y': .015 * math.sin(7*phase),
                  'CTRL_L_brow_raiseIn': .035 * (1 + math.sin(3*phase)),
                  'CTRL_R_brow_raiseIn': .03 * (1 + math.sin(3*phase + .3))}
        for name, value in values.items():
            if name in channels:
                channels[name].add_key(unreal.FrameNumber(frame), value * max(0., envelope),
                                       interpolation=unreal.MovieSceneKeyInterpolation.LINEAR)
    return times
