"""Generate the native Windows icon from our original vector-style brand mark."""
from pathlib import Path
from PIL import Image, ImageDraw

root = Path(__file__).resolve().parent
image = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((0, 0, 255, 255), radius=72, fill="#5267eb")
points = [(64, 166), (64, 90), (109, 90), (128, 122), (147, 90), (192, 90), (192, 166), (160, 166), (160, 122), (128, 166), (96, 122), (96, 166)]
draw.polygon(points, fill="#ffffff")
draw.ellipse((173, 45, 211, 83), fill="#acb7ff")
image.save(root / "static" / "assets" / "midoo.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
