"""Independent image retrieval checks, using generated fixtures only."""
import io
import json
import unittest

from PIL import Image, ImageDraw

from image_engine import compare_features, extract_features, fast_compare_features, make_thumbnail


def image_bytes(image, fmt="PNG", **kwargs):
    output = io.BytesIO()
    image.save(output, fmt, **kwargs)
    return output.getvalue()


def product_fixture(background="white", alternate=False):
    image = Image.new("RGB", (640, 640), background)
    draw = ImageDraw.Draw(image)
    if alternate:
        draw.ellipse((160, 130, 500, 485), fill="#d07c3e", outline="#553522", width=9)
        draw.ellipse((184, 158, 475, 459), outline="#e3d4b8", width=10)
        for n in range(10):
            draw.line((190 + n * 24, 190, 280 + n * 15, 430), fill="#805022", width=4)
    else:
        draw.rounded_rectangle((160, 180, 430, 490), radius=28, fill="#698c91", outline="#27373a", width=7)
        draw.ellipse((145, 158, 445, 216), fill="#a3b9b5", outline="#27373a", width=5)
        draw.ellipse((182, 173, 408, 202), fill="#263c3d")
        draw.arc((372, 246, 518, 428), -90, 90, fill="#27373a", width=22)
        draw.arc((385, 261, 503, 412), -90, 90, fill="#90aaab", width=12)
        for row in range(4):
            for col in range(5):
                x, y = 194 + col * 43, 244 + row * 54
                draw.ellipse((x, y, x + 13, y + 21), fill="#e4d4a0")
        draw.line((180, 468, 410, 468), fill="#425b5d", width=5)
    return image


class ImageEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = product_fixture()
        cls.original_bytes = image_bytes(cls.original)
        cls.features = extract_features(cls.original_bytes)
        cls.other = extract_features(image_bytes(product_fixture(alternate=True)))

    def test_features_are_json_serializable_and_exact_match(self):
        restored = json.loads(json.dumps(self.features))
        self.assertEqual(compare_features(restored, self.features)["score"], 100)
        self.assertTrue(compare_features(restored, self.features)["exact"])

    def test_compressed_resized_same_product_beats_different_object(self):
        resized = self.original.resize((280, 280), Image.Resampling.LANCZOS)
        query = extract_features(image_bytes(resized, "JPEG", quality=62))
        own = compare_features(query, self.features)
        other = compare_features(query, self.other)
        self.assertFalse(own["exact"])
        self.assertGreater(own["score"], 80)
        self.assertGreater(own["score"], other["score"] + 15)

    def test_cropped_image_still_ranks_known_image_first(self):
        query = extract_features(image_bytes(self.original.crop((125, 135, 540, 535))))
        own = compare_features(query, self.features)
        other = compare_features(query, self.other)
        self.assertGreater(own["score"], 70)
        self.assertGreater(own["score"], other["score"] + 12)

    def test_uniform_background_change_retains_foreground_match(self):
        changed = extract_features(image_bytes(product_fixture("#e4d8ca")))
        own = compare_features(changed, self.features)
        self.assertGreater(own["score"], 75)
        self.assertGreater(own["score"], compare_features(changed, self.other)["score"] + 12)

    def test_blank_images_cannot_generate_high_similarity(self):
        white = extract_features(image_bytes(Image.new("RGB", (250, 250), "white")))
        gray = extract_features(image_bytes(Image.new("RGB", (250, 250), "#eee")))
        self.assertLess(compare_features(white, gray)["score"], 20)
        self.assertLess(compare_features(white, self.features)["score"], 20)

    def test_malformed_images_are_rejected(self):
        for data in [b"", b"not an image", image_bytes(Image.new("RGB", (2, 2)))]:
            with self.assertRaises(ValueError):
                extract_features(data)

    def test_transparent_image_is_safely_flattened(self):
        image = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        ImageDraw.Draw(image).rectangle((20, 20, 80, 80), fill=(255, 0, 0, 255))
        thumbnail = Image.open(io.BytesIO(make_thumbnail(image_bytes(image))))
        self.assertEqual(thumbnail.mode, "RGB")
        self.assertGreater(min(thumbnail.getpixel((0, 0))), 245)

    def test_thumbnail_respects_size_and_exif_orientation(self):
        image = Image.new("RGB", (900, 450), "red")
        exif = Image.Exif()
        exif[274] = 6
        data = image_bytes(image, "JPEG", exif=exif)
        thumb = Image.open(io.BytesIO(make_thumbnail(data)))
        self.assertEqual(thumb.size, (320, 640))
        features = extract_features(data)
        self.assertEqual((features["width"], features["height"]), (450, 900))

    def test_coarse_ranking_does_not_need_orb(self):
        query = extract_features(image_bytes(self.original.resize((300, 300))))
        self.assertGreater(fast_compare_features(query, self.features)["score"],
                           fast_compare_features(query, self.other)["score"])


if __name__ == "__main__":
    unittest.main()
