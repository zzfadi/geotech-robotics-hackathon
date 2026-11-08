# The Human Touch · presentation art

[Open the native Keynote presentation](deck/The%20Human%20Touch%20%E2%80%94%20Judges.key) · [Slide proof](deck/The%20Human%20Touch%20%E2%80%94%20Judges.pdf) · [Timed outline and notes](deck/judges-script.md)

[Review the asset library](asset-contact-sheet.jpg) · [Play the Higgsfield museum sequence](films/higgsfield/museum-sequence.mp4) · [Opening and ending concepts](keyframes/pairs.jpg) · [Brand guide](../../brand-kit-final/identity/brand-guide.pdf) · [Emmy turnaround](../../brand-kit-final/emmy/README.md)

## Media cues

| Moment | Presentation asset | Use |
| --- | --- | --- |
| Opening | [Fingertip pullback](films/higgsfield/opening.mp4) | Start tight. Reveal the painted war. Ask “How can we avoid this future?” |
| Hold my beer | [Title card](assets/title.jpg) | Use this stage hold while the speaker addresses the physical arm. Insert the real take after feature lock. |
| Why people matter | [Alone and with a person](assets/problem-comparison.jpg) | In the empty Vermeer kitchen the bowl stays still. When the milkmaid begins pouring, Emmy has a reason to steady it. |
| Live co-presence | [System design](assets/architecture.jpg) | Webcam or Quest supplies human input. Unreal and Isaac are the rehearsal architecture. Dream supplies simulated-arm actions. Codex and the SDK drive the physical Maker Arm. Live Isaac integration is a design goal. |
| Evidence | [Observed runs](assets/numbers.jpg) | Ten parallel simulations. Eighty-four Dream attempts in 170 seconds. The physical right arm picked up a cylinder, lifted it, placed it and parked in 16 seconds. These are distinct observations. |
| Museum exhibits | [Framed supper](assets/gallery-02-supper.jpg), [kerchief](assets/gallery-03-kerchief.jpg), [photo](assets/gallery-04-photo-gallery.jpg) | Emmy appears inside each work. The same thin gilt frame and strip of limestone wall remain visible during each gesture. |
| What comes next | [The Milkmaid](assets/next-card.jpg) | Discuss assisted intent, which would help smooth human input, inviting more remote participants, and testing transfer of rehearsed skills on the real arm. |
| Close | [Partner strip](assets/partners.jpg) · [QR end card](assets/end-card.jpg) | The code opens the public project repository. |
| Social | [Thumbnail](assets/social-thumbnail.jpg) · [Templates](social/) | Three 20-second captioned layout previews with a replaceable real-footage interval. |

## Motion

The stage sequence runs 31 seconds. The opening pulls back from the finger gap inside the mounted frame. Each subsequent five-second Higgsfield source was edited into a six-second stage clip, with separate timing for the painted or photographed subject and Emmy. Her response begins after the human cue. The architectural surround stays fixed. Each exhibit has its own movie slide and can be advanced independently in Keynote.

[View the five-cut timing proof](films/higgsfield/motion-review.jpg). The original 30-second local animatic remains available as [a rehearsal alternative](museum-reel.mp4). The stage cut uses the apron version of Emmy in the Vermeer kitchen. The [concept direction](keyframes/REVIEW.md) and [motion cues](motion-brief.md) document the approved action and stopping point.

## Real footage after feature lock

Capture the hero in horizontal and vertical formats, missed grabs, wrist POV, a clean parallel-sim run and a 3×3 still, matched webcam/avatar views, Quest passthrough, the team photo and full backup performance. Include the fist bump, thinking overlay and voice audio only if the features are in the locked demo. Keep the on-site footage distinct from the educational museum illustrations.

To replace the social template interval, select a continuous take at least 12 seconds long. The silent assembly includes a 3 s opener, 12 s captioned real action and 5 s close. For example:

    uv run --with pillow python demo/pitch/social/render.py ig hero-vertical.mp4 --hero take.mov

Use the platform names linkedin (square), x (landscape) and ig (vertical). The preview files identify the missing footage on screen. Align final captions and production sound to the selected performance.

## Asset sources

The limestone gallery is an Imagegen original. The new Vermeer study uses [The Milkmaid](https://commons.wikimedia.org/wiki/File:Johannes_Vermeer_-_Het_melkmeisje_-_Google_Art_Project.jpg) as its source. The [earlier exhibit library](../../brand-kit-scratchpad/parian-hero-v1/exhibits/README.md) records the underlying artworks. All framed views use the same gallery photograph. The source sponsor artwork is under [partners](assets/partners/).

- [MakerMods](https://www.makermods.ai/assets/Logos/makermods-lockup-black.svg) — official horizontal lockup.
- [Dream Labs](https://dreamlabs.ai/brand/icon-cloud.svg) — official cloud mark.
- [LiveKit](https://livekit.io/images/livekit-safari-pinned-tab.svg) — official monochrome symbol.
- GeoTech — official horizontal SVG from the GeoCRM brand library.
