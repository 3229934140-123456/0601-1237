from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

from ..config import load_task_state, save_task_state, append_log, LOG_DIR, save_task_config, load_task_config, EXPORT_DIR
from ..models import Receipt, TaskStatus, ProcessLog, TaskConfig, ExportRecord
from ..utils import (
    format_amount, check_export_files_exist, get_available_batches,
    get_export_records_by_batch,
)


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


def get_batch_records(task_dir: Path, batch_id: str) -> Optional[dict]:
    state = load_task_state(task_dir)
    records = get_export_records_by_batch(state.export_records, batch_id)
    if not records:
        return None

    records_dicts = []
    for er in records:
        if isinstance(er, dict):
            d = dict(er)
        else:
            d = er.to_dict() if hasattr(er, "to_dict") else {}
        d["file_exists"] = Path(d.get("filepath", "")).exists()
        records_dicts.append(d)

    records_dicts.sort(key=lambda x: x.get("export_type", ""))

    first_ts = min(r.get("timestamp", "") for r in records_dicts)
    operation = records_dicts[0].get("operation", "export") if records_dicts else "export"
    operator = records_dicts[0].get("operator", "财务人员") if records_dicts else "财务人员"

    months = sorted({r.get("month_filter") for r in records_dicts if r.get("month_filter")})

    monthly_breakdown = {}
    for m in months:
        m_records = [r for r in records_dicts if r.get("month_filter") == m]
        monthly_breakdown[m] = {
            "record_count": max(r.get("record_count", 0) or 0 for r in m_records),
            "total_amount": max(r.get("total_amount", 0) or 0 for r in m_records),
            "file_count": len(m_records),
            "valid_files": sum(1 for r in m_records if r.get("file_exists")),
            "files": m_records,
        }

    if len(months) > 1:
        total_amount = sum(m["total_amount"] for m in monthly_breakdown.values())
        record_count = sum(m["record_count"] for m in monthly_breakdown.values())
    else:
        total_amount = max(r.get("total_amount", 0) or 0 for r in records_dicts)
        record_count = max(r.get("record_count", 0) or 0 for r in records_dicts)

    month_filter = months[0] if len(months) == 1 else None
    month_range = f"{months[0]}~{months[-1]}" if len(months) > 1 else (months[0] if months else "-")

    return {
        "batch_id": batch_id,
        "record_count": len(records_dicts),
        "records": records_dicts,
        "first_timestamp": first_ts,
        "total_amount": total_amount,
        "record_count_receipt": record_count,
        "month_filter": month_filter,
        "month_range": month_range,
        "months": months,
        "operation": operation,
        "operator": operator,
        "valid_files": sum(1 for r in records_dicts if r.get("file_exists")),
        "monthly_breakdown": monthly_breakdown,
    }


def list_batches(task_dir: Path) -> list[dict]:
    state = load_task_state(task_dir)
    batches = get_available_batches(state.export_records)
    for b in batches:
        recs = get_export_records_by_batch(state.export_records, b["batch_id"])
        valid = 0
        for er in recs:
            fp = er.get("filepath", "") if isinstance(er, dict) else (er.filepath if hasattr(er, "filepath") else "")
            if Path(fp).exists():
                valid += 1
        b["valid_files"] = valid
    return batches


def get_handover_view(task_dir: Path) -> dict:
    state = load_task_state(task_dir)
    receipts = [Receipt.from_dict(r) for r in state.receipts]
    batches = list_batches(task_dir)

    handover_batches = []
    for b in batches:
        status = "待核对"
        if b.get("valid_files", 0) == b.get("file_count", 0) and b.get("file_count", 0) > 0:
            status = "已完成"
        elif b.get("valid_files", 0) < b.get("file_count", 0):
            status = "文件缺失"

        handover_batches.append({
            "batch_id": b["batch_id"],
            "operation": b["operation"],
            "operator": b.get("operator", "财务人员"),
            "timestamp": b["first_timestamp"],
            "month_range": b.get("month_range", "-"),
            "file_count": b["file_count"],
            "valid_files": b.get("valid_files", 0),
            "record_count": b["record_count"],
            "total_amount": b["total_amount"],
            "status": status,
            "note": "",
        })

    total_receipts = len(receipts)
    total_amount = sum(r.amount or 0 for r in receipts)
    high_risk = sum(1 for r in receipts if r.risk_level == "高")
    medium_risk = sum(1 for r in receipts if r.risk_level == "中")
    duplicates = sum(1 for r in receipts if r.is_duplicate)
    missing = sum(1 for r in receipts if r.is_missing_attachment)
    modified = sum(1 for r in receipts if r.is_modified)

    return {
        "task_name": state.task_name,
        "total_receipts": total_receipts,
        "total_amount": total_amount,
        "high_risk": high_risk,
        "medium_risk": medium_risk,
        "duplicates": duplicates,
        "missing_attachments": missing,
        "modified": modified,
        "batches": handover_batches,
    }


def export_handover_list(task_dir: Path, output_path: Optional[Path] = None) -> Path:
    import csv
    handover = get_handover_view(task_dir)

    if output_path is None:
        export_dir = task_dir / EXPORT_DIR
        export_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = export_dir / f"交接台账_{timestamp}.csv"

    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)

        writer.writerow(["=== 报销任务交接台账 ==="])
        writer.writerow([])
        writer.writerow(["任务名称", handover["task_name"]])
        writer.writerow(["导出时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
        writer.writerow([])

        writer.writerow(["=== 任务概览 ==="])
        writer.writerow(["票据总数", handover["total_receipts"]])
        writer.writerow(["总金额", format_amount(handover["total_amount"])])
        writer.writerow(["高风险项", handover["high_risk"]])
        writer.writerow(["中风险项", handover["medium_risk"]])
        writer.writerow(["重复票据", handover["duplicates"]])
        writer.writerow(["缺附件", handover["missing_attachments"]])
        writer.writerow(["已修改", handover["modified"]])
        writer.writerow([])

        writer.writerow(["=== 批次清单 ==="])
        writer.writerow([
            "批次号", "操作类型", "经办人", "生成时间", "覆盖月份",
            "文件数", "有效文件", "票据数", "总金额", "状态", "核对结论",
        ])
        for b in handover["batches"]:
            op_labels = {
                "export": "普通导出",
                "export_month": "按月导出",
                "archive_month": "月度归档",
                "report": "汇总报告",
            }
            op_label = op_labels.get(b["operation"], b["operation"])
            writer.writerow([
                b["batch_id"],
                op_label,
                b["operator"],
                b["timestamp"][:19],
                b["month_range"],
                b["file_count"],
                f"{b['valid_files']}/{b['file_count']}",
                b["record_count"],
                format_amount(b["total_amount"]),
                b["status"],
                b["note"],
            ])
        writer.writerow([])

        writer.writerow(["=== 核对说明 ==="])
        writer.writerow(["1. 请核对每个批次的文件是否完整存在"])
        writer.writerow(["2. 请核对票据数和总金额是否与实际一致"])
        writer.writerow(["3. 高风险项需重点复核"])
        writer.writerow(["4. 核对无误后在\"核对结论\"列签字确认"])

    return output_path


def compare_batches(task_dir: Path, batch_id1: str, batch_id2: str) -> Optional[dict]:
    state = load_task_state(task_dir)
    all_receipts = [Receipt.from_dict(r) for r in state.receipts]

    b1 = get_batch_records(task_dir, batch_id1)
    b2 = get_batch_records(task_dir, batch_id2)
    if not b1 or not b2:
        return None

    months1 = set(b1.get("months", []))
    months2 = set(b2.get("months", []))

    all_months = sorted({r.date[:7] for r in all_receipts if r.date})
    m1_effective = months1 if months1 else set(all_months)
    m2_effective = months2 if months2 else set(all_months)
    common_months = sorted(m1_effective & m2_effective)

    def get_receipt_ids_for_batch(batch_data):
        months = batch_data.get("months", [])
        if not months:
            return {r.id for r in all_receipts}
        ids = set()
        for r in all_receipts:
            if r.date and any(r.date.startswith(m) for m in months):
                ids.add(r.id)
        return ids

    ids1 = get_receipt_ids_for_batch(b1)
    ids2 = get_receipt_ids_for_batch(b2)

    only_in_1 = ids1 - ids2
    only_in_2 = ids2 - ids1
    common = ids1 & ids2

    changed_receipts = []
    for rid in common:
        r = next((x for x in all_receipts if x.id == rid), None)
        if r:
            mods = r.field_modifications or []
            if mods:
                changed_receipts.append({
                    "receipt_id": rid,
                    "filename": r.filename,
                    "modifications": [m.to_dict() if hasattr(m, "to_dict") else m for m in mods],
                })

    def summarize(ids, receipts_list):
        rs = [r for r in all_receipts if r.id in ids]
        return {
            "count": len(rs),
            "total_amount": sum(r.amount or 0 for r in rs),
            "high_risk": sum(1 for r in rs if r.risk_level == "高"),
            "medium_risk": sum(1 for r in rs if r.risk_level == "中"),
            "duplicates": sum(1 for r in rs if r.is_duplicate),
            "missing_attachments": sum(1 for r in rs if r.is_missing_attachment),
            "receipts": [{
                "id": r.id,
                "filename": r.filename,
                "date": r.date,
                "amount": r.amount,
                "employee": r.employee,
                "project": r.project,
                "risk_level": r.risk_level,
                "risk_reason": r.risk_reason,
            } for r in rs],
        }

    return {
        "batch1": {
            "batch_id": batch_id1,
            "operation": b1["operation"],
            "timestamp": b1["first_timestamp"],
            "month_range": b1.get("month_range", "-"),
            "total_amount": b1["total_amount"],
            "record_count": b1["record_count_receipt"],
        },
        "batch2": {
            "batch_id": batch_id2,
            "operation": b2["operation"],
            "timestamp": b2["first_timestamp"],
            "month_range": b2.get("month_range", "-"),
            "total_amount": b2["total_amount"],
            "record_count": b2["record_count_receipt"],
        },
        "common_months": common_months,
        "only_in_batch1": summarize(only_in_1, all_receipts),
        "only_in_batch2": summarize(only_in_2, all_receipts),
        "common_count": len(common),
        "changed_receipts": changed_receipts,
        "amount_diff": b2["total_amount"] - b1["total_amount"],
        "count_diff": b2["record_count_receipt"] - b1["record_count_receipt"],
    }


def view_progress(task_dir: Path, include_all_exports: bool = False,
                  check_file_exists: bool = True) -> dict:
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
    medium_risk = sum(1 for r in receipts if r.risk_level == "中")
    modified = sum(1 for r in receipts if r.is_modified)

    status_order = ["已初始化", "已扫描", "已提取", "已检查", "已归类", "已导出", "已审核"]
    current_idx = status_order.index(state.status) if state.status in status_order else 0

    pipeline = []
    for step in status_order:
        pipeline.append((step, status_order.index(step) <= current_idx))

    export_records_checked = check_export_files_exist(state.export_records) if check_file_exists else [
        (dict(er) if isinstance(er, dict) else er.to_dict())
        for er in state.export_records
    ]
    export_records_checked.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

    if check_file_exists:
        valid_count = sum(1 for er in export_records_checked if er.get("file_exists"))
        missing_count = len(export_records_checked) - valid_count
    else:
        valid_count = len(export_records_checked)
        missing_count = 0

    export_limit = len(export_records_checked) if include_all_exports else 10

    operation_labels = {
        "export": "普通导出",
        "export_month": "按月导出",
        "archive_month": "月度归档",
        "report": "汇总报告",
    }
    for er in export_records_checked:
        op = er.get("operation", "export")
        er["operation_label"] = operation_labels.get(op, op)

    batches = get_available_batches(state.export_records)

    total_amount_all = sum(r.amount or 0 for r in receipts)

    month_distribution = {}
    for r in receipts:
        if r.date:
            m = r.date[:7]
            month_distribution[m] = month_distribution.get(m, 0) + 1

    return {
        "task_name": state.task_name,
        "status": state.status,
        "rule_version": state.config.get("rule_version", 1) if state.config else 1,
        "batch_counter": state.batch_counter,
        "total_receipts": total,
        "total_amount": total_amount_all,
        "ocr_completed": has_ocr,
        "date_extracted": has_date,
        "amount_extracted": has_amount,
        "employee_matched": has_employee,
        "duplicates": duplicates,
        "missing_attachments": missing,
        "high_risk": high_risk,
        "medium_risk": medium_risk,
        "modified": modified,
        "pipeline": pipeline,
        "log_count": len(state.logs),
        "status_counts": status_counts,
        "export_records": export_records_checked[:export_limit],
        "total_exports": len(export_records_checked),
        "valid_exports": valid_count,
        "missing_exports": missing_count,
        "batches": batches,
        "month_distribution": sorted(month_distribution.items()),
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
