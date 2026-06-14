from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.text import Text
from rich import box

from .models import TaskStatus, Receipt, RiskLevel, ExtractionStatus, ReceiptType
from .config import get_task_dir, load_task_state, load_task_config, DEFAULT_TASK_DIR, EXPORT_DIR
from .commands.init_clean_cmd import init_task, clean_task
from .commands.scan_cmd import scan_source_dir
from .commands.extract_cmd import extract_receipts
from .commands.check_cmd import check_receipts
from .commands.group_cmd import group_receipts
from .commands.export_cmd import export_to_excel, export_to_csv, generate_report, export_by_month
from .commands.review_cmd import (
    modify_field, view_progress, get_logs, get_modification_history,
    list_receipts_by_status, ALL_EDITABLE_FIELDS, FIELD_LABELS,
)
from .commands.rule_cmd import (
    get_current_rules, set_amount_threshold, set_duplicate_threshold,
    add_project, remove_project, set_project_keywords, set_attachment_rule,
    add_employee, remove_employee, reset_rules,
)
from .utils import format_amount

console = Console()


def _resolve_task_dir(task_name: str, base_dir: Optional[str]) -> Path:
    task_dir = get_task_dir(task_name, base_dir)
    if not (task_dir / "task_state.json").exists():
        console.print(f"[red]错误: 任务 '{task_name}' 不存在。请先运行 reimb init[/red]")
        sys.exit(1)
    return task_dir


def _status_color(status: str) -> str:
    color_map = {
        ExtractionStatus.SUCCESS.value: "green",
        ExtractionStatus.PARTIAL.value: "yellow",
        ExtractionStatus.FAILED.value: "red",
        ExtractionStatus.MODIFIED.value: "blue",
        ExtractionStatus.PENDING.value: "dim",
        ExtractionStatus.PROCESSING.value: "cyan",
    }
    return color_map.get(status, "white")


@click.group()
@click.version_option(version="1.1.0", prog_name="reimb")
def cli():
    """🏦 AI 报销材料批量整理工具 - 帮助财务人员高效处理报销票据"""
    pass


@cli.command()
@click.argument("task_name")
@click.option("--source", "-s", required=True, help="源文件目录路径")
@click.option("--employees", "-e", default="", help="员工姓名列表，逗号分隔")
@click.option("--projects", "-p", default="", help="项目名称列表，逗号分隔")
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def init(task_name: str, source: str, employees: str, projects: str, base_dir: Optional[str]):
    """初始化报销任务，创建任务目录结构"""
    source_path = Path(source).resolve()
    if not source_path.exists():
        console.print(f"[red]错误: 源目录不存在: {source}[/red]")
        sys.exit(1)

    emp_list = [e.strip() for e in employees.split(",") if e.strip()] if employees else []
    proj_list = [p.strip() for p in projects.split(",") if p.strip()] if projects else []

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("正在初始化任务...", total=None)
        task_dir = init_task(task_name, str(source_path), emp_list, proj_list, base_dir)
        progress.update(task, completed=True)

    console.print(Panel(
        f"[green]✓ 任务初始化成功[/green]\n\n"
        f"  任务名称: [bold]{task_name}[/bold]\n"
        f"  源目录:   {source_path}\n"
        f"  任务目录: {task_dir}\n"
        f"  员工数:   {len(emp_list)}\n"
        f"  项目数:   {len(proj_list)}\n\n"
        f"[dim]默认规则已加载，使用 reimb rules 查看[/dim]",
        title="任务初始化",
        border_style="green",
    ))


@cli.command()
@click.argument("task_name")
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def scan(task_name: str, base_dir: Optional[str]):
    """扫描源目录，读取图片与 PDF 文件，递归子目录"""
    task_dir = _resolve_task_dir(task_name, base_dir)

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("正在扫描文件...", total=None)
        result = scan_source_dir(task_dir)
        progress.update(task, completed=True)

    table = Table(title="扫描结果", show_lines=True, box=box.SQUARE)
    table.add_column("序号", style="cyan", width=6)
    table.add_column("票据ID", style="dim", width=14)
    table.add_column("文件名", style="white")
    table.add_column("来源子目录", style="magenta")
    table.add_column("类型", style="cyan", width=8)
    table.add_column("来源路径", style="dim")

    for i, receipt in enumerate(result["new_receipts"], 1):
        table.add_row(
            str(i), receipt.id, receipt.filename,
            receipt.source_subdir or "[dim]-[/dim]",
            receipt.file_type, receipt.source_path,
        )

    console.print(table)

    summary = Table(show_header=False, box=box.SIMPLE, width=60)
    summary.add_column("项", style="cyan")
    summary.add_column("值", style="bold")
    summary.add_row("发现文件总数", str(result["total_found"]))
    summary.add_row("新增票据", f"[green]{result['added']}[/green]")
    summary.add_row("跳过重复", f"[yellow]{result['skipped']}[/yellow]")
    console.print(Panel(summary, title="扫描统计", border_style="green"))


@cli.command()
@click.argument("task_name")
@click.option("--force", "-f", is_flag=True, help="强制重新提取所有票据（忽略已处理的）")
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def extract(task_name: str, force: bool, base_dir: Optional[str]):
    """识别票据文字，提取日期、金额、员工姓名等信息"""
    task_dir = _resolve_task_dir(task_name, base_dir)

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("正在提取票据信息...", total=None)
        result = extract_receipts(task_dir, force=force)
        progress.update(task, completed=True)

    stats = result["stats"]
    receipts = result["receipts"]

    table = Table(title="提取结果", show_lines=True, box=box.SQUARE)
    table.add_column("序号", style="cyan", width=6)
    table.add_column("票据ID", style="dim", width=14)
    table.add_column("文件名", style="white")
    table.add_column("识别状态", width=10)
    table.add_column("票据类型", style="magenta", width=10)
    table.add_column("日期", style="yellow", width=12)
    table.add_column("金额", style="green", width=12)
    table.add_column("员工", style="blue", width=10)
    table.add_column("项目", style="cyan", width=10)

    for i, r in enumerate(receipts, 1):
        color = _status_color(r.extraction_status)
        status_cell = f"[{color}]{r.extraction_status}[/{color}]"
        table.add_row(
            str(i), r.id, r.filename, status_cell, r.receipt_type,
            r.date or "[dim]未识别[/dim]",
            format_amount(r.amount) if r.amount else "[dim]未识别[/dim]",
            r.employee or "[dim]未识别[/dim]",
            r.project or "[dim]未识别[/dim]",
        )

    console.print(table)

    summary = Table(show_header=False, box=box.SIMPLE, width=60)
    summary.add_column("项", style="cyan")
    summary.add_column("值", style="bold")
    summary.add_row("总计", str(stats["total"]))
    summary.add_row("已处理", str(stats["processed"]))
    summary.add_row("识别成功", f"[green]{stats['success']}[/green]")
    summary.add_row("部分识别", f"[yellow]{stats['partial']}[/yellow]")
    summary.add_row("识别失败", f"[red]{stats['failed']}[/red]")
    summary.add_row("跳过", str(stats["skipped"]))
    console.print(Panel(summary, title="提取统计", border_style="green"))


@cli.command()
@click.argument("task_name")
@click.option("--recheck", "-r", is_flag=True, help="重新运行所有检查（重置已有结果）")
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def check(task_name: str, recheck: bool, base_dir: Optional[str]):
    """检查重复票据、提示缺失附件、标记高风险项"""
    task_dir = _resolve_task_dir(task_name, base_dir)

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("正在检查票据...", total=None)
        summary = check_receipts(task_dir, reset_status=recheck)
        progress.update(task, completed=True)

    table = Table(title="检查结果", show_lines=True, box=box.SQUARE)
    table.add_column("检查项", style="cyan", width=22)
    table.add_column("数量", style="bold", width=10)

    for status, count in summary.get("status_counts", {}).items():
        color = _status_color(status)
        table.add_row(status, f"[{color}]{count}[/{color}]")

    table.add_section()
    table.add_row("票据总数", str(summary["total"]))
    table.add_row("重复票据", f"[red]{summary['duplicates']}[/red]" if summary["duplicates"] else str(summary["duplicates"]))
    table.add_row("缺少附件", f"[red]{summary['missing_attachments']}[/red]" if summary["missing_attachments"] else str(summary["missing_attachments"]))
    table.add_row("高风险项", f"[red]{summary['high_risk']}[/red]" if summary["high_risk"] else str(summary["high_risk"]))
    table.add_row("中风险项", f"[yellow]{summary['medium_risk']}[/yellow]" if summary["medium_risk"] else str(summary["medium_risk"]))
    table.add_row("低风险项", str(summary["low_risk"]))

    console.print(table)
    console.print(f"\n[dim]当前规则版本: v{summary['rule_version']}[/dim]")

    receipts = summary["receipts"]
    issues = []
    for r in receipts:
        if r.is_duplicate:
            issues.append(("重复", f"[red]•[/red] {r.filename} (与 {r.duplicate_of} 重复)"))
        if r.is_missing_attachment:
            issues.append(("缺附件", f"[yellow]•[/yellow] {r.filename} - 缺少: {', '.join(r.missing_attachments)}"))
        if r.risk_level == "高":
            issues.append(("高风险", f"[red]⚠[/red] {r.filename} - {r.risk_reason}"))

    if issues:
        console.print("\n[bold]问题明细:[/bold]")
        for category, msg in issues:
            console.print(f"  [{category}] {msg}")


@cli.command()
@click.argument("task_name")
@click.option("--month", "-m", default=None, help="按月份筛选 (格式: YYYY-MM)")
@click.option("--regroup", "-r", is_flag=True, help="重新归类所有票据")
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def group(task_name: str, month: Optional[str], regroup: bool, base_dir: Optional[str]):
    """按项目归类票据，支持按月份筛选"""
    task_dir = _resolve_task_dir(task_name, base_dir)

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("正在归类票据...", total=None)
        result = group_receipts(task_dir, month=month)
        progress.update(task, completed=True)

    title = "归类结果"
    if result["month_filter"]:
        title += f" (月份: {result['month_filter']})"

    table = Table(title=title, show_lines=True, box=box.SQUARE)
    table.add_column("项目", style="cyan", width=20)
    table.add_column("票据数", style="bold", width=10)
    table.add_column("总金额", style="green", width=14)

    for project, count in result["summary"].items():
        style = "red" if project == "未分类" else "green"
        total = sum(r.amount or 0 for r in result["groups"].get(project, []))
        table.add_row(f"[{style}]{project}[/{style}]", str(count), format_amount(total))

    console.print(table)

    if result["month_filter"]:
        console.print(f"\n[dim]原始总数: {result['total_original']}, 筛选后: {result['total_filtered']}[/dim]")
    console.print(f"\n[green]✓ 归类完成，共 {len(result['summary'])} 个分组[/green]")


@cli.command()
@click.argument("task_name")
@click.option("--format", "-f", "fmt", type=click.Choice(["excel", "csv"]), default="excel", help="导出格式")
@click.option("--report", "-r", is_flag=True, help="同时生成汇总报告")
@click.option("--month", "-m", default=None, help="按月份导出 (格式: YYYY-MM)")
@click.option("--by-month", is_flag=True, help="按月份批量导出")
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def export(task_name: str, fmt: str, report: bool, month: Optional[str], by_month: bool, base_dir: Optional[str]):
    """导出报销清单表格，支持按月份导出，生成汇总报告"""
    task_dir = _resolve_task_dir(task_name, base_dir)

    if by_month:
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
            task = progress.add_task("正在按月批量导出...", total=None)
            result = export_by_month(task_dir, fmt=fmt)
            progress.update(task, completed=True)

        if result["exported"] == 0:
            console.print("[yellow]⚠ 没有可导出的月份数据（票据缺少日期字段）[/yellow]")
        else:
            console.print(f"[green]✓ 按月导出完成，共 {result['exported']} 个月:[/green]")
            for m in result["months"]:
                console.print(f"  • {m}")
            for f in result["files"]:
                console.print(f"    [dim]{f['filepath']}[/dim]")
        return

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("正在导出数据...", total=None)
        if fmt == "csv":
            filepath = export_to_csv(task_dir, month_filter=month)
        else:
            filepath = export_to_excel(task_dir, month_filter=month)
        progress.update(task, completed=True)

    month_label = f" ({month})" if month else ""
    console.print(f"[green]✓ 报销清单已导出{month_label}: {filepath}[/green]")

    if report:
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
            task = progress.add_task("正在生成汇总报告...", total=None)
            report_path = generate_report(task_dir, month_filter=month)
            progress.update(task, completed=True)
        console.print(f"[green]✓ 汇总报告已生成: {report_path}[/green]")


@cli.command()
@click.argument("task_name")
@click.option("--receipt-id", "-r", default=None, help="要修改的票据ID")
@click.option("--field", "-f", default=None, help="要修改的字段名")
@click.option("--value", "-v", default=None, help="新值")
@click.option("--logs", "-l", is_flag=True, help="查看处理日志")
@click.option("--history", "-H", is_flag=True, help="查看修改历史")
@click.option("--progress", "-p", "show_progress", is_flag=True, help="查看任务进度")
@click.option("--status", "-s", "status_filter", default=None, help="按识别状态筛选查看 (成功/部分/失败/已修正)")
@click.option("--limit", "-n", default=50, help="日志条数限制")
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def review(task_name: str, receipt_id: Optional[str], field: Optional[str],
           value: Optional[str], logs: bool, history: bool,
           show_progress: bool, status_filter: Optional[str],
           limit: int, base_dir: Optional[str]):
    """人工修正字段、查看处理日志、查看任务进度、查看修改历史"""
    task_dir = _resolve_task_dir(task_name, base_dir)

    if receipt_id and field and value is not None:
        try:
            result = modify_field(task_dir, receipt_id, field, value)
            if result is None:
                console.print(f"[red]错误: 找不到票据 {receipt_id}[/red]")
            elif result.get("unchanged"):
                console.print(f"[yellow]⚠ 字段值未变化，无需修改[/yellow]")
            else:
                mod = result["modification"]
                console.print(Panel(
                    f"[green]✓ 修改成功[/green]\n\n"
                    f"  票据ID:   {receipt_id}\n"
                    f"  文件名:   {result['receipt'].filename}\n"
                    f"  字段:     {FIELD_LABELS.get(field, field)}\n"
                    f"  旧值:     {mod['old_value']!r}\n"
                    f"  新值:     {mod['new_value']!r}\n"
                    f"  时间:     {mod['timestamp']}\n\n"
                    f"[dim]请重新运行 check -> group -> export 以更新关联数据[/dim]",
                    title="字段修改",
                    border_style="green",
                ))
        except ValueError as e:
            console.print(f"[red]错误: {e}[/red]")
            console.print(f"[dim]可修改字段: {', '.join(ALL_EDITABLE_FIELDS)}[/dim]")
        return

    if logs:
        log_data = get_logs(task_dir, limit=limit)
        table = Table(title=f"处理日志 (最近{len(log_data)}条)", show_lines=True, box=box.SQUARE)
        table.add_column("时间", style="cyan", width=22)
        table.add_column("操作", style="green", width=10)
        table.add_column("详情", style="white")
        table.add_column("票据ID", style="dim", width=14)
        for log in log_data:
            detail = log.get("detail", "")
            if log.get("field"):
                detail += f" [blue][{log['field']}: {log.get('old_value')!r} -> {log.get('new_value')!r}][/blue]"
            table.add_row(
                log.get("timestamp", "")[:19],
                log.get("action", ""),
                detail,
                log.get("receipt_id") or "",
            )
        console.print(table)
        return

    if history:
        mods = get_modification_history(task_dir, receipt_id=receipt_id)
        if not mods:
            console.print("[yellow]暂无修改历史[/yellow]")
            return
        table = Table(title="修改历史记录", show_lines=True, box=box.SQUARE)
        table.add_column("时间", style="cyan", width=22)
        table.add_column("票据ID", style="dim", width=14)
        table.add_column("文件名", style="white")
        table.add_column("字段", style="yellow", width=10)
        table.add_column("旧值", style="red")
        table.add_column("新值", style="green")
        table.add_column("操作人", style="dim", width=10)
        for m in mods:
            table.add_row(
                m.get("timestamp", "")[:19], m.get("receipt_id", ""),
                m.get("filename", ""), m.get("field", ""),
                str(m.get("old_value", "")), str(m.get("new_value", "")),
                m.get("operator", "财务人员"),
            )
        console.print(table)
        return

    if status_filter:
        status_map = {
            "成功": ExtractionStatus.SUCCESS.value,
            "部分": ExtractionStatus.PARTIAL.value,
            "失败": ExtractionStatus.FAILED.value,
            "已修正": ExtractionStatus.MODIFIED.value,
        }
        s = status_map.get(status_filter, status_filter)
        receipts = list_receipts_by_status(task_dir, status_filter=s)
        if not receipts:
            console.print(f"[yellow]没有状态为 '{status_filter}' 的票据[/yellow]")
            return
        table = Table(title=f"状态筛选: {status_filter} ({len(receipts)}张)", show_lines=True, box=box.SQUARE)
        table.add_column("票据ID", style="dim", width=14)
        table.add_column("文件名", style="white")
        table.add_column("票据类型", style="magenta")
        table.add_column("日期", style="yellow")
        table.add_column("金额", style="green")
        table.add_column("员工", style="blue")
        table.add_column("项目", style="cyan")
        for r in receipts:
            table.add_row(r.id, r.filename, r.receipt_type,
                         r.date or "[dim]-[/dim]",
                         format_amount(r.amount) if r.amount else "[dim]-[/dim]",
                         r.employee or "[dim]-[/dim]",
                         r.project or "[dim]-[/dim]")
        console.print(table)
        return

    progress_data = view_progress(task_dir)

    console.print(Panel(
        f"任务: [bold]{progress_data['task_name']}[/bold]\n"
        f"状态: [green]{progress_data['status']}[/green]\n"
        f"规则: [dim]v{load_task_config(task_dir).rule_version}[/dim]",
        title="任务进度",
        border_style="blue",
    ))

    table = Table(title="处理统计", show_lines=True, box=box.SQUARE)
    table.add_column("指标", style="cyan", width=20)
    table.add_column("数值", style="bold", width=10)

    for status, count in progress_data.get("status_counts", {}).items():
        color = _status_color(status)
        table.add_row(status, f"[{color}]{count}[/{color}]")

    table.add_section()
    table.add_row("票据总数", str(progress_data["total_receipts"]))
    table.add_row("OCR完成", str(progress_data["ocr_completed"]))
    table.add_row("日期已提取", str(progress_data["date_extracted"]))
    table.add_row("金额已提取", str(progress_data["amount_extracted"]))
    table.add_row("员工已匹配", str(progress_data["employee_matched"]))
    table.add_row("重复票据", str(progress_data["duplicates"]))
    table.add_row("缺少附件", str(progress_data["missing_attachments"]))
    table.add_row("高风险项", str(progress_data["high_risk"]))
    table.add_row("人工修改", str(progress_data["modified"]))
    table.add_row("日志条数", str(progress_data["log_count"]))

    console.print(table)

    console.print("\n[bold]处理流水线:[/bold]")
    for step, done in progress_data["pipeline"]:
        icon = "[green]✓[/green]" if done else "[dim]○[/dim]"
        console.print(f"  {icon} {step}")

    if progress_data.get("export_records"):
        console.print(f"\n[bold]最近导出文件 ({progress_data['total_exports']} 个):[/bold]")
        for er in progress_data["export_records"]:
            fp = Path(er.get("filepath", ""))
            console.print(
                f"  [dim]{er.get('timestamp', '')[:19]}[/dim] "
                f"[{er.get('format','').upper()}] {fp.name} "
                f"([cyan]{er.get('record_count', 0)}条[/cyan], "
                f"[green]{format_amount(er.get('total_amount', 0))}[/green])"
            )

    if progress_data["modified"] > 0:
        console.print(f"\n[yellow]⚠ 有 {progress_data['modified']} 张票据被人工修改，请重新运行 check 和 group[/yellow]")


@cli.command()
@click.argument("task_name")
@click.option("--keep-exports", "-k", is_flag=True, default=True, help="保留导出文件")
@click.option("--keep-receipts", is_flag=True, default=True, help="保留票据文件")
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def clean(task_name: str, keep_exports: bool, keep_receipts: bool, base_dir: Optional[str]):
    """清理临时文件，支持完全重置"""
    task_dir = _resolve_task_dir(task_name, base_dir)

    result = clean_task(task_dir, keep_exports=keep_exports, keep_receipts=keep_receipts)
    console.print(Panel(
        f"[green]✓ 清理完成[/green]\n\n"
        f"  删除文件数: {result['removed_count']}\n"
        f"  保留导出:   {'是' if keep_exports else '否'}\n"
        f"  保留票据:   {'是' if keep_receipts else '否'}",
        title="清理",
        border_style="green",
    ))

    if result["removed_files"]:
        console.print("\n[dim]已删除:[/dim]")
        for f in result["removed_files"][:10]:
            console.print(f"  • {f}")
        if len(result["removed_files"]) > 10:
            console.print(f"  ... 及其他 {len(result['removed_files']) - 10} 个文件")


@cli.command()
@click.argument("task_name")
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def report(task_name: str, base_dir: Optional[str]):
    """生成汇总报告"""
    task_dir = _resolve_task_dir(task_name, base_dir)

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("正在生成报告...", total=None)
        report_path = generate_report(task_dir)
        progress.update(task, completed=True)

    console.print(f"[green]✓ 汇总报告已生成: {report_path}[/green]")

    state = load_task_state(task_dir)
    console.print(Panel(state.status, title="当前任务状态", border_style="blue"))


@cli.group()
def rules():
    """⚙️  规则配置管理 - 查看/调整阈值、关键词、附件规则"""
    pass


@rules.command(name="show")
@click.argument("task_name")
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def rules_show(task_name: str, base_dir: Optional[str]):
    """查看当前任务的所有规则配置"""
    task_dir = _resolve_task_dir(task_name, base_dir)
    current = get_current_rules(task_dir)

    console.print(Panel(
        f"[bold]规则版本:[/bold] v{current['rule_version']}\n"
        f"[bold]金额阈值:[/bold] {format_amount(current['amount_threshold'])}\n"
        f"[bold]重复阈值:[/bold] {current['duplicate_threshold']}\n"
        f"[bold]月份筛选:[/bold] {current['month_filter'] or '未设置'}",
        title="基础规则",
        border_style="cyan",
    ))

    table = Table(title="项目关键词配置", show_lines=True, box=box.SQUARE)
    table.add_column("项目", style="cyan", width=20)
    table.add_column("关键词", style="white")
    for project, keywords in current["project_keywords"].items():
        table.add_row(project, ", ".join(keywords))
    console.print(table)

    table2 = Table(title="附件规则配置", show_lines=True, box=box.SQUARE)
    table2.add_column("票据类型", style="cyan", width=20)
    table2.add_column("必备附件", style="white")
    for rtype, attachments in current["attachment_rules"].items():
        table2.add_row(rtype, ", ".join(attachments))
    console.print(table2)

    table3 = Table(title="员工列表", show_lines=True, box=box.SQUARE)
    table3.add_column("序号", style="cyan", width=6)
    table3.add_column("员工姓名", style="white")
    for i, emp in enumerate(current["employee_list"], 1):
        table3.add_row(str(i), emp)
    console.print(table3)


@rules.command(name="set-amount")
@click.argument("task_name")
@click.argument("amount", type=float)
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def rules_set_amount(task_name: str, amount: float, base_dir: Optional[str]):
    """设置高金额预警阈值"""
    task_dir = _resolve_task_dir(task_name, base_dir)
    result = set_amount_threshold(task_dir, amount)
    console.print(
        f"[green]✓ 金额阈值已更新: {format_amount(result['old'])} -> {format_amount(result['new'])} "
        f"(规则v{result['version']})[/green]"
    )
    console.print("[dim]请重新运行 check 以按新规则评估风险[/dim]")


@rules.command(name="set-duplicate")
@click.argument("task_name")
@click.argument("threshold", type=float)
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def rules_set_duplicate(task_name: str, threshold: float, base_dir: Optional[str]):
    """设置重复检测相似度阈值 (0.0-1.0)"""
    task_dir = _resolve_task_dir(task_name, base_dir)
    if not (0.0 <= threshold <= 1.0):
        console.print("[red]错误: 阈值应在 0.0 到 1.0 之间[/red]")
        sys.exit(1)
    result = set_duplicate_threshold(task_dir, threshold)
    console.print(
        f"[green]✓ 重复阈值已更新: {result['old']} -> {result['new']} "
        f"(规则v{result['version']})[/green]"
    )
    console.print("[dim]请重新运行 check 以按新规则检测重复[/dim]")


@rules.command(name="add-project")
@click.argument("task_name")
@click.argument("project")
@click.option("--keywords", "-k", default="", help="关键词列表，逗号分隔")
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def rules_add_project(task_name: str, project: str, keywords: str, base_dir: Optional[str]):
    """添加项目，支持指定匹配关键词"""
    task_dir = _resolve_task_dir(task_name, base_dir)
    kw_list = [k.strip() for k in keywords.split(",") if k.strip()] if keywords else None
    result = add_project(task_dir, project, kw_list)
    if result["added"]:
        console.print(
            f"[green]✓ 项目已添加: {project} "
            f"(关键词: {result.get('keywords') or [project]}) "
            f"(规则v{result['version']})[/green]"
        )
    else:
        console.print(f"[yellow]⚠ {result['reason']}[/yellow]")


@rules.command(name="remove-project")
@click.argument("task_name")
@click.argument("project")
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def rules_remove_project(task_name: str, project: str, base_dir: Optional[str]):
    """移除项目"""
    task_dir = _resolve_task_dir(task_name, base_dir)
    result = remove_project(task_dir, project)
    if result["removed"]:
        console.print(f"[green]✓ 项目已移除: {project} (规则v{result['version']})[/green]")
    else:
        console.print(f"[yellow]⚠ {result['reason']}[/yellow]")


@rules.command(name="set-keywords")
@click.argument("task_name")
@click.argument("project")
@click.argument("keywords")
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def rules_set_keywords(task_name: str, project: str, keywords: str, base_dir: Optional[str]):
    """设置项目匹配关键词 (逗号分隔)"""
    task_dir = _resolve_task_dir(task_name, base_dir)
    kw_list = [k.strip() for k in keywords.split(",") if k.strip()]
    result = set_project_keywords(task_dir, project, kw_list)
    if result["updated"]:
        console.print(
            f"[green]✓ 关键词已更新: {project} -> {kw_list} "
            f"(规则v{result['version']})[/green]"
        )
    else:
        console.print(f"[yellow]⚠ {result['reason']}[/yellow]")


@rules.command(name="set-attachment")
@click.argument("task_name")
@click.argument("receipt_type")
@click.argument("attachments")
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def rules_set_attachment(task_name: str, receipt_type: str, attachments: str, base_dir: Optional[str]):
    """设置票据类型的必备附件 (逗号分隔)"""
    task_dir = _resolve_task_dir(task_name, base_dir)
    att_list = [a.strip() for a in attachments.split(",") if a.strip()]
    result = set_attachment_rule(task_dir, receipt_type, att_list)
    if result["updated"]:
        console.print(
            f"[green]✓ 附件规则已更新: {receipt_type} -> {att_list} "
            f"(规则v{result['version']})[/green]"
        )
        console.print("[dim]请重新运行 check 以按新规则检查附件[/dim]")
    else:
        console.print(f"[yellow]⚠ {result['reason']}[/yellow]")
        types = [e.value for e in ReceiptType]
        console.print(f"[dim]可选类型: {', '.join(types)}[/dim]")


@rules.command(name="add-employee")
@click.argument("task_name")
@click.argument("name")
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def rules_add_employee(task_name: str, name: str, base_dir: Optional[str]):
    """添加员工"""
    task_dir = _resolve_task_dir(task_name, base_dir)
    result = add_employee(task_dir, name)
    if result["added"]:
        console.print(f"[green]✓ 员工已添加: {name} (规则v{result['version']})[/green]")
    else:
        console.print(f"[yellow]⚠ {result['reason']}[/yellow]")


@rules.command(name="remove-employee")
@click.argument("task_name")
@click.argument("name")
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def rules_remove_employee(task_name: str, name: str, base_dir: Optional[str]):
    """移除员工"""
    task_dir = _resolve_task_dir(task_name, base_dir)
    result = remove_employee(task_dir, name)
    if result["removed"]:
        console.print(f"[green]✓ 员工已移除: {name} (规则v{result['version']})[/green]")
    else:
        console.print(f"[yellow]⚠ {result['reason']}[/yellow]")


@rules.command(name="reset")
@click.argument("task_name")
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def rules_reset(task_name: str, base_dir: Optional[str]):
    """重置所有规则为默认值"""
    task_dir = _resolve_task_dir(task_name, base_dir)
    if click.confirm("确定要重置所有规则为默认值吗？此操作不可撤销。"):
        result = reset_rules(task_dir)
        console.print(
            f"[green]✓ 规则已重置: v{result['old_version']} -> v{result['new_version']}[/green]"
        )


def main():
    cli()


if __name__ == "__main__":
    main()
