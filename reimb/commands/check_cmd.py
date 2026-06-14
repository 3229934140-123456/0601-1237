from __future__ import annotations

from pathlib import Path

from ..config import load_task_state, save_task_state, load_task_config, append_log
from ..models import Receipt, TaskStatus
from ..utils import detect_duplicates, check_missing_attachments, assess_risk


def check_receipts(task_dir: Path) -> dict:
    state = load_task_state(task_dir)
    config = load_task_config(task_dir)

    receipts = [Receipt.from_dict(r) for r in state.receipts]

    receipts = detect_duplicates(receipts, threshold=config.duplicate_threshold)
    receipts = check_missing_attachments(receipts)
    receipts = assess_risk(receipts, amount_threshold=config.amount_warning_threshold)

    state.receipts = [r.to_dict() for r in receipts]
    state.status = TaskStatus.CHECKED.value
    save_task_state(task_dir, state)

    duplicate_count = sum(1 for r in receipts if r.is_duplicate)
    missing_count = sum(1 for r in receipts if r.is_missing_attachment)
    high_risk_count = sum(1 for r in receipts if r.risk_level == "高")
    medium_risk_count = sum(1 for r in receipts if r.risk_level == "中")

    summary = {
        "total": len(receipts),
        "duplicates": duplicate_count,
        "missing_attachments": missing_count,
        "high_risk": high_risk_count,
        "medium_risk": medium_risk_count,
        "low_risk": len(receipts) - high_risk_count - medium_risk_count,
    }

    append_log(
        task_dir, "check",
        f"检查完成: 重复{duplicate_count}个, 缺附件{missing_count}个, 高风险{high_risk_count}个"
    )
    return summary
