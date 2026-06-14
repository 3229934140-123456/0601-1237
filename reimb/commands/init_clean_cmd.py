from __future__ import annotations

import json
from pathlib import Path

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


def clean_task(task_dir: Path, keep_exports: bool = True,
               keep_receipts: bool = True) -> dict:
    state = load_task_state(task_dir)
    removed = []

    temp_dir = task_dir / TEMP_DIR
    if temp_dir.exists():
        for f in temp_dir.rglob("*"):
            if f.is_file():
                removed.append(f"temp/{f.name}")
                try:
                    f.unlink()
                except Exception:
                    pass

    log_dir = task_dir / LOG_DIR
    if log_dir.exists():
        for f in log_dir.iterdir():
            if f.suffix == ".tmp" or f.name.endswith(".bak"):
                removed.append(f"logs/{f.name}")
                try:
                    f.unlink()
                except Exception:
                    pass

    if not keep_exports:
        export_dir = task_dir / EXPORT_DIR
        if export_dir.exists():
            for f in export_dir.iterdir():
                if f.is_file():
                    removed.append(f"exports/{f.name}")
                    try:
                        f.unlink()
                    except Exception:
                        pass
        state.export_records = []

    if not keep_receipts:
        receipts_dir = task_dir / RECEIPTS_DIR
        if receipts_dir.exists():
            for f in receipts_dir.iterdir():
                if f.is_file():
                    removed.append(f"receipts/{f.name}")
                    try:
                        f.unlink()
                    except Exception:
                        pass
        state.receipts = []
        state.groups = {}
        state.status = TaskStatus.INIT.value

    save_task_state(task_dir, state)

    append_log(
        task_dir, "clean",
        f"清理完成，删除 {len(removed)} 个文件 (keep_exports={keep_exports}, keep_receipts={keep_receipts})"
    )
    return {"removed_count": len(removed), "removed_files": removed}


def get_config(task_dir: Path) -> dict:
    config = load_task_config(task_dir)
    state = load_task_state(task_dir)
    return {
        "config": config,
        "state": state,
    }
