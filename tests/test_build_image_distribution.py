from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.build_image_distribution import build_distribution, validate_distribution


class BuildImageDistributionTests(unittest.TestCase):
    def test_builds_one_deterministic_pack_per_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "image_bank_full"
            (source / "Norway" / "Tromsø").mkdir(parents=True)
            (source / "Norway" / "Oslo").mkdir(parents=True)
            (source / "Norway" / "Tromsø" / "aurora.webp").write_bytes(b"tromso-image")
            (source / "Norway" / "Oslo" / "opera.webp").write_bytes(b"oslo-image")

            output = root / "build"
            manifest_path = build_distribution(
                source_root=source,
                output_root=output,
                repository="Dkaalen/itinerary-image-bank",
                release_tag="image-bank-distribution",
                source_commit="abc123",
            )

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["destination_count"], 2)
            self.assertEqual(manifest["image_count"], 2)
            self.assertIn("Norway/Tromsø", manifest["destinations"])
            tromso = manifest["destinations"]["Norway/Tromsø"]
            self.assertIn("Tromso", tromso["destination_aliases"])
            self.assertTrue(tromso["download_url"].endswith(tromso["asset_name"]))

            archive_path = output / "packs" / tromso["asset_name"]
            with zipfile.ZipFile(archive_path) as archive:
                self.assertEqual(
                    archive.namelist(),
                    ["image_bank_full/Norway/Tromsø/aurora.webp"],
                )
            first_sha = tromso["sha256"]
            second_manifest_path = build_distribution(
                source_root=source,
                output_root=output,
                repository="Dkaalen/itinerary-image-bank",
                release_tag="image-bank-distribution",
                source_commit="abc123",
            )
            second_manifest = json.loads(second_manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(first_sha, second_manifest["destinations"]["Norway/Tromsø"]["sha256"])
            validate_distribution(manifest_path, output / "packs")

    def test_ignores_non_image_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "image_bank_full"
            destination = source / "Norway" / "Alta"
            destination.mkdir(parents=True)
            (destination / "photo.webp").write_bytes(b"image")
            (destination / "notes.txt").write_text("not an image", encoding="utf-8")

            manifest_path = build_distribution(
                source_root=source,
                output_root=root / "build",
                repository="Dkaalen/itinerary-image-bank",
                release_tag="image-bank-distribution",
                source_commit="abc123",
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["image_count"], 1)
            self.assertEqual(manifest["destinations"]["Norway/Alta"]["file_count"], 1)


if __name__ == "__main__":
    unittest.main()
