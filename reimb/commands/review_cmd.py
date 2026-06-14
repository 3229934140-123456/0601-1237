from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..config import load_task_state, save_task_state, append_log, LOG_DIR
from ..models import Receipt, TaskStatus, ProcessLog


def modify_field(task_dir: Path, receipt_id: str, field: str, new_value: str) -> Optional[Receipt]:
    state = load_task_state(task_dir)

    target = None
    for i, r_data in enumerate(state.receipts):
        if r_data.get("id") == receipt_id:
            target = (i, Receipt.from_dict(r_data))
            break

    if target is None:
        return None

    idx, receipt = target

    if not hasattr(receipt, field):
        raise ValueError(f"无效字段: {field}")

    old_value = getattr(receipt, field)
    setattr(receipt, field, new_value)
    receipt.is_modified = True
    if field not in receipt.modified_fields:
        receipt.modified_fields.append(field)

    state.receipts[idx] = receipt.to_dict()
    state.status = TaskStatus.REVIEWED.value
    save_task_state(task_dir, state)

    append_log(
        task_dir, "review",
        f"修改字段: {field}, 旧值={old_value}, 新值={new_value}",
        receipt_id=receipt_id,
    )
    return receipt


def view_progress(task_dir: Path) -> dict:
    state = load_task_state(task_dir)
    receipts = [Receipt.from_dict(r) for r in state.receipts]

    total = len(receipts)
    has_ocr = sum(1 for r in receipts if r.ocr_text)
    has_date = sum(1 for r in receipts if r.date)
    has_amount = sum(1 for r in receipts if r.amount)
    has_employee = sum(1 for r in receipts if r.employee)
    duplicates = sum(1 for r in receipts if r.is_duplicate)
    missing = sum(1 for r in receipts if r.is_missing_attachment)
    high_risk = sum(1 for r in receipts if r.risk_level == "高")
    modified = sum(1 for r in receipts if r.is_modified)

    pipeline = [
        ("初始化", True),
        ("扫描文件", state.status in [s.value for s in TaskStatus if s.value != "已初始化"]),
        ("提取信息", state.status in ["已提取", "已检查", "已归类", "已导出", "已审核"]),
        ("检查问题", state.status in ["已检查", "已归类", "已导出", "已审核"]),
        ("项目归类", state.status in ["已归类", "已导出", "已审核"]),
        ("导出结果", state.status in ["已导出", "已审核"]),
        ("人工审核", state.status == "已审核"),
    ]

    return {
        "task_name": state.task_name,
        "status": state.status,
        "total_receipts": total,
        "ocr_completed": has_ocr,
        "date_extracted": has_date,
        "amount_extracted": has_amount,
        "employee_matched": has_employee,
        "duplicates": duplicates,
        "missing_attachments": missing,
        "high_risk": high_risk,
        "modified": modified,
        "pipeline": pipeline,
        "log_count": len(state.logs),
    }


def get_logs(task_dir: Path, limit: Optional[int] = None) -> list[dict]:
    state = load_task_state(task_dir)
    logs = state.logs
    if limit:
        logs = logs[-limit:]
    return logs
