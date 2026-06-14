from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ..config import (
    init_task_dirs, save_task_state, load_task_state, save_task_config,
    load_task_config, TEMP_DIR, LOG_DIR, RECEIPTS_DIR, EXPORT_DIR,
    append_log,
)
from ..models import TaskState, TaskConfig, TaskStatus, ProjectKeyword, AttachmentRule, ReceiptType


DEFAULT_ATTACHMENT_RULES = [
    AttachmentRule(receipt_type=ReceiptType.FLIGHT_TICKET.value, required_attachments=["行程单", "审批单"]),
    AttachmentRule(receipt_type=ReceiptType.HOTEL.value, required_attachments=["入住确认单", "审批单"]),
    AttachmentRule(receipt_type=ReceiptType.INVOICE.value, required_attachments=["合同", "审批单"]),
]


CLEAN_MODE_EXPORTS = "exports_only"
CLEAN_MODE_OCR = "ocr_only"
CLEAN_MODE_RESET = "reset_full"
CLEAN_MODE_TEMP = "temp_only"


def init_task(task_name: str, source_dir: str, employees: list[str] = None,
              projects: list[str] = None, base_dir: str = None) -> Path:
    task_dir = init_task_dirs(task_name, base_dir)

    default_keywords = [ProjectKeyword(project=p, keywords=[p]) for p in (projects or [])]

    config = TaskConfig(
        task_name=task_name,
        source_dir=source_dir,
        employee_list=employees or [],
        project_list=projects or [],
        project_keywords=default_keywords,
        attachment_rules=list(DEFAULT_ATTACHMENT_RULES),
    )
    save_task_config(task_dir, config)

    state = TaskState(
        task_name=task_name,
        status=TaskStatus.INIT.value,
        config=config.to_dict(),
    )
    save_task_state(task_dir, state)

    return task_dir


def _delete_files_in_dir(dir_path: Path, removed: list, prefix: str,
                         pattern: Optional[str] = None) -> None:
    if not dir_path.exists():
        return
    for f in dir_path.iterdir():
        if f.is_file():
            if pattern and not f.match(pattern):
                continue
            removed.append(f"{prefix}/{f.name}")
            try:
                f.unlink()
            except Exception:
                pass


def clean_task(task_dir: Path, mode: str = CLEAN_MODE_TEMP) -> dict:
    state = load_task_state(task_dir)
    removed = []
    mode_description = ""

    if mode == CLEAN_MODE_EXPORTS:
        mode_description = "仅清理导出文件"
        export_dir = task_dir / EXPORT_DIR
        _delete_files_in_dir(export_dir, removed, "exports")
        state.export_records = []

    elif mode == CLEAN_MODE_OCR:
        mode_description = "仅清理OCR识别结果"
        for r_data in state.receipts:
            r_data["ocr_text"] = ""
            r_data["date"] = None
            r_data["amount"] = None
            r_data["employee"] = None
            r_data["project"] = None
            r_data["receipt_type"] = ReceiptType.OTHER.value
            r_data["extraction_status"] = "待处理"
            r_data["is_duplicate"] = False
            r_data["duplicate_of"] = None
            r_data["is_missing_attachment"] = False
            r_data["missing_attachments"] = []
            r_data["risk_level"] = "无"
            r_data["risk_reason"] = ""
            r_data["is_modified"] = False
            r_data["field_modifications"] = []
            r_data["description"] = None
        state.groups = {}
        state.status = TaskStatus.SCANNED.value
        temp_dir = task_dir / TEMP_DIR
        _delete_files_in_dir(temp_dir, removed, "temp")

    elif mode == CLEAN_MODE_RESET:
        mode_description = "完全重置为刚初始化状态"
        export_dir = task_dir / EXPORT_DIR
        _delete_files_in_dir(export_dir, removed, "exports")
        receipts_dir = task_dir / RECEIPTS_DIR
        _delete_files_in_dir(receipts_dir, removed, "receipts")
        temp_dir = task_dir / TEMP_DIR
        _delete_files_in_dir(temp_dir, removed, "temp")
        log_dir = task_dir / LOG_DIR
        _delete_files_in_dir(log_dir, removed, "logs")
        state.receipts = []
        state.groups = {}
        state.export_records = []
        state.logs = []
        state.status = TaskStatus.INIT.value
        config = load_task_config(task_dir)
        state.config = config.to_dict()

    else:
        mode_description = "仅清理临时文件"
        temp_dir = task_dir / TEMP_DIR
        if temp_dir.exists():
            for f in temp_dir.rglob("*"):
                if f.is_file():
                    removed.append(f"temp/{f.name}")
                    try:
                        f.unlink()
                    except Exception:
                        pass

    save_task_state(task_dir, state)

    append_log(
        task_dir, "clean",
        f"{mode_description}: 删除 {len(removed)} 个文件"
    )
    return {
        "removed_count": len(removed),
        "removed_files": removed,
        "mode": mode,
        "mode_description": mode_description,
    }


def get_config(task_dir: Path) -> dict:
    config = load_task_config(task_dir)
    state = load_task_state(task_dir)
    return {
        "config": config,
        "state": state,
    }
