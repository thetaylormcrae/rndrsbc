"""
rndrSBC - Color Pipeline & E-Paper Palette Quantizer
Converts RGB canvas output into hardware e-paper color spaces with smart palette snapping and optional dithering.
"""

from PIL import Image

# 7-Color ACeP / Spectra 6 Palette (Black, White, Green, Blue, Red, Yellow, Orange)
PALETTE_7COLOR = [
    0, 0, 0,       # Black
    255, 255, 255, # White
    0, 200, 0,     # Green
    0, 0, 220,     # Blue
    230, 0, 0,     # Red
    255, 220, 0,   # Yellow
    255, 128, 0    # Orange
]

PALETTE_BWR = [
    0, 0, 0,       # Black
    255, 255, 255, # White
    255, 0, 0      # Red
]

PALETTE_BW = [
    0, 0, 0,       # Black
    255, 255, 255  # White
]

def snap_near_white(image: Image.Image, threshold: int = 240) -> Image.Image:
    """Snaps off-white / light gray colors directly to pure white (255, 255, 255) to prevent noisy background dithering."""
    img = image.convert("RGB")
    pixels = img.load()
    w, h = img.size
    for y in range(h):
        for x in range(w):
            r, g, b = pixels[x, y]
            # If all channels are very light (off-white card fills), force pure white
            if r >= threshold and g >= threshold and b >= threshold:
                pixels[x, y] = (255, 255, 255)
    return img

def quantize_image(image: Image.Image, color_mode: str = "rgb", dither: bool = False, snap_white: bool = True) -> Image.Image:
    """
    Transforms an RGB image into the target hardware color space.
    Modes: 'rgb', '7color', 'bwr', 'bw'
    """
    if color_mode == "rgb" or not color_mode:
        return image.convert("RGB")

    # Clean up dirty off-whites first
    processed = snap_near_white(image) if snap_white else image

    palette_map = {
        "7color": PALETTE_7COLOR,
        "bwr": PALETTE_BWR,
        "bw": PALETTE_BW
    }

    raw_palette = palette_map.get(color_mode.lower(), PALETTE_BW)
    padded_palette = raw_palette + [0] * (768 - len(raw_palette))
    
    pal_img = Image.new("P", (1, 1))
    pal_img.putpalette(padded_palette)

    dither_mode = Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE
    quantized = processed.convert("RGB").quantize(palette=pal_img, dither=dither_mode)
    
    return quantized.convert("RGB")
