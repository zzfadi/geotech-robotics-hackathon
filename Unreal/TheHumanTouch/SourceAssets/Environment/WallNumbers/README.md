# Painted room numbers

Eleven 512×512 RGBA numeral masks, rendered locally with Pillow using macOS Arial Bold at 360 pixels, centered by glyph bounds. Only the number ink is opaque; the background is transparent. No font file is included.

`Tools/maker_arm_wall_numbers.py` imports these textures and builds translucent deferred-decal materials. Each room receives two wall projections plus one floor projection clear of the table. Internal T00–T10 map to visible 1–11. Decal volumes are square, so the original glyph proportions are retained. These are navigation graphics, not equipment or measured venue geometry.
