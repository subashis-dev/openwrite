import os
from PIL import Image, ImageDraw

def create_openwrite_icon():
    scratch_dir = r"C:\Users\subas\.gemini\antigravity-cli\brain\3902a322-9494-41ad-8cb8-a388e31b2d22\scratch"
    os.makedirs(scratch_dir, exist_ok=True)
    icon_path = os.path.join(scratch_dir, "app.ico")

    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    images = []

    for size in sizes:
        w, h = size
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Background rounded badge: deep slate / indigo gradient feel
        margin = max(1, w // 16)
        draw.rounded_rectangle(
            [margin, margin, w - margin, h - margin],
            radius=max(3, w // 5),
            fill=(15, 23, 42, 255),
            outline=(99, 102, 241, 255),
            width=max(1, w // 24)
        )

        # Draw sleek microphone shape
        cx = w / 2
        cy = h * 0.40
        rw = w * 0.16
        rh = h * 0.22

        draw.rounded_rectangle(
            [cx - rw, cy - rh, cx + rw, cy + rh],
            radius=rw,
            fill=(99, 102, 241, 255)  # Indigo-500
        )

        # Mic arc / stand
        arc_top = cy - rh * 0.2
        arc_bottom = cy + rh + (h * 0.10)
        arc_w = rw + (w * 0.09)
        draw.arc(
            [cx - arc_w, arc_top, cx + arc_w, arc_bottom],
            start=0,
            end=180,
            fill=(248, 250, 252, 255),
            width=max(1, int(w * 0.055))
        )

        # Mic stem
        stem_top = arc_bottom
        stem_bottom = stem_top + (h * 0.10)
        draw.line(
            [(cx, stem_top), (cx, stem_bottom)],
            fill=(248, 250, 252, 255),
            width=max(1, int(w * 0.055))
        )

        # Mic base
        base_w = w * 0.18
        draw.line(
            [(cx - base_w, stem_bottom), (cx + base_w, stem_bottom)],
            fill=(248, 250, 252, 255),
            width=max(1, int(w * 0.055))
        )

        images.append(img)

    images[0].save(
        icon_path,
        format="ICO",
        sizes=[(im.width, im.height) for im in images],
        append_images=images[1:]
    )
    print(f"Generated {icon_path} successfully!")

if __name__ == "__main__":
    create_openwrite_icon()
