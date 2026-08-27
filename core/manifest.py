"""
rndrSBC - Declarative Manifest UI Compiler
Compiles declarative JSON/dict UI trees into pixel-perfect e-paper renders using pure Python.
Zero C/Rust dependencies, ultra-fast recursion.
"""

import os
from PIL import Image
from core.canvas import ResponsiveCanvas, Rect

class ManifestCompiler:
    def __init__(self, canvas: ResponsiveCanvas):
        self.canvas = canvas

    def render(self, node: dict, rect: Rect = None):
        if rect is None:
            rect = self.canvas.bounds

        node_type = node.get("type", "box").lower()

        # 1. Container / Box / Card
        if node_type in ["box", "card"]:
            self._render_box(node, rect)
        
        # 2. Text Elements
        elif node_type in ["h1", "h2", "h3", "p", "text", "small"]:
            self._render_text(node, rect, node_type)

        # 3. Icon Element
        elif node_type == "icon":
            self._render_icon(node, rect)

    def _render_box(self, node: dict, rect: Rect):
        # Draw background/outline if it's a card
        if node.get("type", "").lower() == "card":
            fill = node.get("fill", "#ffffff")
            outline = node.get("outline", "#000000")
            radius = node.get("radius", 8)
            self.canvas.draw_card(rect, radius=radius, fill=fill, outline=outline, width=1)

        children = node.get("children", [])
        if not children:
            return

        direction = node.get("dir", "COLUMN").upper()
        padding = node.get("padding", 0)
        gap = node.get("gap", 0)
        
        # Apply padding
        padded_rect = rect.inset(self.canvas.pt(padding)) if padding else rect
        
        # Calculate splits based on heights/widths or weights
        weights = []
        for child in children:
            if direction == "ROW":
                w_val = child.get("width")
                if w_val and isinstance(w_val, str) and w_val.endswith("%"):
                    weights.append(float(w_val[:-1]))
                elif "grow" in child:
                    weights.append(float(child.get("grow", 1)) * 10)
                else:
                    weights.append(1.0)
            else: # COLUMN
                h_val = child.get("height")
                if h_val and isinstance(h_val, str) and h_val.endswith("%"):
                    weights.append(float(h_val[:-1]))
                elif "grow" in child:
                    weights.append(float(child.get("grow", 1)) * 10)
                else:
                    weights.append(1.0)

        # Split bounding box
        gap_px = self.canvas.pt(gap)
        sub_boxes = padded_rect.split_columns(weights, gap=gap_px) if direction == "ROW" else padded_rect.split_rows(weights, gap=gap_px)

        for i, child in enumerate(children):
            if i < len(sub_boxes):
                self.render(child, sub_boxes[i])

    def _render_text(self, node: dict, rect: Rect, node_type: str):
        content = str(node.get("content", ""))
        color = node.get("color", "#000000")
        align = node.get("align", "left").lower()

        # Font scale mapping
        size_map = {
            "h1": (40, "bold"),
            "h2": (24, "bold"),
            "h3": (18, "bold"),
            "p": (15, "regular"),
            "text": (13, "regular"),
            "small": (11, "regular")
        }
        pt_size, weight = size_map.get(node_type, (14, "regular"))
        custom_size = node.get("font_size")
        if custom_size: pt_size = custom_size

        font = self.canvas.get_font("Roboto-Bold" if weight == "bold" else "Roboto-Regular", pt_size)

        anchor = "lm" if align == "left" else ("mm" if align == "center" else "rm")
        pos_x = rect.x if align == "left" else (rect.center[0] if align == "center" else rect.right)
        pos_y = rect.center[1]

        self.canvas.draw_text(content, (pos_x, pos_y), font=font, fill=color, anchor=anchor)

    def _render_icon(self, node: dict, rect: Rect):
        path = node.get("path")
        if not path:
            return
        
        # Resolve path
        resolved = path
        if not os.path.isabs(path):
            base_dir = os.path.join(os.path.dirname(__file__), "..")
            resolved = os.path.join(base_dir, path)

        if os.path.exists(resolved):
            size_pt = node.get("size")
            self.canvas.paste_icon(resolved, rect, size_pt=size_pt)

def render_manifest(manifest: dict, dimensions: tuple[int, int] = (800, 480), frame: str = "Corner") -> Image.Image:
    """Convenience helper to compile and render a manifest dictionary into a PIL Image."""
    canvas = ResponsiveCanvas(dimensions, bg_color="#ffffff")
    content = canvas.bounds.inset(canvas.pt(16))
    compiler = ManifestCompiler(canvas)
    compiler.render(manifest, content)
    if frame and frame != "None":
        canvas.draw_frame(frame, color="#000000")
    return canvas.to_image()
