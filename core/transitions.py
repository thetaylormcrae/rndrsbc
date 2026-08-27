"""
rndrSBC - E-Paper Safe Transitions & Anti-Ghosting Inversion
Applies subtle, e-Paper friendly transitions and hardware clearing passes:
  - 'cut' (Instant switch)
  - 'wipe_horizontal' (Left to right wipe)
  - 'wipe_vertical' (Top to bottom wipe)
  - 'invert_flash' (Brief full black/white waveform flash to eliminate ghosting)
  - 'cross_fade' (Luminance threshold crossfade)
"""

import logging
from typing import Optional
from PIL import Image, ImageChops, ImageOps

logger = logging.getLogger("rndrSBC.transitions")


def apply_transition(
    prev_frame: Optional[Image.Image],
    new_frame: Image.Image,
    transition_type: str = "cut",
    progress: float = 1.0
) -> Image.Image:
    """
    Blends prev_frame and new_frame according to the selected transition type and progress (0.0 to 1.0).
    """
    if prev_frame is None or transition_type == "cut" or progress >= 1.0:
        return new_frame.copy()

    prev_rgb = prev_frame.convert("RGB")
    new_rgb = new_frame.convert("RGB")
    w, h = new_frame.size

    if transition_type == "wipe_horizontal":
        split_x = int(w * progress)
        res = prev_rgb.copy()
        if split_x > 0:
            crop_new = new_rgb.crop((0, 0, split_x, h))
            res.paste(crop_new, (0, 0))
        return res

    elif transition_type == "wipe_vertical":
        split_y = int(h * progress)
        res = prev_rgb.copy()
        if split_y > 0:
            crop_new = new_rgb.crop((0, 0, w, split_y))
            res.paste(crop_new, (0, 0))
        return res

    elif transition_type == "invert_flash":
        # At 50% progress, flashes inverted frame to scrub pigment particles
        if 0.4 <= progress <= 0.6:
            return ImageOps.invert(new_rgb)
        return new_rgb.copy()

    elif transition_type == "cross_fade":
        return Image.blend(prev_rgb, new_rgb, alpha=progress)

    return new_rgb.copy()
