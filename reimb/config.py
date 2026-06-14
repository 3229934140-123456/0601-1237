from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from .models import TaskState, TaskConfig, ProcessLog


DEFAULT_TASK_DIR = Path.cwd() / "reimb_tasks"
TASK_STATE_FILE = "task_state.json"
TASK_CONFIG_FILE = "task_config.json"
RECEIPTS_DIR = "receipts"
TEMP_DIR = "temp"
EXPORT_DIR = "exports"
LOG_DIR = "logs"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}
PDF_EXTENSIONS = {".pdf"}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | PDF_EXTENSIONS


def get_task_dir(task_name: str, base_dir: Optional[str] = None) -> Path:
    base = Path(base_dir) if base_dir else DEFAULT_TASK_DIR
    return base / task_name


def init_task_dirs(task_name: str, base_dir: Optional[str] = None) -> Path:
    task_dir = get_task_dir(task_name, base_dir)
    (task_dir / RECEIPTS_DIR).mkdir(parents=True, exist_ok=True)
    (task_dir / TEMP_DIR).mkdir(parents=True, exist_ok=True)
    (task_dir / EXPORT_DIR).mkdir(parents=True, exist_ok=True)
    (task_dir / LOG_DIR).mkdir(parents=True, exist_ok=True)
    return task_dir


def save_task_state(task_dir: Path, state: TaskState) -> None:
    state.updated_at = __import__("datetime").datetime.now().isoformat()
    path = task_dir / TASK_STATE_FILE
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)


def load_task_state(task_dir: Path) -> TaskState:
    path = task_dir / TASK_STATE_FILE
    if not path.exists():
        raise FileNotFoundError(f"任务状态文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return TaskState.from_dict(data)


def save_task_config(task_dir: Path, config: TaskConfig) -> None:
    path = task_dir / TASK_CONFIG_FILE
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config.to_dict(), f, ensure_ascii=False, indent=2)


def load_task_config(task_dir: Path) -> TaskConfig:
    path = task_dir / TASK_CONFIG_FILE
    if not path.exists():
        raise FileNotFoundError(f"任务配置文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return TaskConfig.from_dict(data)


def append_log(task_dir: Path, action: str, detail: str, receipt_id: Optional[str] = None) -> None:
    state = load_task_state(task_dir)
    log = ProcessLog(action=action, detail=detail, receipt_id=receipt_id)
    state.logs.append(log.to_dict())
    save_task_state(task_dir, state)

    log_path = task_dir / LOG_DIR / "process.log"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{log.timestamp}] {action}: {detail}")
        if receipt_id:
            f.write(f" (receipt_id={receipt_id})")
        f.write("\n")


def is_image_file(filepath: Path) -> bool:
    return filepath.suffix.lower() in IMAGE_EXTENSIONS


def is_pdf_file(filepath: Path) -> bool:
    return filepath.suffix.lower() in PDF_EXTENSIONS


def is_supported_file(filepath: Path) -> bool:
    return filepath.suffix.lower() in SUPPORTED_EXTENSIONS
