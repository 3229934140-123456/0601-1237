from __future__ import annotations

from pathlib import Path
from typing import Optional
import json
import shutil

from ..config import (
    load_task_state, save_task_state, load_task_config, is_supported_file,
    is_image_file, is_pdf_file, RECEIPTS_DIR, append_log,
)
from ..models import Receipt, TaskStatus
from ..utils import compute_file_hash
from ..services.ocr import perform_ocr_image, perform_ocr_pdf


def scan_source_dir(task_dir: Path) -> list[Receipt]:
    state = load_task_state(task_dir)
    config = load_task_config(task_dir)
    source_dir = Path(config.source_dir)

    if not source_dir.exists():
        raise FileNotFoundError(f"源目录不存在: {source_dir}")

    receipts_dir = task_dir / RECEIPTS_DIR
    existing_hashes = {r.get("file_hash", "") for r in state.receipts}

    new_receipts = []
    file_count = 0
    for filepath in sorted(source_dir.rglob("*")):
        if not filepath.is_file():
            continue
        if not is_supported_file(filepath):
            continue

        file_hash = compute_file_hash(filepath)
        if file_hash in existing_hashes:
            continue

        dest = receipts_dir / filepath.name
        if dest.exists():
            base = dest.stem
            suffix = dest.suffix
            counter = 1
            while dest.exists():
                dest = receipts_dir / f"{base}_{counter}{suffix}"
                counter += 1
        shutil.copy2(filepath, dest)

        receipt = Receipt(
            filename=filepath.name,
            file_hash=file_hash,
            file_type="image" if is_image_file(filepath) else "pdf",
        )
        new_receipts.append(receipt)
        existing_hashes.add(file_hash)
        file_count += 1

    state.receipts.extend([r.to_dict() for r in new_receipts])
    state.status = TaskStatus.SCANNED.value
    save_task_state(task_dir, state)

    append_log(task_dir, "scan", f"扫描完成，发现 {file_count} 个新文件")
    return new_receipts
