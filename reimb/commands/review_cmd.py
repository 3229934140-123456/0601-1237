from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

from ..config import load_task_state, save_task_state, append_log, LOG_DIR, save_task_config, load_task_config
from ..models import Receipt, TaskStatus, ProcessLog, TaskConfig, ExportRecord


ALL_EDITABLE_FIELDS = [
    "date", "amount", "employee", "project", "receipt_type",
    "description", "risk_level", "is_duplicate", "is_missing_attachment",
]

FIELD_LABELS = {
    "date": "日期",
    "amount": "金额",
    "employee": "员工",
    "project": "项目",
    "receipt_type": "票据类型",
    "description": "备注",
    "risk_level": "风险等级",
    "is_duplicate": "是否重复",
    "is_missing_attachment": "是否缺附件",
}


def _parse_value(field: str, value: str) -> Any:
    if field == "amount":
        try:
            s = value.replace(",", "").replace("¥", "").replace("￥", "")
            return float(s)
        except ValueError:
            raise ValueError(f"金额格式错误: {value}")
    if field == "is_duplicate" or field == "is_missing_attachment":
        return value.lower() in ["true", "1", "yes", "是"]
    return value


def modify_field(task_dir: Path, receipt_id: str, field: str, new_value_str: str) -> Optional[dict]:
    state = load_task_state(task_dir)

    target_idx = None
    receipt = None
    for i, r_data in enumerate(state.receipts):
        if r_data.get("id") == receipt_id:
            target_idx = i
            receipt = Receipt.from_dict(r_data)
            break

    if receipt is None:
        return None

    if field not in ALL_EDITABLE_FIELDS:
        raise ValueError(
            f"无效字段: {field}. 可修改字段: {', '.join(ALL_EDITABLE_FIELDS)}"
        )

    parsed_value = _parse_value(field, new_value_str)
    old_value = getattr(receipt, field)

    if old_value == parsed_value:
        return {"unchanged": True, "receipt": receipt}

    mod = receipt.modify_field(field, parsed_value)

    state.receipts[target_idx] = receipt.to_dict()
    state.status = TaskStatus.REVIEWED.value
    save_task_state(task_dir, state)

    append_log(
        task_dir, "review",
        f"修改 {FIELD_LABELS.get(field, field)}: {old_value!r} -> {parsed_value!r}",
        receipt_id=receipt_id,
        field=field,
        old_value=old_value,
        new_value=parsed_value,
    )

    return {
        "unchanged": False,
        "receipt": receipt,
        "modification": mod.to_dict(),
    }


def get_modification_history(task_dir: Path, receipt_id: Optional[str] = None) -> list:
    state = load_task_state(task_dir)
    receipts = [Receipt.from_dict(r) for r in state.receipts]
    all_mods = []

    for r in receipts:
        if receipt_id and r.id != receipt_id:
            continue
        for mod in r.field_modifications:
            mod_dict = mod.to_dict() if hasattr(mod, "to_dict") else mod
            mod_dict["receipt_id"] = r.id
            mod_dict["filename"] = r.filename
            all_mods.append(mod_dict)

    all_mods.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return all_mods


def view_progress(task_dir: Path) -> dict:
    state = load_task_state(task_dir)
    receipts = [Receipt.from_dict(r) for r in state.receipts]

    total = len(receipts)
    status_counts = {}
    for r in receipts:
        status_counts[r.extraction_status] = status_counts.get(r.extraction_status, 0) + 1

    has_ocr = sum(1 for r in receipts if r.ocr_text)
    has_date = sum(1 for r in receipts if r.date)
    has_amount = sum(1 for r in receipts if r.amount)
    has_employee = sum(1 for r in receipts if r.employee)
    duplicates = sum(1 for r in receipts if r.is_duplicate)
    missing = sum(1 for r in receipts if r.is_missing_attachment)
    high_risk = sum(1 for r in receipts if r.risk_level == "高")
    modified = sum(1 for r in receipts if r.is_modified)

    status_order = ["已初始化", "已扫描", "已提取", "已检查", "已归类", "已导出", "已审核"]
    current_idx = status_order.index(state.status) if state.status in status_order else 0

    pipeline = []
    for step in status_order:
        pipeline.append((step, status_order.index(step) <= current_idx))

    export_records = []
    for er in state.export_records:
        if isinstance(er, ExportRecord):
            export_records.append(er.to_dict())
        else:
            export_records.append(er)
    export_records.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

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
        "status_counts": status_counts,
        "export_records": export_records[:5],
        "total_exports": len(export_records),
    }


def get_logs(task_dir: Path, limit: Optional[int] = None) -> list[dict]:
    state = load_task_state(task_dir)
    logs = [ProcessLog.from_dict(l) if isinstance(l, dict) else l for l in state.logs]
    log_dicts = [l.to_dict() if hasattr(l, "to_dict") else l for l in logs]
    if limit:
        log_dicts = log_dicts[-limit:]
    log_dicts.reverse()
    return log_dicts


def list_receipts_by_status(task_dir: Path, status_filter: Optional[str] = None) -> list[Receipt]:
    state = load_task_state(task_dir)
    receipts = [Receipt.from_dict(r) for r in state.receipts]
    if status_filter:
        receipts = [r for r in receipts if r.extraction_status == status_filter]
    return receipts
