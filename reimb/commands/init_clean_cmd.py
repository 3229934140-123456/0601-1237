from __future__ import annotations

import json
from pathlib import Path

from ..config import (
    init_task_dirs, save_task_state, load_task_state, save_task_config,
    load_task_config, TEMP_DIR, LOG_DIR, RECEIPTS_DIR, EXPORT_DIR,
    append_log,
)
from ..models import TaskState, TaskConfig, TaskStatus


def init_task(task_name: str, source_dir: str, employees: list[str] = None,
              projects: list[str] = None, base_dir: str = None) -> Path:
    task_dir = init_task_dirs(task_name, base_dir)

    config = TaskConfig(
        task_name=task_name,
        source_dir=source_dir,
        employee_list=employees or [],
        project_list=projects or [],
    )
    save_task_config(task_dir, config)

    state = TaskState(
        task_name=task_name,
        status=TaskStatus.INIT.value,
        config=config.to_dict(),
    )
    save_task_state(task_dir, state)

    return task_dir


def clean_task(task_dir: Path, keep_exports: bool = True) -> dict:
    state = load_task_state(task_dir)
    removed = []

    temp_dir = task_dir / TEMP_DIR
    if temp_dir.exists():
        for f in temp_dir.iterdir():
            f.unlink()
            removed.append(str(f.name))
        temp_dir.rmdir()
        temp_dir.mkdir(parents=True, exist_ok=True)

    log_dir = task_dir / LOG_DIR
    if log_dir.exists():
        for f in log_dir.iterdir():
            if f.suffix == ".tmp":
                f.unlink()
                removed.append(str(f.name))

    if not keep_exports:
        export_dir = task_dir / EXPORT_DIR
        if export_dir.exists():
            for f in export_dir.iterdir():
                f.unlink()
                removed.append(str(f.name))

    state_dict = state.to_dict()
    if state_dict.get("receipts"):
        for r in state_dict["receipts"]:
            r.pop("ocr_text", None)
        save_task_state(task_dir, state)

    append_log(task_dir, "clean", f"清理完成，删除 {len(removed)} 个临时文件")
    return {"removed_count": len(removed), "removed_files": removed}
