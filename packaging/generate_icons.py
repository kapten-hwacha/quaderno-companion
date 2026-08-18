#!/usr/bin/env python3
"""Generates macOS .icns and PNG icon assets for Quaderno Companion."""

import math
import os
import shutil
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def draw_quaderno_icon(size: int) -> Image.Image:
    """Draw a clean, modern Apple-style E-ink e-reader icon."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Padding
    pad = int(size * 0.08)
    radius = int(size * 0.18)

    # Draw rounded rectangle container (device chassis: dark slate)
    box = [pad, pad, size - pad, size - pad]
    draw.rounded_rectangle(box, radius=radius, fill=(35, 39, 46, 255), outline=(55, 62, 73, 255), width=max(1, int(size * 0.02)))

    # Draw E-ink screen area (light cream/white)
    screen_pad_x = int(size * 0.18)
    screen_pad_top = int(size * 0.18)
    screen_pad_bottom = int(size * 0.22)
    screen_box = [
        screen_pad_x,
        screen_pad_top,
        size - screen_pad_x,
        size - screen_pad_bottom,
    ]
    screen_radius = max(2, int(size * 0.04))
    draw.rounded_rectangle(screen_box, radius=screen_radius, fill=(244, 243, 238, 255))

    # Draw stylized document text lines inside the screen
    line_x_start = screen_pad_x + int(size * 0.06)
    line_x_end = size - screen_pad_x - int(size * 0.06)
    line_w = max(1, int(size * 0.025))

    # Header line (dark)
    h_y = screen_pad_top + int(size * 0.08)
    draw.line([(line_x_start, h_y), (line_x_start + int(size * 0.22), h_y)], fill=(40, 40, 40, 255), width=max(2, int(line_w * 1.5)))

    # Body lines
    for i, factor in enumerate([0.15, 0.21, 0.27, 0.33]):
        y = screen_pad_top + int(size * factor)
        end_x = line_x_end if i % 2 == 0 else int(line_x_end - size * 0.08)
        draw.line([(line_x_start, y), (end_x, y)], fill=(90, 95, 105, 230), width=line_w)

    # Stylized spark / companion star in bottom-right corner of screen
    spark_center_x = size - screen_pad_x - int(size * 0.08)
    spark_center_y = size - screen_pad_bottom - int(size * 0.08)
    r = max(2, int(size * 0.04))
    draw.ellipse(
        [spark_center_x - r, spark_center_y - r, spark_center_x + r, spark_center_y + r],
        fill=(230, 80, 50, 255),
    )

    # Device home button indicator
    btn_y = size - int(screen_pad_bottom * 0.6)
    btn_w = int(size * 0.08)
    draw.rounded_rectangle(
        [(size - btn_w) // 2, btn_y - max(1, int(size * 0.01)), (size + btn_w) // 2, btn_y + max(1, int(size * 0.01))],
        radius=max(1, int(size * 0.01)),
        fill=(80, 85, 95, 255),
    )

    return img


def generate_assets(output_dir: Path):
    """Generate PNGs and macOS .icns file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    iconset_dir = output_dir / "QuadernoCompanion.iconset"
    if iconset_dir.exists():
        shutil.rmtree(iconset_dir)
    iconset_dir.mkdir()

    # Standard macOS icon sizes
    sizes = [
        (16, "icon_16x16.png"),
        (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"),
        (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"),
        (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"),
        (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"),
        (1024, "icon_512x512@2x.png"),
    ]

    for px, filename in sizes:
        icon_img = draw_quaderno_icon(px)
        icon_img.save(iconset_dir / filename, "PNG")

    # Main 1024x1024 png
    main_png = output_dir / "icon.png"
    draw_quaderno_icon(1024).save(main_png, "PNG")

    # Convert to .icns via macOS iconutil if available
    icns_path = output_dir / "QuadernoCompanion.icns"
    if shutil.which("iconutil"):
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset_dir), "-o", str(icns_path)],
            check=True,
        )
        print(f"Generated macOS icns: {icns_path}")
    
    # Clean up temporary iconset
    shutil.rmtree(iconset_dir)
    print(f"Generated PNG icon: {main_png}")


if __name__ == "__main__":
    assets_path = Path(__file__).parent / "assets"
    generate_assets(assets_path)
