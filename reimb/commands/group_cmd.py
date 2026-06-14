from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..config import load_task_state, save_task_state, load_task_config, save_task_config, append_log
from ..models import Receipt, TaskStatus
from ..utils import group_by_project, filter_by_month


def group_receipts(task_dir: Path, month: Optional[str] = None,
                   use_current_rules: bool = True,
                   use_stored_month: bool = False) -> dict:
    state = load_task_state(task_dir)
    config = load_task_config(task_dir)

    if month:
        config.month_filter = month
        save_task_config(task_dir, config)

    active_month = month
    if active_month is None and use_stored_month and config.month_filter:
        active_month = config.month_filter

    receipts = [Receipt.from_dict(r) for r in state.receipts]
    original_count = len(receipts)

    if active_month:
        receipts = filter_by_month(receipts, active_month)

    project_keywords = config.get_project_keywords_dict()
    groups = group_by_project(receipts, config.project_list, project_keywords=project_keywords)

    groups_dict = {}
    for project, project_receipts in groups.items():
        groups_dict[project] = [r.to_dict() for r in project_receipts]

    for r_data in state.receipts:
        for r in receipts:
            if r_data.get("id") == r.id and r.project:
                r_data["project"] = r.project
                break

    state.groups = groups_dict
    state.status = TaskStatus.GROUPED.value
    if use_current_rules:
        state.config = config.to_dict()
    save_task_state(task_dir, state)

    summary = {project: len(receipts_list) for project, receipts_list in groups.items()}

    filter_desc = f" (筛选月份: {active_month})" if active_month else ""
    total_grouped = sum(summary.values())
    append_log(
        task_dir, "group",
        f"归类完成{filter_desc}: 共{original_count}张, 筛选后{len(receipts)}张, "
        + ", ".join(f"{p}:{c}张" for p, c in summary.items())
    )
    return {
        "summary": summary,
        "groups": groups,
        "month_filter": active_month,
        "total_original": original_count,
        "total_filtered": len(receipts),
    }
