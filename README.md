# Itinerary Image Bank

Destination-specific WebP image bank for the itinerary app.

Expected source structure:

```text
image_bank_full/<Country>/<Destination>/*.webp
```

## Fast application delivery

The repository publishes a machine-generated distribution release containing:

- `manifest.json`
- One ZIP archive per destination
- SHA-256 checksums, image counts and normalized destination aliases

Stable manifest endpoint:

```text
https://github.com/Dkaalen/itinerary-image-bank/releases/download/image-bank-distribution/manifest.json
```

The itinerary app should download the manifest first and then fetch only the destination packs required by the current itinerary. It should not clone or download the complete repository during the user workflow.

## Automatic publishing

`.github/workflows/publish-image-distribution.yml` rebuilds the distribution whenever source images or the builder change. It also supports manual runs from the GitHub Actions page.

The workflow:

1. Scans `image_bank_full`.
2. Builds deterministic destination ZIP archives.
3. Generates and validates the manifest.
4. Removes obsolete release assets.
5. Publishes the current manifest and packs to the stable `image-bank-distribution` release.

## Local validation

```powershell
python .\scripts\build_image_distribution.py
python -m unittest discover -s .\tests -p "test_*.py"
```

Generated files are written to `build/image_distribution` and are not committed to the repository.
