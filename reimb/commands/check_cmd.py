from __future__ import annotations

from pathlib import Path

from ..config import load_task_state, save_task_state, load_task_config, append_log
from ..models import Receipt, TaskStatus, RiskLevel
from ..utils import (
    detect_duplicates, check_missing_attachments, assess_risk,
    determine_extraction_status,
)


def check_receipts(task_dir: Path, use_current_rules: bool = True, reset_status: bool = False) -> dict:
    state = load_task_state(task_dir)
    config = load_task_config(task_dir)

    receipts = [Receipt.from_dict(r) for r in state.receipts]

    if reset_status:
        for r in receipts:
            r.is_duplicate = False
            r.duplicate_of = None
            r.is_missing_attachment = False
            r.missing_attachments = []
            r.risk_level = RiskLevel.LOW.value
            r.risk_reason = ""

    attachment_rules = config.get_attachment_rules_dict()

    receipts = detect_duplicates(receipts, threshold=config.duplicate_threshold)
    receipts = check_missing_attachments(receipts, attachment_rules=attachment_rules)
    receipts = assess_risk(receipts, amount_threshold=config.amount_warning_threshold)

    for r in receipts:
        r.extraction_status = determine_extraction_status(r)

    state.receipts = [r.to_dict() for r in receipts]
    state.status = TaskStatus.CHECKED.value
    if use_current_rules:
        state.config = config.to_dict()
    save_task_state(task_dir, state)

    duplicate_count = sum(1 for r in receipts if r.is_duplicate)
    missing_count = sum(1 for r in receipts if r.is_missing_attachment)
    high_risk_count = sum(1 for r in receipts if r.risk_level == "高")
    medium_risk_count = sum(1 for r in receipts if r.risk_level == "中")

    status_counts = {}
    for r in receipts:
        status_counts[r.extraction_status] = status_counts.get(r.extraction_status, 0) + 1

    summary = {
        "total": len(receipts),
        "duplicates": duplicate_count,
        "missing_attachments": missing_count,
        "high_risk": high_risk_count,
        "medium_risk": medium_risk_count,
        "low_risk": len(receipts) - high_risk_count - medium_risk_count,
        "rule_version": config.rule_version,
        "status_counts": status_counts,
        "receipts": receipts,
    }

    duplicate_receipts = [r for r in receipts if r.is_duplicate]
    missing_receipts = [r for r in receipts if r.is_missing_attachment]
    high_risk_receipts = [r for r in receipts if r.risk_level == "高"]

    append_log(
        task_dir, "check",
        f"检查完成(规则v{config.rule_version}): 重复{duplicate_count}个, "
        f"缺附件{missing_count}个, 高风险{high_risk_count}个"
    )
    return summary
