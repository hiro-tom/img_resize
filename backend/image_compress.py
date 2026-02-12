"""Image compression module for SFTP Sync Service."""

import os
from pathlib import Path
from typing import Callable, Optional

from PIL import Image

# Supported image extensions
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tiff', '.tif'}


def is_image_file(file_path: str) -> bool:
    """Check if file is a supported image format."""
    return Path(file_path).suffix.lower() in IMAGE_EXTENSIONS


def compress_image(
    input_path: str,
    output_path: str,
    quality: int = 85,
    resize_width: Optional[int] = None,
) -> dict:
    """
    Compress a single image file.

    Args:
        input_path: Source image file path
        output_path: Destination file path
        quality: Compression quality (1-100)
        resize_width: Target width in pixels (optional, height auto-calculated to maintain aspect ratio)

    Returns:
        dict with original_size, compressed_size, and saved_bytes
    """
    original_size = os.path.getsize(input_path)

    with Image.open(input_path) as img:
        original_dimensions = img.size  # (width, height)

        # Convert to RGB if necessary (for PNG with alpha, etc.)
        if img.mode in ('RGBA', 'LA', 'P'):
            # Create white background for transparent images
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')

        # Resize if width is specified (height auto-calculated to maintain aspect ratio)
        if resize_width:
            orig_w, orig_h = img.size
            resize_height = int(orig_h * resize_width / orig_w)
            img = img.resize((resize_width, resize_height), Image.Resampling.LANCZOS)

        # Ensure output directory exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # Save as JPEG with specified quality
        output_jpeg = Path(output_path).with_suffix('.jpg')
        img.save(str(output_jpeg), 'JPEG', quality=quality, optimize=True)

        new_dimensions = img.size

    compressed_size = os.path.getsize(str(output_jpeg))

    return {
        'original_size': original_size,
        'compressed_size': compressed_size,
        'saved_bytes': original_size - compressed_size,
        'output_path': str(output_jpeg),
        'original_dimensions': original_dimensions,
        'new_dimensions': new_dimensions,
    }


def compress_images_in_folder(
    input_dir: str,
    output_dir: str,
    quality: int = 85,
    log_callback: Optional[Callable[[str, str, Optional[str]], None]] = None,
    stop_check: Optional[Callable[[], bool]] = None,
    resize_width: Optional[int] = None,
) -> dict:
    """
    Compress all images in a folder.

    Args:
        input_dir: Source directory containing images
        output_dir: Destination directory for compressed images
        quality: Compression quality (1-100)
        log_callback: Function to log messages (level, message, detail)
        stop_check: Function to check if operation should stop

    Returns:
        dict with total_files, compressed_files, skipped_files, total_saved_bytes
    """
    def log(level: str, message: str, detail: Optional[str] = None):
        if log_callback:
            log_callback(level, message, detail)

    input_path = Path(input_dir)
    output_path = Path(output_dir)

    if not input_path.exists():
        log("ERROR", f"入力フォルダーが存在しません: {input_dir}")
        return {
            'total_files': 0,
            'compressed_files': 0,
            'skipped_files': 0,
            'error_files': 0,
            'total_saved_bytes': 0,
        }

    # Create output directory
    output_path.mkdir(parents=True, exist_ok=True)
    log("INFO", f"画像圧縮処理開始: {input_dir} → {output_dir}")
    log("INFO", f"圧縮品質: {quality}")
    if resize_width:
        log("INFO", f"リサイズ設定: 横幅{resize_width}dpi (縦は比率保持)")

    stats = {
        'total_files': 0,
        'compressed_files': 0,
        'skipped_files': 0,
        'error_files': 0,
        'total_saved_bytes': 0,
    }

    # Walk through all files in input directory
    for root, dirs, files in os.walk(input_dir):
        if stop_check and stop_check():
            log("WARN", "画像圧縮処理が停止されました")
            break

        for filename in files:
            if stop_check and stop_check():
                break

            input_file = os.path.join(root, filename)

            if not is_image_file(input_file):
                continue

            stats['total_files'] += 1

            # Calculate relative path for output
            rel_path = os.path.relpath(input_file, input_dir)
            output_file = os.path.join(output_dir, rel_path)

            try:
                result = compress_image(input_file, output_file, quality, resize_width)
                stats['compressed_files'] += 1
                stats['total_saved_bytes'] += result['saved_bytes']

                original_kb = result['original_size'] / 1024
                compressed_kb = result['compressed_size'] / 1024
                saved_kb = result['saved_bytes'] / 1024
                orig_dim = result.get('original_dimensions', (0, 0))
                new_dim = result.get('new_dimensions', (0, 0))
                resize_info = f", {orig_dim[0]}×{orig_dim[1]} → {new_dim[0]}×{new_dim[1]}" if resize_width else ""
                log("INFO", f"圧縮完了: {rel_path} ({original_kb:.1f}KB → {compressed_kb:.1f}KB, 削減: {saved_kb:.1f}KB{resize_info})")

            except Exception as e:
                stats['error_files'] += 1
                log("ERROR", f"圧縮エラー: {rel_path}", detail=str(e))

    # Log final summary
    saved_mb = stats['total_saved_bytes'] / (1024 * 1024)
    log("INFO", f"画像圧縮処理完了: 合計={stats['total_files']}件, 圧縮={stats['compressed_files']}件, スキップ={stats['skipped_files']}件, エラー={stats['error_files']}件, 削減={saved_mb:.2f}MB")

    return stats


def watch_and_compress(
    input_dir: str,
    output_dir: str,
    quality: int = 85,
    log_callback: Optional[Callable[[str, str, Optional[str]], None]] = None,
    stop_check: Optional[Callable[[], bool]] = None,
    interval: float = 5.0,
    resize_width: Optional[int] = None,
) -> dict:
    """
    Continuously watch input folder and compress new/modified images.

    Args:
        input_dir: Source directory containing images
        output_dir: Destination directory for compressed images
        quality: Compression quality (1-100)
        log_callback: Function to log messages (level, message, detail)
        stop_check: Function to check if operation should stop
        interval: Seconds between each scan cycle

    Returns:
        dict with total stats from all cycles
    """
    import time

    def log(level: str, message: str, detail: Optional[str] = None):
        if log_callback:
            log_callback(level, message, detail)

    input_path = Path(input_dir)
    output_path = Path(output_dir)

    if not input_path.exists():
        log("ERROR", f"入力フォルダーが存在しません: {input_dir}")
        return {
            'total_files': 0,
            'compressed_files': 0,
            'skipped_files': 0,
            'error_files': 0,
            'total_saved_bytes': 0,
        }

    # Create output directory
    output_path.mkdir(parents=True, exist_ok=True)

    total_stats = {
        'total_files': 0,
        'compressed_files': 0,
        'skipped_files': 0,
        'error_files': 0,
        'total_saved_bytes': 0,
    }

    cycle_count = 0

    log("INFO", f"画像圧縮監視開始: {input_dir} → {output_dir}")
    resize_info = f", リサイズ: 横幅{resize_width}dpi (縦は比率保持)" if resize_width else ""
    log("INFO", f"監視間隔: {interval}秒, 圧縮品質: {quality}{resize_info}")

    while not (stop_check and stop_check()):
        cycle_count += 1
        cycle_compressed = 0
        cycle_skipped = 0
        cycle_errors = 0

        # Walk through all files in input directory
        for root, dirs, files in os.walk(input_dir):
            if stop_check and stop_check():
                break

            for filename in files:
                if stop_check and stop_check():
                    break

                input_file = os.path.join(root, filename)

                if not is_image_file(input_file):
                    continue

                # Calculate relative path for output
                rel_path = os.path.relpath(input_file, input_dir)
                output_file = os.path.join(output_dir, rel_path)
                output_jpeg = Path(output_file).with_suffix('.jpg')

                # Check if output file exists and is newer than input
                if output_jpeg.exists():
                    input_mtime = os.path.getmtime(input_file)
                    output_mtime = os.path.getmtime(str(output_jpeg))
                    if output_mtime >= input_mtime:
                        cycle_skipped += 1
                        continue

                try:
                    result = compress_image(input_file, output_file, quality, resize_width)
                    cycle_compressed += 1
                    total_stats['compressed_files'] += 1
                    total_stats['total_saved_bytes'] += result['saved_bytes']

                    original_kb = result['original_size'] / 1024
                    compressed_kb = result['compressed_size'] / 1024
                    saved_kb = result['saved_bytes'] / 1024
                    orig_dim = result.get('original_dimensions', (0, 0))
                    new_dim = result.get('new_dimensions', (0, 0))
                    dim_info = f", {orig_dim[0]}×{orig_dim[1]} → {new_dim[0]}×{new_dim[1]}" if resize_width else ""
                    log("INFO", f"圧縮完了: {rel_path} ({original_kb:.1f}KB → {compressed_kb:.1f}KB, 削減: {saved_kb:.1f}KB{dim_info})")

                except Exception as e:
                    cycle_errors += 1
                    total_stats['error_files'] += 1
                    log("ERROR", f"圧縮エラー: {rel_path}", detail=str(e))

        total_stats['skipped_files'] += cycle_skipped

        if cycle_compressed > 0 or cycle_errors > 0:
            log("INFO", f"[監視サイクル {cycle_count}] 圧縮={cycle_compressed}件, スキップ={cycle_skipped}件, エラー={cycle_errors}件")

        # Wait before next cycle
        if not (stop_check and stop_check()):
            for _ in range(int(interval * 10)):
                if stop_check and stop_check():
                    break
                time.sleep(0.1)

    log("WARN", f"画像圧縮監視停止: 合計{cycle_count}サイクル実行")
    return total_stats
