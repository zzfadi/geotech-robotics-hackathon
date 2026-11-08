"""Place one synthetic webcam performer in the Maker Arm's simulation coordinates."""


SUBJECT = "Human_Cam_B"
CYCLE = 150
CLEARANCE = 0.22


def hand_sweep(seq, center):
    from rehearse_performers import skeleton

    # A hold clear of the wrist, an approach, a hold, then withdrawal to clear.
    phase = seq % CYCLE
    fraction = max(0.0, min(1.0, (phase - 40) / 35, (125 - phase) / 35))
    bones = skeleton(SUBJECT, "neutral")
    hand = next(r for r in bones if r[0] == "left_hand")
    x, y, z = center
    # Encode simulation (+forward, +left, +up) in XR source axes.
    offset = (-(y - CLEARANCE) - hand[2], z - hand[3], -x - hand[4])
    for row in bones:
        row[2:5] = [a + b for a, b in zip(row[2:5], offset)]
        if row[0] in ("left_hand", "left_thumb", "left_index", "left_pinky"):
            row[2] -= CLEARANCE * fraction
        elif row[0] == "left_forearm":
            row[2] -= CLEARANCE * fraction / 2
    return bones
