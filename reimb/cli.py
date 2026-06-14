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
from .commands.init_clean_cmd import (
    init_task, clean_task,
    CLEAN_MODE_TEMP, CLEAN_MODE_EXPORTS, CLEAN_MODE_OCR, CLEAN_MODE_RESET,
)
from .commands.scan_cmd import scan_source_dir
from .commands.extract_cmd import extract_receipts
from .commands.check_cmd import check_receipts
from .commands.group_cmd import group_receipts
from .commands.export_cmd import export_to_excel, export_to_csv, generate_report, export_by_month
from .commands.archive_cmd import monthly_archive, preview_monthly_archive
from .commands.review_cmd import (
    modify_field, view_progress, get_logs, get_modification_history,
    list_receipts_by_status, ALL_EDITABLE_FIELDS, FIELD_LABELS,
    get_batch_records, list_batches,
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
@click.version_option(version="1.2.0", prog_name="reimb")
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
    """扫描源目录，读取图片与 PDF 文件，递归子目录，不做重复过滤"""
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

    summary = Table(show_header=False, box=box.SIMPLE, width=70)
    summary.add_column("项", style="cyan")
    summary.add_column("值", style="bold")
    summary.add_row("发现文件总数", str(result["total_found"]))
    summary.add_row("新增票据", f"[green]{result['added']}[/green]")
    summary.add_row("说明", "[dim]扫描阶段未过滤重复，重复票据将在check阶段标记[/dim]")
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
@click.option("--month", "-m", default=None, help="按月份筛选归类 (格式: YYYY-MM)，直接出结果")
@click.option("--regroup", "-r", is_flag=True, help="重新归类所有票据")
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def group(task_name: str, month: Optional[str], regroup: bool, base_dir: Optional[str]):
    """按项目归类票据，支持按月份筛选直接出结果"""
    task_dir = _resolve_task_dir(task_name, base_dir)

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("正在归类票据...", total=None)
        result = group_receipts(task_dir, month=month)
        progress.update(task, completed=True)

    title = "归类结果"
    if result["month_filter"]:
        title += f" (月份: [bold]{result['month_filter']}[/bold])"

    table = Table(title=title, show_lines=True, box=box.SQUARE)
    table.add_column("项目", style="cyan", width=20)
    table.add_column("票据数", style="bold", width=10)
    table.add_column("总金额", style="green", width=16)
    table.add_column("高风险", style="red", width=10)
    table.add_column("中风险", style="yellow", width=10)

    for project, prs in result["groups"].items():
        style = "red" if project == "未分类" else "green"
        count = len(prs)
        total = sum(r.amount or 0 for r in prs)
        hc = sum(1 for r in prs if r.risk_level == "高")
        mc = sum(1 for r in prs if r.risk_level == "中")
        table.add_row(
            f"[{style}]{project}[/{style}]", str(count), format_amount(total),
            f"[red]{hc}[/red]" if hc else str(hc),
            f"[yellow]{mc}[/yellow]" if mc else str(mc),
        )

    console.print(table)

    if result["month_filter"]:
        console.print(
            f"\n[dim]原始总数: {result['total_original']}, "
            f"筛选后: {result['total_filtered']}[/dim]"
        )

    console.print(f"\n[green]✓ 归类完成，共 {len(result['summary'])} 个分组[/green]")


@cli.command()
@click.argument("task_name")
@click.option("--format", "-f", "fmt", type=click.Choice(["excel", "csv"]), default="excel", help="导出格式")
@click.option("--report", "-r", is_flag=True, help="同时生成汇总报告")
@click.option("--month", "-m", default=None, help="按月份导出 (格式: YYYY-MM)")
@click.option("--by-month", is_flag=True, help="按月份批量导出")
@click.option("--use-stored-month", is_flag=True, help="使用任务中保存的月份筛选")
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def export(task_name: str, fmt: str, report: bool, month: Optional[str],
           by_month: bool, use_stored_month: bool, base_dir: Optional[str]):
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
            bid = result.get("batch_id", "")
            console.print(f"[green]✓ 按月导出完成，共 {result['exported']} 个月 (批次 {bid}):[/green]")
            for f in result["files"]:
                m = f.get("month", "") or "-"
                fp = Path(f.get("filepath", ""))
                console.print(
                    f"  • {m}\n"
                    f"    [cyan]{fmt.upper()}[/cyan] {fp.name}\n"
                    f"    [dim]{f.get('filepath')}[/dim]\n"
                    f"    ({f.get('record_count', 0)}条, {format_amount(f.get('total_amount', 0))})"
                )
        return

    active_month = month
    if active_month is None and use_stored_month:
        config = load_task_config(task_dir)
        active_month = config.month_filter

    from .commands.export_cmd import create_batch_id
    batch_id = create_batch_id(task_dir)

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("正在导出数据...", total=None)
        if fmt == "csv":
            filepath = export_to_csv(task_dir, month_filter=active_month, batch_id=batch_id)
        else:
            filepath = export_to_excel(task_dir, month_filter=active_month, batch_id=batch_id)
        progress.update(task, completed=True)

    month_label = f" ({active_month})" if active_month else ""
    fp = Path(filepath)
    console.print(Panel(
        f"[green]✓ 报销清单已导出{month_label}[/green]\n\n"
        f"  批次号:   [bold]{batch_id}[/bold]\n"
        f"  文件名称: [bold]{fp.name}[/bold]\n"
        f"  完整路径: [dim]{filepath.resolve()}[/dim]",
        title="导出完成",
        border_style="green",
    ))

    if report:
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
            task = progress.add_task("正在生成汇总报告...", total=None)
            report_path = generate_report(task_dir, month_filter=active_month, batch_id=batch_id)
            progress.update(task, completed=True)
        rp = Path(report_path)
        console.print(Panel(
            f"[green]✓ 汇总报告已生成[/green]\n\n"
            f"  批次号:   [bold]{batch_id}[/bold]\n"
            f"  文件名称: [bold]{rp.name}[/bold]\n"
            f"  完整路径: [dim]{report_path.resolve()}[/dim]",
            title="报告完成",
            border_style="blue",
        ))


@cli.command(name="monthly-archive")
@click.argument("task_name")
@click.option("--month", "-m", default=None, help="指定月份归档 (格式: YYYY-MM)，不指定则按月批量归档")
@click.option("--format", "-f", "fmt", type=click.Choice(["excel", "csv"]), default="excel", help="清单格式")
@click.option("--no-zip", is_flag=True, help="不生成打包zip")
@click.option("--preview", "-p", is_flag=True, help="预览模式：只统计不生成文件")
@click.option("--yes", "-y", is_flag=True, help="跳过确认直接生成")
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def monthly_archive_cmd(task_name: str, month: Optional[str], fmt: str,
                        no_zip: bool, preview: bool, yes: bool, base_dir: Optional[str]):
    """📦 按月归档：一次生成当月清单、风险说明、汇总报告，可选打包zip"""
    task_dir = _resolve_task_dir(task_name, base_dir)

    if preview:
        result = preview_monthly_archive(task_dir, month=month)
        if result["preview_count"] == 0:
            console.print("[yellow]⚠ 没有可归档的月份数据（票据缺少日期字段）[/yellow]")
            return

        console.print(Panel(
            f"共 {result['preview_count']} 个月份待归档 (任务总计 {result['total_receipts']} 张)",
            title="归档预览",
            border_style="cyan",
        ))

        for pv in result["previews"]:
            m = pv.get("month") or "全部"
            console.print(f"\n[bold]📅 {m}[/bold]")
            stat_table = Table(show_header=False, box=box.SIMPLE, width=70)
            stat_table.add_column("项", style="cyan", width=16)
            stat_table.add_column("值", style="bold")
            stat_table.add_row("票据数", str(pv["receipt_count"]))
            stat_table.add_row("总金额", format_amount(pv["total_amount"]))
            stat_table.add_row("高风险", f"[red]{pv['high_risk']}[/red]" if pv["high_risk"] else "0")
            stat_table.add_row("中风险", f"[yellow]{pv['medium_risk']}[/yellow]" if pv["medium_risk"] else "0")
            stat_table.add_row("重复票据", str(pv["duplicates"]))
            stat_table.add_row("缺附件", str(pv["missing_attachments"]))
            stat_table.add_row("已修改", str(pv["modified"]))
            console.print(stat_table)

            console.print("\n[dim]将生成文件:[/dim]")
            for label, ftype in pv["files_to_generate"]:
                if ftype == "ZIP" and no_zip:
                    continue
                console.print(f"  • {label} ({ftype})")
        console.print(f"\n[cyan]加 --yes 或不带 --preview 执行即可实际生成[/cyan]")
        return

    if not yes:
        preview_data = preview_monthly_archive(task_dir, month=month)
        if preview_data["preview_count"] == 0:
            console.print("[yellow]⚠ 没有可归档的月份数据（票据缺少日期字段）[/yellow]")
            return

        console.print(Panel(
            f"即将归档 {preview_data['preview_count']} 个月份，"
            f"共 {preview_data['total_receipts']} 张票据",
            title="确认归档",
            border_style="yellow",
        ))
        if not click.confirm("确认执行归档？"):
            console.print("[yellow]已取消[/yellow]")
            return

    desc = "正在生成月度归档..."
    if month:
        desc = f"正在生成 {month} 月度归档..."
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task(desc, total=None)
        result = monthly_archive(task_dir, month=month, fmt=fmt, create_zip=not no_zip)
        progress.update(task, completed=True)

    if result["archive_count"] == 0:
        console.print("[yellow]⚠ 没有可归档的月份数据（票据缺少日期字段）[/yellow]")
        return

    bid = result.get("batch_id", "")
    console.print(Panel(
        f"[green]✓ 月度归档完成[/green]\n\n"
        f"  批次号:   [bold]{bid}[/bold]\n"
        f"  归档月份数: [bold]{result['archive_count']}[/bold]\n"
        f"  生成文件数: [bold]{result['total_files']}[/bold]",
        title="月度归档",
        border_style="green",
    ))

    for arc in result["archives"]:
        m = arc.get("month") or "全部"
        console.print(f"\n[bold]📅 {m}[/bold]  ({arc['record_count']}条, {format_amount(arc['total_amount'])})")
        for label, key in [("清单", "list_path"), ("风险说明", "risk_path"), ("汇总报告", "report_path"), ("归档包", "zip_path")]:
            p = arc.get(key)
            if p is None:
                continue
            fp = Path(p)
            console.print(f"  • [cyan]{label}[/cyan] {fp.name}")
            console.print(f"    [dim]{fp.resolve()}[/dim]")


@cli.command()
@click.argument("task_name")
@click.option("--receipt-id", "-r", default=None, help="要修改的票据ID")
@click.option("--field", "-f", default=None, help="要修改的字段名")
@click.option("--value", "-v", default=None, help="新值")
@click.option("--logs", "-l", is_flag=True, help="查看处理日志")
@click.option("--history", "-H", is_flag=True, help="查看修改历史")
@click.option("--progress", "-p", "show_progress", is_flag=True, help="查看任务进度和台账")
@click.option("--all-exports", "-a", is_flag=True, help="进度中显示全部导出记录")
@click.option("--status", "-s", "status_filter", default=None, help="按识别状态筛选查看 (成功/部分/失败/已修正)")
@click.option("--batches", is_flag=True, help="列出所有导出批次")
@click.option("--batch", "batch_id", default=None, help="按批次号回看该批次的所有文件")
@click.option("--limit", "-n", default=50, help="日志条数限制")
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def review(task_name: str, receipt_id: Optional[str], field: Optional[str],
           value: Optional[str], logs: bool, history: bool,
           show_progress: bool, all_exports: bool,
           status_filter: Optional[str], batches: bool,
           batch_id: Optional[str], limit: int, base_dir: Optional[str]):
    """人工修正字段、查看日志、进度台账、修改历史、批次管理"""
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
                    f"  时间:     {mod['timestamp']}\n"
                    f"  操作人:   {mod.get('operator', '财务人员')}\n\n"
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
        table = Table(title=f"修改历史记录 (共{len(mods)}条)", show_lines=True, box=box.SQUARE)
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
        table.add_column("来源路径", style="dim")
        table.add_column("票据类型", style="magenta")
        table.add_column("日期", style="yellow")
        table.add_column("金额", style="green")
        table.add_column("员工", style="blue")
        table.add_column("项目", style="cyan")
        for r in receipts:
            table.add_row(r.id, r.filename, r.source_path,
                         r.receipt_type,
                         r.date or "[dim]-[/dim]",
                         format_amount(r.amount) if r.amount else "[dim]-[/dim]",
                         r.employee or "[dim]-[/dim]",
                         r.project or "[dim]-[/dim]")
        console.print(table)
        return

    if batches:
        batch_list = list_batches(task_dir)
        if not batch_list:
            console.print("[yellow]暂无导出批次记录[/yellow]")
            return
        op_labels = {
            "export": "普通导出",
            "export_month": "按月导出",
            "archive_month": "月度归档",
            "report": "汇总报告",
        }
        table = Table(title=f"批次列表 (共{len(batch_list)}个)", show_lines=True, box=box.SQUARE)
        table.add_column("批次号", style="cyan bold", width=18)
        table.add_column("操作类型", style="yellow", width=14)
        table.add_column("文件数", style="bold", width=8)
        table.add_column("有效文件", style="green", width=10)
        table.add_column("票据数", style="dim", width=8)
        table.add_column("总金额", style="green", width=14)
        table.add_column("月份", style="blue", width=10)
        table.add_column("生成时间", style="dim", width=20)
        for b in batch_list:
            op = op_labels.get(b.get("operation", ""), b.get("operation", ""))
            valid = b.get("valid_files", 0)
            total = b.get("file_count", 0)
            valid_str = f"[green]{valid}[/green]/[dim]{total}[/dim]" if valid == total else f"[yellow]{valid}[/yellow]/[dim]{total}[/dim]"
            table.add_row(
                b.get("batch_id", ""), op, str(total), valid_str,
                str(b.get("record_count", 0)),
                format_amount(b.get("total_amount", 0)),
                b.get("month_filter") or "-",
                b.get("first_timestamp", "")[:19],
            )
        console.print(table)
        console.print("\n[dim]使用 reimb review <任务> --batch <批次号> 查看批次详情[/dim]")
        return

    if batch_id:
        batch_data = get_batch_records(task_dir, batch_id)
        if not batch_data:
            console.print(f"[red]错误: 找不到批次 {batch_id}[/red]")
            return
        op_labels = {
            "export": "普通导出",
            "export_month": "按月导出",
            "archive_month": "月度归档",
            "report": "汇总报告",
        }
        op = op_labels.get(batch_data.get("operation", ""), batch_data.get("operation", ""))
        m = batch_data.get("month_filter") or "-"

        console.print(Panel(
            f"批次号: [bold]{batch_data['batch_id']}[/bold]\n"
            f"操作: [yellow]{op}[/yellow]\n"
            f"月份: [blue]{m}[/blue]\n"
            f"文件数: {batch_data['valid_files']}/{batch_data['record_count']} 有效\n"
            f"票据数: {batch_data['record_count_receipt']}\n"
            f"总金额: [green]{format_amount(batch_data['total_amount'])}[/green]\n"
            f"生成时间: [dim]{batch_data['first_timestamp'][:19]}[/dim]",
            title=f"批次详情 - {batch_data['batch_id']}",
            border_style="cyan",
        ))

        etable = Table(title="批次文件清单", show_lines=True, box=box.SQUARE)
        etable.add_column("状态", style="dim", width=8)
        etable.add_column("类型", style="green", width=12)
        etable.add_column("格式", style="magenta", width=8)
        etable.add_column("文件名", style="white")
        etable.add_column("完整路径", style="dim")
        for rec in batch_data["records"]:
            status = "[green]存在[/green]" if rec.get("file_exists") else "[red]已删除[/red]"
            fp = Path(rec.get("filepath", ""))
            etable.add_row(
                status,
                rec.get("export_type", ""),
                rec.get("format", "").upper(),
                fp.name,
                str(fp.resolve()) if fp.exists() else rec.get("filepath", ""),
            )
        console.print(etable)
        return

    progress_data = view_progress(task_dir, include_all_exports=all_exports)

    console.print(Panel(
        f"任务: [bold]{progress_data['task_name']}[/bold]\n"
        f"状态: [green]{progress_data['status']}[/green]\n"
        f"规则: [dim]v{progress_data['rule_version']}[/dim]\n"
        f"总金额: [green]{format_amount(progress_data['total_amount'])}[/green]",
        title="任务进度台账",
        border_style="blue",
    ))

    if progress_data.get("month_distribution"):
        mtable = Table(title="月份分布", show_lines=True, box=box.SQUARE, width=50)
        mtable.add_column("月份", style="cyan", width=12)
        mtable.add_column("票据数", style="bold", width=10)
        for m, cnt in progress_data["month_distribution"]:
            mtable.add_row(m, str(cnt))
        console.print(mtable)

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
    table.add_row("重复票据", f"[red]{progress_data['duplicates']}[/red]" if progress_data["duplicates"] else "0")
    table.add_row("缺少附件", f"[red]{progress_data['missing_attachments']}[/red]" if progress_data["missing_attachments"] else "0")
    table.add_row("高风险项", f"[red]{progress_data['high_risk']}[/red]" if progress_data["high_risk"] else "0")
    table.add_row("中风险项", f"[yellow]{progress_data['medium_risk']}[/yellow]" if progress_data["medium_risk"] else "0")
    table.add_row("人工修改", f"[blue]{progress_data['modified']}[/blue]" if progress_data["modified"] else "0")
    table.add_row("日志条数", str(progress_data["log_count"]))

    console.print(table)

    console.print("\n[bold]处理流水线:[/bold]")
    for step, done in progress_data["pipeline"]:
        icon = "[green]✓[/green]" if done else "[dim]○[/dim]"
        console.print(f"  {icon} {step}")

    if progress_data.get("export_records"):
        cnt = progress_data["total_exports"]
        shown = len(progress_data["export_records"])
        valid = progress_data.get("valid_exports", 0)
        missing = progress_data.get("missing_exports", 0)
        title_parts = [f"导出台账 (显示{shown}/{cnt}个"]
        if missing > 0:
            title_parts.append(f"，{valid}有效/{missing}已失效")
        title_parts.append("，加 -a 看全部)")
        title = "".join(title_parts)
        etable = Table(title=title, show_lines=True, box=box.SQUARE)
        etable.add_column("状态", style="dim", width=8)
        etable.add_column("批次号", style="cyan", width=18)
        etable.add_column("操作类型", style="yellow", width=10)
        etable.add_column("类型", style="green", width=12)
        etable.add_column("格式", style="magenta", width=8)
        etable.add_column("月份", style="blue", width=10)
        etable.add_column("条数", style="dim", width=6)
        etable.add_column("金额", style="green", width=14)
        etable.add_column("完整路径", style="dim")

        for er in progress_data["export_records"]:
            fp = Path(er.get("filepath", ""))
            op_label = er.get("operation_label", "导出")
            m = er.get("month_filter") or "-"
            exists = er.get("file_exists", True)
            status = "[green]✓[/green]" if exists else "[red]✗[/red]"
            bid = er.get("batch_id", "") or "[dim]-[/dim]"
            etable.add_row(
                status, bid,
                op_label,
                er.get("export_type", ""),
                er.get("format", "").upper(),
                m,
                str(er.get("record_count", 0)),
                format_amount(er.get("total_amount", 0)),
                str(fp.resolve()) if exists else f"[strike]{er.get('filepath', '')}[/strike]",
            )
        console.print(etable)
        if missing > 0:
            console.print(f"[dim]有 {missing} 个文件已被删除，记录仍保留可追溯[/dim]")

    if progress_data["modified"] > 0:
        console.print(f"\n[yellow]⚠ 有 {progress_data['modified']} 张票据被人工修改，请重新运行 check 和 group[/yellow]")


@cli.command()
@click.argument("task_name")
@click.option("--exports-only", "mode", flag_value=CLEAN_MODE_EXPORTS, help="仅清理导出文件和导出记录")
@click.option("--ocr-only", "mode", flag_value=CLEAN_MODE_OCR, help="仅清理OCR识别结果，回到已扫描状态")
@click.option("--reset-full", "mode", flag_value=CLEAN_MODE_RESET, help="完全重置，退回刚初始化状态")
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def clean(task_name: str, mode: Optional[str], base_dir: Optional[str]):
    """清理：选 --exports-only / --ocr-only / --reset-full，默认清临时文件"""
    task_dir = _resolve_task_dir(task_name, base_dir)

    final_mode = mode if mode else CLEAN_MODE_TEMP

    if final_mode == CLEAN_MODE_RESET:
        if not click.confirm("⚠ 确定要完全重置为初始化状态吗？这将删除所有票据、导出、日志、识别结果！"):
            console.print("[yellow]已取消[/yellow]")
            return

    result = clean_task(task_dir, mode=final_mode)
    console.print(Panel(
        f"[green]✓ 清理完成[/green]\n\n"
        f"  清理模式: [bold]{result['mode_description']}[/bold]\n"
        f"  删除文件数: {result['removed_count']}",
        title="清理",
        border_style="green",
    ))

    if result["removed_files"]:
        console.print("\n[dim]已删除文件:[/dim]")
        for f in result["removed_files"][:15]:
            console.print(f"  • {f}")
        if len(result["removed_files"]) > 15:
            console.print(f"  ... 及其他 {len(result['removed_files']) - 15} 个文件")


@cli.command()
@click.argument("task_name")
@click.option("--month", "-m", default=None, help="按月份生成报告 (格式: YYYY-MM)")
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def report(task_name: str, month: Optional[str], base_dir: Optional[str]):
    """生成汇总报告，支持按月份"""
    task_dir = _resolve_task_dir(task_name, base_dir)

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("正在生成报告...", total=None)
        report_path = generate_report(task_dir, month_filter=month)
        progress.update(task, completed=True)

    rp = Path(report_path)
    month_label = f" ({month})" if month else ""
    console.print(Panel(
        f"[green]✓ 汇总报告已生成{month_label}[/green]\n\n"
        f"  文件名称: [bold]{rp.name}[/bold]\n"
        f"  完整路径: [dim]{report_path.resolve()}[/dim]",
        title="汇总报告",
        border_style="blue",
    ))


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
