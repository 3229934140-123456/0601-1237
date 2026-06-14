from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from ..config import load_task_state, save_task_state, load_task_config, RECEIPTS_DIR, append_log
from ..models import Receipt, TaskStatus, ExtractionStatus
from ..services.ocr import perform_ocr_image, perform_ocr_pdf
from ..utils import (
    extract_date_from_text, extract_amount_from_text,
    match_employee_name, classify_receipt_type, determine_extraction_status,
)


def extract_receipts(task_dir: Path, force: bool = False) -> dict:
    state = load_task_state(task_dir)
    config = load_task_config(task_dir)
    receipts_dir = task_dir / RECEIPTS_DIR

    updated_receipts = []
    stats = {
        "total": 0,
        "processed": 0,
        "success": 0,
        "partial": 0,
        "failed": 0,
        "skipped": 0,
    }

    for r_data in state.receipts:
        receipt = Receipt.from_dict(r_data)
        stats["total"] += 1

        if receipt.ocr_text and not force and not receipt.extraction_error:
            stats["skipped"] += 1
            updated_receipts.append(receipt.to_dict())
            continue

        filepath = receipts_dir / receipt.stored_filename
        if not filepath.exists():
            filepath = receipts_dir / receipt.filename
        if not filepath.exists():
            receipt.extraction_error = "文件不存在"
            receipt.extraction_status = ExtractionStatus.FAILED.value
            stats["failed"] += 1
            updated_receipts.append(receipt.to_dict())
            continue

        receipt.extraction_status = ExtractionStatus.PROCESSING.value
        try:
            if receipt.file_type == "image":
                receipt.ocr_text = perform_ocr_image(filepath)
            elif receipt.file_type == "pdf":
                receipt.ocr_text = perform_ocr_pdf(filepath)
            receipt.extraction_error = ""
        except Exception as e:
            receipt.extraction_error = str(e)
            receipt.extraction_status = ExtractionStatus.FAILED.value
            receipt.ocr_text = receipt.ocr_text or ""
            stats["failed"] += 1
            updated_receipts.append(receipt.to_dict())
            continue

        receipt.receipt_type = classify_receipt_type(receipt.ocr_text)
        receipt.date = extract_date_from_text(receipt.ocr_text)
        receipt.amount = extract_amount_from_text(receipt.ocr_text)

        if config.employee_list:
            receipt.employee = match_employee_name(receipt.ocr_text, config.employee_list)

        if config.project_list:
            kw_dict = config.get_project_keywords_dict()
            text_lower = receipt.ocr_text.lower()
            for project, keywords in kw_dict.items():
                for kw in keywords:
                    if kw.lower() in text_lower:
                        receipt.project = project
                        break
                if receipt.project:
                    break

        receipt.extraction_status = determine_extraction_status(receipt)
        receipt.extracted_at = datetime.now().isoformat()

        if receipt.extraction_status == ExtractionStatus.SUCCESS.value:
            stats["success"] += 1
        elif receipt.extraction_status == ExtractionStatus.PARTIAL.value:
            stats["partial"] += 1
        elif receipt.extraction_status == ExtractionStatus.FAILED.value:
            stats["failed"] += 1

        stats["processed"] += 1
        updated_receipts.append(receipt.to_dict())

    state.receipts = updated_receipts
    state.status = TaskStatus.EXTRACTED.value
    save_task_state(task_dir, state)

    append_log(
        task_dir, "extract",
        f"提取完成: 处理{stats['processed']}个, 成功{stats['success']}个, "
        f"部分{stats['partial']}个, 失败{stats['failed']}个, 跳过{stats['skipped']}个"
    )

    return {
        "stats": stats,
        "receipts": [Receipt.from_dict(r) for r in updated_receipts],
    }
