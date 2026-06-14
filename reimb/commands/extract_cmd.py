from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..config import load_task_state, save_task_state, load_task_config, RECEIPTS_DIR, append_log
from ..models import Receipt, TaskStatus
from ..services.ocr import perform_ocr_image, perform_ocr_pdf
from ..utils import (
    extract_date_from_text, extract_amount_from_text,
    match_employee_name, classify_receipt_type,
)


def extract_receipts(task_dir: Path) -> list[Receipt]:
    state = load_task_state(task_dir)
    config = load_task_config(task_dir)
    receipts_dir = task_dir / RECEIPTS_DIR

    updated_receipts = []
    extracted_count = 0

    for r_data in state.receipts:
        receipt = Receipt.from_dict(r_data)

        if receipt.ocr_text:
            updated_receipts.append(receipt.to_dict())
            continue

        filepath = receipts_dir / receipt.filename
        if not filepath.exists():
            updated_receipts.append(receipt.to_dict())
            continue

        if receipt.file_type == "image":
            receipt.ocr_text = perform_ocr_image(filepath)
        elif receipt.file_type == "pdf":
            receipt.ocr_text = perform_ocr_pdf(filepath)

        receipt.receipt_type = classify_receipt_type(receipt.ocr_text)
        receipt.date = extract_date_from_text(receipt.ocr_text)
        receipt.amount = extract_amount_from_text(receipt.ocr_text)

        if config.employee_list:
            receipt.employee = match_employee_name(receipt.ocr_text, config.employee_list)

        if config.project_list:
            for project in config.project_list:
                if project.lower() in receipt.ocr_text.lower():
                    receipt.project = project
                    break

        updated_receipts.append(receipt.to_dict())
        extracted_count += 1

    state.receipts = updated_receipts
    state.status = TaskStatus.EXTRACTED.value
    save_task_state(task_dir, state)

    append_log(task_dir, "extract", f"提取完成，处理 {extracted_count} 个票据")
    return [Receipt.from_dict(r) for r in updated_receipts]
