from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..config import load_task_state, load_task_config, EXPORT_DIR, append_log
from ..models import Receipt
from ..utils import format_amount


def export_to_excel(task_dir: Path) -> Path:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    except ImportError:
        return _export_to_csv(task_dir)

    state = load_task_state(task_dir)
    config = load_task_config(task_dir)
    export_dir = task_dir / EXPORT_DIR
    export_dir.mkdir(parents=True, exist_ok=True)

    wb = Workbook()

    ws_receipts = wb.active
    ws_receipts.title = "报销清单"

    headers = ["序号", "文件名", "票据类型", "日期", "金额", "员工", "项目",
               "是否重复", "缺附件", "风险等级", "风险原因", "备注"]
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True, size=11)
    thin_border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin"),
    )

    for col, header in enumerate(headers, 1):
        cell = ws_receipts.cell(row=1, column=col, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")
        cell.border = thin_border

    receipts = [Receipt.from_dict(r) for r in state.receipts]
    high_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    medium_fill = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")

    for i, receipt in enumerate(receipts, 1):
        row_data = [
            i, receipt.filename, receipt.receipt_type,
            receipt.date or "", format_amount(receipt.amount),
            receipt.employee or "", receipt.project or "",
            "是" if receipt.is_duplicate else "否",
            "是" if receipt.is_missing_attachment else "否",
            receipt.risk_level, receipt.risk_reason, receipt.description,
        ]
        row_num = i + 1
        for col, value in enumerate(row_data, 1):
            cell = ws_receipts.cell(row=row_num, column=col, value=value)
            cell.border = thin_border
            if receipt.risk_level == "高":
                cell.fill = high_fill
            elif receipt.risk_level == "中":
                cell.fill = medium_fill

    for col in range(1, len(headers) + 1):
        ws_receipts.column_dimensions[chr(64 + col)].width = 15

    if state.groups:
        ws_groups = wb.create_sheet("项目归类")
        group_headers = ["项目", "票据数", "总金额", "高风险数"]
        for col, header in enumerate(group_headers, 1):
            cell = ws_groups.cell(row=1, column=col, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")

        for row_idx, (project, r_list) in enumerate(state.groups.items(), 2):
            project_receipts = [Receipt.from_dict(r) for r in r_list]
            total = sum(r.amount or 0 for r in project_receipts)
            high_count = sum(1 for r in project_receipts if r.risk_level == "高")
            ws_groups.cell(row=row_idx, column=1, value=project)
            ws_groups.cell(row=row_idx, column=2, value=len(project_receipts))
            ws_groups.cell(row=row_idx, column=3, value=format_amount(total))
            ws_groups.cell(row=row_idx, column=4, value=high_count)

    ws_diff = wb.create_sheet("差异说明")
    diff_headers = ["文件名", "差异项", "原始值", "说明"]
    for col, header in enumerate(diff_headers, 1):
        cell = ws_diff.cell(row=1, column=col, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")

    row_idx = 2
    for receipt_data in state.receipts:
        receipt = Receipt.from_dict(receipt_data)
        if not receipt.date:
            ws_diff.cell(row=row_idx, column=1, value=receipt.filename)
            ws_diff.cell(row=row_idx, column=2, value="日期")
            ws_diff.cell(row=row_idx, column=3, value="(空)")
            ws_diff.cell(row=row_idx, column=4, value="未能识别日期信息")
            row_idx += 1
        if not receipt.amount:
            ws_diff.cell(row=row_idx, column=1, value=receipt.filename)
            ws_diff.cell(row=row_idx, column=2, value="金额")
            ws_diff.cell(row=row_idx, column=3, value="(空)")
            ws_diff.cell(row=row_idx, column=4, value="未能识别金额信息")
            row_idx += 1
        if receipt.is_duplicate:
            ws_diff.cell(row=row_idx, column=1, value=receipt.filename)
            ws_diff.cell(row=row_idx, column=2, value="重复")
            ws_diff.cell(row=row_idx, column=3, value=f"与{receipt.duplicate_of}重复")
            ws_diff.cell(row=row_idx, column=4, value="文件哈希或内容高度相似")
            row_idx += 1
        if receipt.is_missing_attachment:
            ws_diff.cell(row=row_idx, column=1, value=receipt.filename)
            ws_diff.cell(row=row_idx, column=2, value="缺附件")
            ws_diff.cell(row=row_idx, column=3, value=",".join(receipt.missing_attachments))
            ws_diff.cell(row=row_idx, column=4, value="缺少必要附件")
            row_idx += 1

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = export_dir / f"报销清单_{timestamp}.xlsx"
    wb.save(filepath)

    append_log(task_dir, "export", f"导出Excel: {filepath.name}")
    return filepath


def _export_to_csv(task_dir: Path) -> Path:
    import csv
    state = load_task_state(task_dir)
    export_dir = task_dir / EXPORT_DIR
    export_dir.mkdir(parents=True, exist_ok=True)

    receipts = [Receipt.from_dict(r) for r in state.receipts]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = export_dir / f"报销清单_{timestamp}.csv"

    with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["序号", "文件名", "票据类型", "日期", "金额", "员工",
                         "项目", "是否重复", "缺附件", "风险等级", "风险原因"])
        for i, receipt in enumerate(receipts, 1):
            writer.writerow([
                i, receipt.filename, receipt.receipt_type,
                receipt.date or "", format_amount(receipt.amount),
                receipt.employee or "", receipt.project or "",
                "是" if receipt.is_duplicate else "否",
                "是" if receipt.is_missing_attachment else "否",
                receipt.risk_level, receipt.risk_reason,
            ])

    append_log(task_dir, "export", f"导出CSV: {filepath.name}")
    return filepath


def generate_report(task_dir: Path) -> Path:
    state = load_task_state(task_dir)
    config = load_task_config(task_dir)
    export_dir = task_dir / EXPORT_DIR
    export_dir.mkdir(parents=True, exist_ok=True)

    receipts = [Receipt.from_dict(r) for r in state.receipts]
    total_amount = sum(r.amount or 0 for r in receipts)
    total_count = len(receipts)
    duplicate_count = sum(1 for r in receipts if r.is_duplicate)
    missing_count = sum(1 for r in receipts if r.is_missing_attachment)
    high_risk_count = sum(1 for r in receipts if r.risk_level == "高")
    medium_risk_count = sum(1 for r in receipts if r.risk_level == "中")

    type_summary = {}
    for r in receipts:
        type_summary[r.receipt_type] = type_summary.get(r.receipt_type, 0) + 1

    project_summary = {}
    for project, r_list in state.groups.items():
        project_receipts = [Receipt.from_dict(r) for r in r_list]
        project_summary[project] = {
            "count": len(project_receipts),
            "total": sum(r.amount or 0 for r in project_receipts),
        }

    high_risk_items = [r for r in receipts if r.risk_level == "高"]

    lines = [
        "=" * 60,
        "         报销材料汇总报告",
        "=" * 60,
        "",
        f"任务名称: {state.task_name}",
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"任务状态: {state.status}",
        "",
        "-" * 40,
        "  总体统计",
        "-" * 40,
        f"  票据总数:     {total_count}",
        f"  总金额:       {format_amount(total_amount)}",
        f"  重复票据:     {duplicate_count}",
        f"  缺少附件:     {missing_count}",
        f"  高风险项:     {high_risk_count}",
        f"  中风险项:     {medium_risk_count}",
        "",
        "-" * 40,
        "  票据类型分布",
        "-" * 40,
    ]
    for rtype, count in sorted(type_summary.items(), key=lambda x: -x[1]):
        lines.append(f"  {rtype}: {count}张")

    if project_summary:
        lines.extend(["", "-" * 40, "  项目归类统计", "-" * 40])
        for project, info in project_summary.items():
            lines.append(f"  {project}: {info['count']}张, 金额 {format_amount(info['total'])}")

    if high_risk_items:
        lines.extend(["", "-" * 40, "  ⚠ 高风险项明细", "-" * 40])
        for r in high_risk_items:
            lines.append(f"  [{r.filename}] {r.risk_reason}")

    lines.extend(["", "=" * 60])

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = export_dir / f"汇总报告_{timestamp}.txt"
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    append_log(task_dir, "export", f"生成汇总报告: {filepath.name}")
    return filepath
