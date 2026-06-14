from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..config import load_task_state, save_task_state, load_task_config, append_log
from ..models import Receipt, TaskStatus
from ..utils import group_by_project, filter_by_month


def group_receipts(task_dir: Path, month: Optional[str] = None) -> dict:
    state = load_task_state(task_dir)
    config = load_task_config(task_dir)

    if month:
        config.month_filter = month

    receipts = [Receipt.from_dict(r) for r in state.receipts]

    if config.month_filter:
        receipts = filter_by_month(receipts, config.month_filter)

    groups = group_by_project(receipts, config.project_list)

    groups_dict = {}
    for project, project_receipts in groups.items():
        groups_dict[project] = [r.to_dict() for r in project_receipts]

    state.groups = groups_dict
    state.status = TaskStatus.GROUPED.value
    save_task_state(task_dir, state)

    summary = {project: len(receipts_list) for project, receipts_list in groups.items()}

    filter_desc = f" (筛选月份: {config.month_filter})" if config.month_filter else ""
    append_log(
        task_dir, "group",
        f"归类完成{filter_desc}: " + ", ".join(f"{p}:{c}个" for p, c in summary.items())
    )
    return summary
