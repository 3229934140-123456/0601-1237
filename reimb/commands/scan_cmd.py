from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional, Tuple
import shutil

from ..config import (
    load_task_state, save_task_state, load_task_config, is_supported_file,
    is_image_file, is_pdf_file, RECEIPTS_DIR, append_log,
)
from ..models import Receipt, TaskStatus, ExtractionStatus
from ..utils import compute_file_hash


def _resolve_conflict_name(receipts_dir: Path, filename: str, source_subdir: str) -> Tuple[Path, str]:
    if source_subdir:
        safe_subdir = source_subdir.replace("\\", "_").replace("/", "_")
        base = Path(filename)
        candidate = receipts_dir / f"{safe_subdir}_{base.name}"
        stored_name = candidate.name
    else:
        candidate = receipts_dir / filename
        stored_name = filename

    if candidate.exists():
        base = candidate.stem
        suffix = candidate.suffix
        counter = 1
        while True:
            candidate = receipts_dir / f"{base}_{counter}{suffix}"
            if not candidate.exists():
                stored_name = candidate.name
                break
            counter += 1
    return candidate, stored_name


def scan_source_dir(task_dir: Path) -> dict:
    state = load_task_state(task_dir)
    config = load_task_config(task_dir)
    source_dir = Path(config.source_dir)

    if not source_dir.exists():
        raise FileNotFoundError(f"源目录不存在: {source_dir}")

    receipts_dir = task_dir / RECEIPTS_DIR
    existing_hashes = {r.get("file_hash", "") for r in state.receipts}
    existing_ids = {r.get("id") for r in state.receipts}

    new_receipts = []
    skipped = 0
    total_files = 0

    for filepath in sorted(source_dir.rglob("*")):
        if not filepath.is_file():
            continue
        if not is_supported_file(filepath):
            continue

        total_files += 1
        file_hash = compute_file_hash(filepath)

        if file_hash in existing_hashes:
            skipped += 1
            continue

        try:
            rel_path = filepath.relative_to(source_dir)
            if rel_path.parent.name:
                source_subdir = str(rel_path.parent)
            else:
                source_subdir = ""
        except ValueError:
            source_subdir = filepath.parent.name

        dest, stored_name = _resolve_conflict_name(
            receipts_dir, filepath.name, source_subdir
        )
        shutil.copy2(filepath, dest)

        receipt = Receipt(
            filename=filepath.name,
            stored_filename=stored_name,
            source_path=str(filepath.resolve()),
            source_subdir=source_subdir,
            file_hash=file_hash,
            file_type="image" if is_image_file(filepath) else "pdf",
            extraction_status=ExtractionStatus.PENDING.value,
        )
        while receipt.id in existing_ids:
            import uuid
            receipt.id = uuid.uuid4().hex[:12]

        new_receipts.append(receipt)
        existing_hashes.add(file_hash)
        existing_ids.add(receipt.id)

    state.receipts.extend([r.to_dict() for r in new_receipts])
    state.status = TaskStatus.SCANNED.value
    save_task_state(task_dir, state)

    result = {
        "total_found": total_files,
        "added": len(new_receipts),
        "skipped": skipped,
        "new_receipts": new_receipts,
    }

    append_log(
        task_dir, "scan",
        f"扫描完成: 发现{total_files}个文件, 新增{len(new_receipts)}个, 跳过重复{skipped}个"
    )
    return result
