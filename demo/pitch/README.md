# The Human Touch · presentation art

[Review contact sheet](asset-contact-sheet.jpg) · [Play the museum sequence](museum-reel.mp4) · [Brand guide](../../brand-kit-final/identity/brand-guide.pdf) · [Emmy turnaround](../../brand-kit-final/emmy/README.md)

## Media cues

| Moment | Presentation asset | Use |
| --- | --- | --- |
| Opening | [Fingertip pullback](films/01-creation-reveal.mp4) | Start tight. Reveal the painted war. Ask “How can we avoid this future?” |
| Hold my beer | [Title card](assets/title.jpg) | Use this stage hold while the speaker addresses the physical arm. Insert the real take after feature lock. |
| Why people matter | [Recognizing a human cue](assets/problem.jpg) | The painted woman is crying. Emmy offers a folded cloth. Use the scene to explain what an isolated rehearsal misses. |
| Live co-presence | [System design](assets/architecture.jpg) | Webcam or Quest supplies human input. Unreal and Isaac are the rehearsal architecture. Dream supplies simulated-arm actions. Codex and the SDK drive the physical Maker Arm. Live Isaac integration is a design goal. |
| Evidence | [Observed runs](assets/numbers.jpg) | Ten parallel simulations. Eighty-four Dream attempts in 170 seconds. The physical right arm picked up a cylinder, lifted it, placed it and parked in 16 seconds. These are distinct observations. |
| Museum exhibits | [Framed supper](assets/gallery-02-supper.jpg), [kerchief](assets/gallery-03-kerchief.jpg), [photo](assets/gallery-04-photo-gallery.jpg) | Emmy appears inside each work. The same thin gilt frame and strip of limestone wall remain visible during each gesture. |
| What comes next | [The Milkmaid](assets/next-card.jpg) | Discuss assisted intent, which would help smooth human input, inviting more remote participants, and testing transfer of rehearsed skills on the real arm. |
| Close | [Partner strip](assets/partners.jpg) · [QR end card](assets/end-card.jpg) | The code opens the public project repository. |
| Social | [Thumbnail](assets/social-thumbnail.jpg) · [Templates](social/) | Three 20-second captioned layout previews with a replaceable real-footage interval. |

## Motion

The 30-second presentation reel visits five normally hung exhibits. Each silent six-second clip keeps the frame around the moving work. The mural holds the fingertip gesture, then Emmy offers bread, extends a kerchief, offers a light and steadies a bowl. The painting occupies 88% of the width and height of each shot. These are locally authored camera and limited 2D animations. An optional seven-second tight finger pullback is supplied separately for the original prologue.

Higgsfield did not submit a character-animation job. Seedance required Plus, and Wan reported insufficient Starter credits. The image-to-video cues are in [motion-brief.md](motion-brief.md) for a future render.

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
