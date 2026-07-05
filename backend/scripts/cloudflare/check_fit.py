#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


MAX_DISK_GB = 20.0


def run_json(command: list[str]) -> object:
    output = subprocess.check_output(command, text=True)
    return json.loads(output)


def image_size_bytes(image: str) -> int:
    data = run_json(["docker", "image", "inspect", image])
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"docker image inspect returned no data for {image}")
    size = data[0].get("Size")
    if not isinstance(size, int):
        raise RuntimeError(f"docker image inspect did not return integer Size for {image}")
    return size


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check whether a built Lean UI backend image fits Cloudflare Containers disk limits."
    )
    parser.add_argument("image", help="Local Docker image tag to inspect")
    parser.add_argument(
        "--output",
        default="backend/outputs/cloudflare_fit.json",
        help="Path for the JSON fit report",
    )
    args = parser.parse_args()

    size_bytes = image_size_bytes(args.image)
    size_gb = size_bytes / 1024 / 1024 / 1024
    report = {
        "image": args.image,
        "image_size_bytes": size_bytes,
        "image_size_gb": round(size_gb, 3),
        "cloudflare_container_disk_limit_gb": MAX_DISK_GB,
        "fits_standard_4_disk": size_gb < MAX_DISK_GB,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["fits_standard_4_disk"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
