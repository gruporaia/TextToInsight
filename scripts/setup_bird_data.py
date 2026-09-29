#!/usr/bin/env python3
"""
scripts/setup_bird_data.py

Automated download, extraction, and validation script for the BIRD benchmark dataset.
Built entirely using Python's standard library with zero external dependencies.

Usage:
    # 1. Download and setup the default compact Mini-Dev dataset:
    python scripts/setup_bird_data.py

    # 2. Download and setup the full official Dev dataset (11 databases, 1,534 questions):
    python scripts/setup_bird_data.py --full

    # 3. Use an existing local zip file without downloading from the internet:
    python scripts/setup_bird_data.py --zip-path /path/to/dev.zip

    # 4. Remove the zip file after extraction to save disk space:
    python scripts/setup_bird_data.py --clean-zip
"""

import argparse
import shutil
import sys
import time
import urllib.request
import zipfile
from pathlib import Path


# Default storage directory 
DEFAULT_DATA_DIR = Path("data/bird")
# Official direct HTTP download links (Alibaba Cloud OSS mirror, quota-free and fast)
URL_DEV_ZIP = "https://bird-bench.oss-cn-beijing.aliyuncs.com/dev.zip"
URL_MINIDEV_ZIP = "https://bird-bench.oss-cn-beijing.aliyuncs.com/minidev.zip"


def render_progress_bar(
    downloaded_bytes: int, 
    total_bytes: int, 
    start_time: float, 
    bar_length: int = 30
) -> None:
    """
    Renders an in-place terminal progress bar using carriage return ('\\r') on sys.stdout.
    
    Parameters:
        downloaded_bytes: Total number of bytes downloaded so far.
        total_bytes: Expected total size in bytes (from Content-Length header).
        start_time: Timestamp (time.time()) when the download started.
        bar_length: Width of the progress bar in terminal characters.
    """
    elapsed_seconds = time.time() - start_time
    speed_bytes_per_sec = downloaded_bytes / elapsed_seconds if elapsed_seconds > 0 else 0.0

    # Convert bytes to human-readable megabytes
    downloaded_mb = downloaded_bytes / (1024 * 1024)
    speed_mb_per_sec = speed_bytes_per_sec / (1024 * 1024)

    if total_bytes > 0:
        ratio = min(1.0, downloaded_bytes / total_bytes)
        filled_length = int(bar_length * ratio)
        bar_visual = "=" * filled_length + (">" if filled_length < bar_length else "")
        bar_string = bar_visual.ljust(bar_length)
        total_mb = total_bytes / (1024 * 1024)

        sys.stdout.write(
            f"\r  [{bar_string}] {ratio * 100:5.1f}% "
            f"({downloaded_mb:6.1f} MB / {total_mb:6.1f} MB) - "
            f"{speed_mb_per_sec:4.1f} MB/s"
        )
    else:
        # Fallback when server does not provide a Content-Length header
        sys.stdout.write(
            f"\r  Downloaded: {downloaded_mb:6.1f} MB ({speed_mb_per_sec:4.1f} MB/s)..."
        )
    sys.stdout.flush()


def download_file(url: str, dest_path: Path, force: bool = False) -> Path:
    """
    Downloads a remote file atomically using a streaming buffer and a .part temporary file.
    
    Parameters:
        url: The web URL to fetch.
        dest_path: Target destination path on disk.
        force: If True, re-downloads even if dest_path already exists.
        
    Returns:
        The destination Path object.
    """
    if dest_path.exists() and not force:
        print(f"  ✓ File already exists: {dest_path.name} (use --force to overwrite)")
        return dest_path

    # Ensure parent directory exists
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    # Use a temporary .part filename to avoid corrupt files if download is interrupted
    temp_path = dest_path.with_name(f"{dest_path.name}.part")

    print(f"  → Downloading: {dest_path.name}")
    print(f"    URL: {url}")

    # Standard browser user-agent header to prevent generic 403 Forbidden blocks
    request = urllib.request.Request(
        url=url, 
        headers={"User-Agent": "TextToInsight-Downloader/1.0"}
    )
    start_time = time.time()

    try:
        with urllib.request.urlopen(request) as response:
            header_content_length = response.headers.get("Content-Length", 0)
            total_bytes = int(header_content_length) if header_content_length else 0
            downloaded_bytes = 0
            chunk_size = 64 * 1024  # 64 KB buffer per iteration

            with open(temp_path, "wb") as output_file:
                while True:
                    data_chunk = response.read(chunk_size)
                    if not data_chunk:
                        break  # Reached end of stream
                    output_file.write(data_chunk)
                    downloaded_bytes += len(data_chunk)
                    render_progress_bar(downloaded_bytes, total_bytes, start_time)

        # Print newline once progress is finished
        sys.stdout.write("\n")
        sys.stdout.flush()

        # Atomic rename: .part is replaced by the actual final destination filename
        temp_path.replace(dest_path)
        print(f"  ✓ Download completed: {dest_path.name}")
        return dest_path

    except Exception as error:
        sys.stdout.write("\n")
        sys.stdout.flush()
        # Clean up partial incomplete file on failure
        if temp_path.exists():
            temp_path.unlink()
        raise RuntimeError(f"Failed to download {url}: {error}") from error


def normalize_extracted_layout(target_dir: Path) -> None:
    """
    Normalizes directory structure across both Full Dev and Mini-Dev packages.
    
    Handles quirks in BIRD archive packaging:
    - Wrapper subfolders with varying case ('MINIDEV', 'minidev', 'dev').
    - Discrepancies in question filename ('mini_dev_sqlite.json' vs 'dev.json').
    - Intermediate folders like dev_[date] (e.g. dev_20240627).
    - Relocates nested databases and schema files, ensuring dev.json is visible
      both in target_dir root and inside any intermediate subfolder.
    """
    if not target_dir.exists():
        return

    # 1. Check for internal dev_databases.zip archive (used in full dev set)
    # Search root and intermediate subfolders (e.g. dev_20240627/dev_databases.zip)
    db_zips: list[Path] = [target_dir / "dev_databases.zip"]
    if target_dir.is_dir():
        for sub in target_dir.iterdir():
            if sub.is_dir() and not sub.name.startswith("."):
                sub_zip = sub / "dev_databases.zip"
                if sub_zip.exists() and sub_zip not in db_zips:
                    db_zips.append(sub_zip)
        for nested in target_dir.rglob("dev_databases.zip"):
            if nested not in db_zips:
                db_zips.append(nested)

    for inner_zip in db_zips:
        if inner_zip.exists() and inner_zip.is_file():
            rel_name = inner_zip.relative_to(target_dir) if inner_zip.is_relative_to(target_dir) else inner_zip.name
            print(f"  → Found nested archive: {rel_name}. Extracting databases...")
            databases_dir = target_dir / "dev_databases"
            databases_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(inner_zip, "r") as inner_archive:
                names = inner_archive.namelist()
                if any(n.startswith("dev_databases/") for n in names):
                    inner_archive.extractall(target_dir)
                else:
                    inner_archive.extractall(databases_dir)
            inner_zip.unlink()
            print(f"  ✓ Databases extracted into: {databases_dir}")

    # 2. Flatten generic wrapper directory if present (e.g. MINIDEV/ or minidev/ or dev/)
    for item in list(target_dir.iterdir()):
        if item.is_dir() and item.name.lower() in ("minidev", "dev"):
            print(f"  → Normalizing nested folder structure: {item.name}/...")
            for sub_item in list(item.iterdir()):
                destination = target_dir / sub_item.name
                if not destination.exists():
                    shutil.move(str(sub_item), str(destination))
                elif sub_item.is_dir() and destination.is_dir():
                    for sub_sub in list(sub_item.iterdir()):
                        dest_sub = destination / sub_sub.name
                        if not dest_sub.exists():
                            shutil.move(str(sub_sub), str(dest_sub))
            shutil.rmtree(item, ignore_errors=True)

    # 3. Standardize and synchronize dev.json across root and any intermediate folders (e.g. dev_[date])
    dev_json = target_dir / "dev.json"
    mini_dev_sqlite = target_dir / "mini_dev_sqlite.json"

    # If dev.json does not exist in target_dir root, locate it in any subfolder or standardize mini_dev_sqlite
    if not dev_json.exists():
        if mini_dev_sqlite.exists():
            shutil.copy2(mini_dev_sqlite, dev_json)
            print(f"  ✓ Standardized {mini_dev_sqlite.name} -> {dev_json.name}")
        else:
            found_sub_dev = None
            for sub in sorted(target_dir.iterdir(), reverse=True):
                if sub.is_dir() and not sub.name.startswith("."):
                    for candidate_name in ("dev.json", "mini_dev_sqlite.json", "mini_dev.json", "minidev.json"):
                        candidate = sub / candidate_name
                        if candidate.is_file():
                            found_sub_dev = candidate
                            break
                if found_sub_dev:
                    break

            if not found_sub_dev:
                for match in sorted(target_dir.rglob("dev.json"), reverse=True):
                    if match.is_file() and not any(part.startswith(".") for part in match.parts):
                        found_sub_dev = match
                        break

            if found_sub_dev:
                shutil.copy2(found_sub_dev, dev_json)
                print(f"  ✓ Made dev.json visible in root: {found_sub_dev.relative_to(target_dir)} -> {dev_json.name}")

    # Ensure dev.json is visible inside any intermediate subfolder (e.g. dev_[date], dev_20240627)
    if dev_json.exists():
        for sub in target_dir.iterdir():
            if (
                sub.is_dir()
                and not sub.name.startswith(".")
                and (sub.name.lower().startswith("dev") or sub.name.lower().startswith("minidev"))
                and not sub.name.lower().startswith("dev_database")
            ):
                sub_dev_json = sub / "dev.json"
                if not sub_dev_json.exists():
                    shutil.copy2(dev_json, sub_dev_json)
                    print(f"  ✓ Made dev.json visible inside folder: {sub.name}/dev.json")

    # 4. Standardize companion files (dev_tables.json, dev.sql, dev_tied_append.json) to root if present in subfolder
    for companion in ("dev_tables.json", "dev.sql", "dev_tied_append.json"):
        if not (target_dir / companion).exists():
            for sub in target_dir.iterdir():
                if sub.is_dir() and (sub / companion).is_file():
                    shutil.copy2(sub / companion, target_dir / companion)
                    print(f"  ✓ Standardized companion file {companion} to root")
                    break


def extract_zip(zip_path: Path, target_dir: Path, clean_zip: bool = False) -> None:
    """
    Unpacks a zip archive into the target directory with integrity checks.
    
    Parameters:
        zip_path: Path to the .zip archive.
        target_dir: Directory where contents will be extracted.
        clean_zip: If True, deletes the .zip file after successful extraction.
    """
    print(f"  → Checking archive integrity: {zip_path.name}...")
    if not zipfile.is_zipfile(zip_path):
        raise ValueError(f"Corrupt or invalid zip archive: {zip_path}")

    target_dir.mkdir(parents=True, exist_ok=True)
    print(f"  → Extracting to: {target_dir}...")

    with zipfile.ZipFile(zip_path, "r") as archive:
        archive.extractall(target_dir)

    print("  ✓ Extraction finished successfully.")

    # Normalize directory layout and standardize filenames
    normalize_extracted_layout(target_dir)

    # Remove outer zip if requested
    if clean_zip:
        print(f"  → Removing archive to free disk space: {zip_path.name}...")
        zip_path.unlink()
        print("  ✓ Archive removed.")



def verify_setup(data_dir: Path) -> bool:
    """
    Verifies that the required BIRD benchmark files exist and are ready for evaluation.
    
    Returns:
        True if the setup is fully verified, False otherwise.
    """
    normalize_extracted_layout(data_dir)
    print("\n Verifying BIRD dataset integrity...")

    # 1. Check for dev.json or mini_dev questions in root and any subfolder
    dev_json_candidates = [
        data_dir / "dev.json",
        data_dir / "mini_dev_sqlite.json",
        data_dir / "mini_dev.json",
        data_dir / "minidev.json"
    ]
    if data_dir.is_dir():
        for sub in sorted(data_dir.iterdir(), reverse=True):
            if sub.is_dir() and not sub.name.startswith("."):
                for name in ("dev.json", "mini_dev_sqlite.json", "mini_dev.json", "minidev.json"):
                    dev_json_candidates.append(sub / name)

    found_dev_json = next((p for p in dev_json_candidates if p.exists() and p.is_file()), None)

    if not found_dev_json:
        for match in sorted(data_dir.rglob("dev.json"), reverse=True):
            if match.is_file() and not any(part.startswith(".") for part in match.parts):
                found_dev_json = match
                break

    if not found_dev_json:
        print(f"  ❌ Missing dev.json in {data_dir}")
        return False

    # Ensure dev.json is visible in data_dir root if found in subfolder
    if found_dev_json.parent != data_dir and not (data_dir / "dev.json").exists():
        shutil.copy2(found_dev_json, data_dir / "dev.json")

    dev_size_kb = found_dev_json.stat().st_size / 1024
    rel_path = found_dev_json.relative_to(data_dir) if found_dev_json.is_relative_to(data_dir) else found_dev_json.name
    print(f"  ✓ Found question dataset: {rel_path} ({dev_size_kb:.1f} KB)")

    # 2. Check for SQLite databases
    sqlite_files = list(data_dir.rglob("*.sqlite")) + list(data_dir.rglob("*.db"))
    if not sqlite_files:
        print(f"  ❌ No SQLite databases (.sqlite / .db) found inside {data_dir}")
        return False

    print(f"  ✓ Found {len(sqlite_files)} SQLite database file(s):")
    for db_path in sorted(sqlite_files)[:5]:
        size_mb = db_path.stat().st_size / (1024 * 1024)
        print(f"      - {db_path.parent.name}/{db_path.name} ({size_mb:.2f} MB)")
    if len(sqlite_files) > 5:
        print(f"      ... and {len(sqlite_files) - 5} more database(s).")

    print("\n BIRD benchmark dataset is completely ready for evaluation!")
    return True



def parse_arguments() -> argparse.Namespace:
    """Configures and parses command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Download, unpack and prepare the BIRD benchmark dataset for TextToInsight."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"Target directory for storing dataset files (default: {DEFAULT_DATA_DIR})"
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Download the complete official Dev set (1.534 questions / 11 databases) instead of Mini-Dev."
    )
    parser.add_argument(
        "--zip-path",
        type=Path,
        default=None,
        help="Optional local path to a pre-downloaded .zip file. Skips downloading."
    )
    parser.add_argument(
        "--clean-zip",
        action="store_true",
        help="Delete the .zip archive after unpacking to save disk space."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-download and re-extraction even if files already exist."
    )
    return parser.parse_args()


def main() -> None:
    """Main execution entrypoint."""
    args = parse_arguments()

    data_dir: Path = args.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print(" BIRD Benchmark Dataset Setup")
    print(f"Target Directory: {data_dir.resolve()}")
    print("=" * 60)

    # Fast path: check if already fully setup
    if not args.force and verify_setup(data_dir):
        print("\nAll files are present. Use --force if you want to re-download.")
        return

    # Case A: Local pre-downloaded zip file supplied by user
    if args.zip_path:
        zip_file = args.zip_path.resolve()
        if not zip_file.exists():
            print(f" Error: Specified zip file does not exist: {zip_file}")
            sys.exit(1)
        extract_zip(zip_file, data_dir, clean_zip=args.clean_zip)

    # Case B: Download from remote repository
    else:
        if args.full:
            download_url = URL_DEV_ZIP
            zip_filename = "dev.zip"
            print("Mode: Full Official Dev Set (1,534 questions)")
        else:
            download_url = URL_MINIDEV_ZIP
            zip_filename = "minidev.zip"
            print("Mode: Mini-Dev Compact Set (Fast local testing)")

        target_zip = data_dir / zip_filename
        downloaded_zip = download_file(download_url, target_zip, force=args.force)
        extract_zip(downloaded_zip, data_dir, clean_zip=args.clean_zip)

    # Final sanity verification
    if not verify_setup(data_dir):
        print("\n Setup finished with warnings: some expected files were not found.")
        sys.exit(1)


if __name__ == "__main__":
    main()