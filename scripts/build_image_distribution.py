#!/usr/bin/env python3
"""Build destination-specific image-bank release assets and a manifest.

The source layout is expected to be:
    image_bank_full/<Country>/<Destination>/**/*.<image extension>

The generated output contains one deterministic ZIP archive per destination and
one manifest describing every pack. The itinerary app can fetch the manifest
and download only the packs required for the current itinerary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence

IMAGE_EXTENSIONS = frozenset({".webp", ".jpg", ".jpeg", ".png", ".avif"})
SCHEMA_VERSION = 1
DEFAULT_RELEASE_TAG = "image-bank-distribution"
DEFAULT_REPOSITORY = "Dkaalen/itinerary-image-bank"
ZIP_TIMESTAMP = (2020, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class SourceImage:
    absolute_path: Path
    archive_path: str
    relative_path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class DestinationGroup:
    country: str
    destination: str
    source_dir: Path
    images: tuple[SourceImage, ...]


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ascii_fold(value: str) -> str:
    translated = value.translate(str.maketrans({
        "ø": "o", "Ø": "O",
        "å": "a", "Å": "A",
        "æ": "ae", "Æ": "AE",
        "ð": "d", "Ð": "D",
        "þ": "th", "Þ": "TH",
        "ł": "l", "Ł": "L",
    }))
    normalized = unicodedata.normalize("NFKD", translated)
    return normalized.encode("ascii", "ignore").decode("ascii")


def _slug(value: str) -> str:
    ascii_text = _ascii_fold(value)
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug or "destination"


def _aliases(value: str) -> list[str]:
    candidates = {
        value,
        _ascii_fold(value),
    }
    return sorted({candidate.strip() for candidate in candidates if candidate.strip()}, key=str.casefold)


def _iter_destination_dirs(source_root: Path) -> Iterable[tuple[str, str, Path]]:
    for country_dir in sorted((p for p in source_root.iterdir() if p.is_dir()), key=lambda p: p.name.casefold()):
        for destination_dir in sorted((p for p in country_dir.iterdir() if p.is_dir()), key=lambda p: p.name.casefold()):
            yield country_dir.name, destination_dir.name, destination_dir


def _load_destination_group(source_root: Path, country: str, destination: str, source_dir: Path) -> DestinationGroup | None:
    images: list[SourceImage] = []
    for path in sorted(source_dir.rglob("*"), key=lambda p: p.as_posix().casefold()):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        relative_inside_destination = path.relative_to(source_dir).as_posix()
        archive_path = PurePosixPath("image_bank_full", country, destination, relative_inside_destination).as_posix()
        source_relative = path.relative_to(source_root.parent).as_posix()
        images.append(
            SourceImage(
                absolute_path=path,
                archive_path=archive_path,
                relative_path=source_relative,
                size_bytes=path.stat().st_size,
                sha256=_sha256_file(path),
            )
        )
    if not images:
        return None
    return DestinationGroup(country=country, destination=destination, source_dir=source_dir, images=tuple(images))


def discover_groups(source_root: Path) -> list[DestinationGroup]:
    if not source_root.is_dir():
        raise FileNotFoundError(f"Image-bank source directory does not exist: {source_root}")
    groups: list[DestinationGroup] = []
    for country, destination, source_dir in _iter_destination_dirs(source_root):
        group = _load_destination_group(source_root, country, destination, source_dir)
        if group is not None:
            groups.append(group)
    if not groups:
        raise RuntimeError(f"No destination image folders were found under {source_root}")
    return groups


def _asset_name(group: DestinationGroup, used_names: set[str]) -> str:
    base = f"{_slug(group.country)}__{_slug(group.destination)}"
    candidate = f"{base}.zip"
    if candidate not in used_names:
        used_names.add(candidate)
        return candidate
    suffix = hashlib.sha256(f"{group.country}/{group.destination}".encode("utf-8")).hexdigest()[:8]
    candidate = f"{base}__{suffix}.zip"
    used_names.add(candidate)
    return candidate


def _write_deterministic_zip(group: DestinationGroup, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    try:
        with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for image in group.images:
                info = zipfile.ZipInfo(image.archive_path, date_time=ZIP_TIMESTAMP)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                with image.absolute_path.open("rb") as source:
                    archive.writestr(info, source.read(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
        os.replace(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _bank_version(groups: Sequence[DestinationGroup]) -> str:
    digest = hashlib.sha256()
    for group in groups:
        digest.update(group.country.encode("utf-8"))
        digest.update(b"\0")
        digest.update(group.destination.encode("utf-8"))
        digest.update(b"\0")
        for image in group.images:
            digest.update(image.relative_path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(image.sha256.encode("ascii"))
            digest.update(b"\0")
    return digest.hexdigest()


def build_distribution(
    source_root: Path,
    output_root: Path,
    repository: str,
    release_tag: str,
    source_commit: str,
) -> Path:
    groups = discover_groups(source_root)
    packs_dir = output_root / "packs"
    packs_dir.mkdir(parents=True, exist_ok=True)

    used_names: set[str] = set()
    destination_entries: dict[str, dict[str, object]] = {}
    bank_version = _bank_version(groups)
    base_url = f"https://github.com/{repository}/releases/download/{release_tag}"

    expected_assets: set[str] = set()
    for group in groups:
        asset_name = _asset_name(group, used_names)
        expected_assets.add(asset_name)
        archive_path = packs_dir / asset_name
        _write_deterministic_zip(group, archive_path)
        archive_sha256 = _sha256_file(archive_path)
        key = f"{group.country}/{group.destination}"
        destination_entries[key] = {
            "country": group.country,
            "destination": group.destination,
            "country_aliases": _aliases(group.country),
            "destination_aliases": _aliases(group.destination),
            "asset_name": asset_name,
            "download_url": f"{base_url}/{asset_name}",
            "sha256": archive_sha256,
            "size_bytes": archive_path.stat().st_size,
            "file_count": len(group.images),
            "images": [
                {
                    "path": image.relative_path,
                    "archive_path": image.archive_path,
                    "size_bytes": image.size_bytes,
                    "sha256": image.sha256,
                }
                for image in group.images
            ],
        }

    for stale_pack in packs_dir.glob("*.zip"):
        if stale_pack.name not in expected_assets:
            stale_pack.unlink()

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "bank_version": bank_version,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source_repository": repository,
        "source_commit": source_commit,
        "release_tag": release_tag,
        "base_url": base_url,
        "destination_count": len(destination_entries),
        "image_count": sum(int(entry["file_count"]) for entry in destination_entries.values()),
        "destinations": destination_entries,
    }
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    validate_distribution(manifest_path, packs_dir)
    return manifest_path


def _safe_archive_name(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts and path.parts[0] == "image_bank_full"


def validate_distribution(manifest_path: Path, packs_dir: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unexpected manifest schema version")
    destinations = manifest.get("destinations")
    if not isinstance(destinations, dict) or not destinations:
        raise ValueError("Manifest contains no destinations")

    for key, entry in destinations.items():
        asset_name = entry.get("asset_name")
        if not isinstance(asset_name, str) or not asset_name.endswith(".zip"):
            raise ValueError(f"Invalid asset name for {key}")
        archive_path = packs_dir / asset_name
        if not archive_path.is_file():
            raise FileNotFoundError(f"Missing archive for {key}: {archive_path}")
        if _sha256_file(archive_path) != entry.get("sha256"):
            raise ValueError(f"Archive checksum mismatch for {key}")
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            if len(names) != entry.get("file_count"):
                raise ValueError(f"Archive file count mismatch for {key}")
            if any(not _safe_archive_name(name) for name in names):
                raise ValueError(f"Unsafe archive member detected for {key}")
            bad_member = archive.testzip()
            if bad_member is not None:
                raise ValueError(f"Corrupt archive member for {key}: {bad_member}")


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("image_bank_full"))
    parser.add_argument("--output", type=Path, default=Path("build/image_distribution"))
    parser.add_argument("--repository", default=os.getenv("GITHUB_REPOSITORY", DEFAULT_REPOSITORY))
    parser.add_argument("--release-tag", default=os.getenv("IMAGE_BANK_RELEASE_TAG", DEFAULT_RELEASE_TAG))
    parser.add_argument("--source-commit", default=os.getenv("GITHUB_SHA", "local"))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    manifest_path = build_distribution(
        source_root=args.source.resolve(),
        output_root=args.output.resolve(),
        repository=args.repository,
        release_tag=args.release_tag,
        source_commit=args.source_commit,
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    print(
        f"Built {manifest['destination_count']} destination packs containing "
        f"{manifest['image_count']} images. Manifest: {manifest_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
