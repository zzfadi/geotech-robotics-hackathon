"""Render a 20-second captioned cut from a feature-locked 12-second take."""
import argparse
from pathlib import Path
import subprocess
import tempfile
from PIL import Image, ImageDraw, ImageFont, ImageOps

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / "assets"
SIZES = {"linkedin": (1080, 1080), "x": (1280, 720), "ig": (1080, 1920)}
parser = argparse.ArgumentParser(__doc__)
parser.add_argument("platform", choices=SIZES)
parser.add_argument("output", type=Path)
parser.add_argument("--hero", type=Path, help="Real footage at least 12 seconds long")
args = parser.parse_args()
w, h = SIZES[args.platform]
if args.output.exists():
    parser.error("Choose a new output filename.")
if args.hero:
    duration = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(args.hero)], text=True)
    if float(duration) < 12:
        parser.error("Use a hero take at least 12 seconds long.")
font = "/System/Library/Fonts/Supplemental/"
def draw_plate(body, caption, artwork=None, overlay=False):
    image = Image.new("RGBA", (w, h), (0, 0, 0, 0) if overlay else "#551F30")
    if artwork:
        art = ImageOps.fit(Image.open(artwork).convert("RGB"), (w, int(h * .70)))
        image.paste(art, (0, 0))
    draw = ImageDraw.Draw(image)
    if body:
        draw.multiline_text((w*.075, h*.32), body, font=ImageFont.truetype(font+"Baskerville.ttc", int(w*.08)), fill="#F2ECE1")
    draw.rectangle((0,h*.74,w,h),fill="#551F30")
    draw.multiline_text((w*.075,h*.78), caption, font=ImageFont.truetype(font+"Arial.ttf", int(w*.043)),fill="#F2ECE1",spacing=10)
    return image
with tempfile.TemporaryDirectory() as tmp:
    d=Path(tmp); parts=[]
    stages=[(3,"","The Human Touch",HERE.parents[2]/"brand-kit-scratchpad/parian-hero-v1/exhibits/01-creation.png"),(5,"Real-arm footage\nplacement","Hold my beer.",None),(7,"Real-arm footage\nplacement","Thanks.",None),(5,"The Human Touch","Bring a human into rehearsal.",None)]
    for i,(sec,body,caption,art) in enumerate(stages):
        real=bool(args.hero and i in (1,2))
        png=d/f"{i}.png"; draw_plate("" if real else body,caption,art,real).save(png)
        cmd=["ffmpeg","-v","error","-y"]
        if real:
            cmd+=["-ss",str(0 if i==1 else 5),"-i",str(args.hero),"-i",str(png),"-filter_complex",f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=0x262521,setsar=1[base];[base][1:v]overlay=0:0"]
        else: cmd+=["-loop","1","-i",str(png)]
        out=d/f"{i}.mp4";cmd+=["-t",str(sec),"-an","-r","24","-pix_fmt","yuv420p","-c:v","libx264","-threads","2","-preset","veryfast",str(out)]
        subprocess.run(cmd,check=True);parts.append(out)
    playlist=d/"list.txt";playlist.write_text("".join(f"file '{p.name}'\n" for p in parts))
    subprocess.run(["ffmpeg","-v","error","-f","concat","-safe","1","-i",str(playlist),"-c","copy","-movflags","+faststart",str(args.output)],check=True)
