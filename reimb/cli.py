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

from .models import TaskStatus, Receipt, RiskLevel
from .config import get_task_dir, load_task_state, load_task_config, DEFAULT_TASK_DIR
from .commands.init_clean_cmd import init_task, clean_task
from .commands.scan_cmd import scan_source_dir
from .commands.extract_cmd import extract_receipts
from .commands.check_cmd import check_receipts
from .commands.group_cmd import group_receipts
from .commands.export_cmd import export_to_excel, generate_report
from .commands.review_cmd import modify_field, view_progress, get_logs
from .utils import format_amount

console = Console()


def _resolve_task_dir(task_name: str, base_dir: Optional[str]) -> Path:
    task_dir = get_task_dir(task_name, base_dir)
    if not (task_dir / "task_state.json").exists():
        console.print(f"[red]错误: 任务 '{task_name}' 不存在。请先运行 reimb init[/red]")
        sys.exit(1)
    return task_dir


@click.group()
@click.version_option(version="1.0.0", prog_name="reimb")
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
        f"  项目数:   {len(proj_list)}",
        title="任务初始化",
        border_style="green",
    ))


@cli.command()
@click.argument("task_name")
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def scan(task_name: str, base_dir: Optional[str]):
    """扫描源目录，读取图片与 PDF 文件"""
    task_dir = _resolve_task_dir(task_name, base_dir)

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("正在扫描文件...", total=None)
        new_receipts = scan_source_dir(task_dir)
        progress.update(task, completed=True)

    table = Table(title="扫描结果", show_lines=True)
    table.add_column("序号", style="cyan", width=6)
    table.add_column("文件名", style="white")
    table.add_column("类型", style="magenta", width=8)
    table.add_column("哈希值", style="dim", width=16)

    for i, receipt in enumerate(new_receipts, 1):
        table.add_row(str(i), receipt.filename, receipt.file_type, receipt.file_hash[:16])

    console.print(table)
    console.print(f"\n[green]✓ 扫描完成，发现 {len(new_receipts)} 个新文件[/green]")


@cli.command()
@click.argument("task_name")
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def extract(task_name: str, base_dir: Optional[str]):
    """识别票据文字，提取日期、金额、员工姓名等信息"""
    task_dir = _resolve_task_dir(task_name, base_dir)

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("正在提取票据信息...", total=None)
        receipts = extract_receipts(task_dir)
        progress.update(task, completed=True)

    table = Table(title="提取结果", show_lines=True)
    table.add_column("序号", style="cyan", width=6)
    table.add_column("文件名", style="white")
    table.add_column("票据类型", style="magenta", width=10)
    table.add_column("日期", style="yellow", width=12)
    table.add_column("金额", style="green", width=12)
    table.add_column("员工", style="blue", width=10)
    table.add_column("项目", style="cyan", width=10)

    for i, r in enumerate(receipts, 1):
        table.add_row(
            str(i), r.filename, r.receipt_type,
            r.date or "[dim]未识别[/dim]",
            format_amount(r.amount) if r.amount else "[dim]未识别[/dim]",
            r.employee or "[dim]未识别[/dim]",
            r.project or "[dim]未识别[/dim]",
        )

    console.print(table)
    console.print(f"\n[green]✓ 提取完成，共处理 {len(receipts)} 个票据[/green]")


@cli.command()
@click.argument("task_name")
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def check(task_name: str, base_dir: Optional[str]):
    """检查重复票据、提示缺失附件、标记高风险项"""
    task_dir = _resolve_task_dir(task_name, base_dir)

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("正在检查票据...", total=None)
        summary = check_receipts(task_dir)
        progress.update(task, completed=True)

    table = Table(title="检查结果", show_lines=True)
    table.add_column("检查项", style="cyan", width=20)
    table.add_column("数量", style="bold", width=10)

    table.add_row("票据总数", str(summary["total"]))
    table.add_row("重复票据", f"[red]{summary['duplicates']}[/red]" if summary["duplicates"] else f"[green]{summary['duplicates']}[/green]")
    table.add_row("缺少附件", f"[red]{summary['missing_attachments']}[/red]" if summary["missing_attachments"] else f"[green]{summary['missing_attachments']}[/green]")
    table.add_row("高风险项", f"[red]{summary['high_risk']}[/red]" if summary["high_risk"] else f"[green]{summary['high_risk']}[/green]")
    table.add_row("中风险项", f"[yellow]{summary['medium_risk']}[/yellow]" if summary["medium_risk"] else f"[green]{summary['medium_risk']}[/green]")
    table.add_row("低风险项", str(summary["low_risk"]))

    console.print(table)

    state = load_task_state(task_dir)
    receipts = [Receipt.from_dict(r) for r in state.receipts]

    duplicates = [r for r in receipts if r.is_duplicate]
    if duplicates:
        console.print("\n[red]⚠ 重复票据:[/red]")
        for r in duplicates:
            console.print(f"  • {r.filename} (与 {r.duplicate_of} 重复)")

    missing = [r for r in receipts if r.is_missing_attachment]
    if missing:
        console.print("\n[yellow]⚠ 缺少附件:[/yellow]")
        for r in missing:
            console.print(f"  • {r.filename} - 缺少: {', '.join(r.missing_attachments)}")

    high_risk = [r for r in receipts if r.risk_level == "高"]
    if high_risk:
        console.print("\n[red]⚠ 高风险项:[/red]")
        for r in high_risk:
            console.print(f"  • {r.filename} - {r.risk_reason}")


@cli.command()
@click.argument("task_name")
@click.option("--month", "-m", default=None, help="按月份筛选 (格式: YYYY-MM)")
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def group(task_name: str, month: Optional[str], base_dir: Optional[str]):
    """按项目归类票据，支持按月份筛选"""
    task_dir = _resolve_task_dir(task_name, base_dir)

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("正在归类票据...", total=None)
        summary = group_receipts(task_dir, month=month)
        progress.update(task, completed=True)

    table = Table(title="归类结果" + (f" (月份: {month})" if month else ""), show_lines=True)
    table.add_column("项目", style="cyan", width=20)
    table.add_column("票据数", style="bold", width=10)

    for project, count in summary.items():
        style = "red" if project == "未分类" else "green"
        table.add_row(f"[{style}]{project}[/{style}]", str(count))

    console.print(table)
    console.print(f"\n[green]✓ 归类完成，共 {len(summary)} 个分组[/green]")


@cli.command()
@click.argument("task_name")
@click.option("--format", "-f", "fmt", type=click.Choice(["excel", "csv"]), default="excel", help="导出格式")
@click.option("--report", "-r", is_flag=True, help="同时生成汇总报告")
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def export(task_name: str, fmt: str, report: bool, base_dir: Optional[str]):
    """导出报销清单表格，生成汇总报告"""
    task_dir = _resolve_task_dir(task_name, base_dir)

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("正在导出数据...", total=None)
        filepath = export_to_excel(task_dir)
        progress.update(task, completed=True)

    console.print(f"[green]✓ 报销清单已导出: {filepath}[/green]")

    if report:
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
            task = progress.add_task("正在生成汇总报告...", total=None)
            report_path = generate_report(task_dir)
            progress.update(task, completed=True)

        console.print(f"[green]✓ 汇总报告已生成: {report_path}[/green]")


@cli.command()
@click.argument("task_name")
@click.option("--receipt-id", "-r", default=None, help="要修改的票据ID")
@click.option("--field", "-f", default=None, help="要修改的字段名")
@click.option("--value", "-v", default=None, help="新值")
@click.option("--logs", "-l", is_flag=True, help="查看处理日志")
@click.option("--progress", "-p", "show_progress", is_flag=True, help="查看任务进度")
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def review(task_name: str, receipt_id: Optional[str], field: Optional[str],
           value: Optional[str], logs: bool, show_progress: bool, base_dir: Optional[str]):
    """人工修正字段、查看处理日志、查看任务进度"""
    task_dir = _resolve_task_dir(task_name, base_dir)

    if receipt_id and field and value:
        try:
            receipt = modify_field(task_dir, receipt_id, field, value)
            if receipt:
                console.print(f"[green]✓ 已修改票据 {receipt_id} 的 {field} 为 {value}[/green]")
            else:
                console.print(f"[red]错误: 找不到票据 {receipt_id}[/red]")
        except ValueError as e:
            console.print(f"[red]错误: {e}[/red]")
        return

    if logs:
        log_data = get_logs(task_dir, limit=20)
        table = Table(title="处理日志 (最近20条)", show_lines=True)
        table.add_column("时间", style="cyan", width=22)
        table.add_column("操作", style="green", width=10)
        table.add_column("详情", style="white")
        for log in log_data:
            table.add_row(log.get("timestamp", ""), log.get("action", ""), log.get("detail", ""))
        console.print(table)
        return

    if show_progress or (not receipt_id and not logs):
        progress_data = view_progress(task_dir)

        console.print(Panel(
            f"任务: [bold]{progress_data['task_name']}[/bold]\n"
            f"状态: [green]{progress_data['status']}[/green]",
            title="任务进度",
            border_style="blue",
        ))

        table = Table(title="处理统计", show_lines=True)
        table.add_column("指标", style="cyan", width=20)
        table.add_column("数值", style="bold", width=10)

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


@cli.command()
@click.argument("task_name")
@click.option("--keep-exports", "-k", is_flag=True, default=True, help="保留导出文件")
@click.option("--base-dir", "-b", default=None, help="任务存储根目录")
def clean(task_name: str, keep_exports: bool, base_dir: Optional[str]):
    """清理临时文件"""
    task_dir = _resolve_task_dir(task_name, base_dir)

    result = clean_task(task_dir, keep_exports=keep_exports)
    console.print(f"[green]✓ 清理完成，删除 {result['removed_count']} 个临时文件[/green]")

    if result["removed_files"]:
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


def main():
    cli()


if __name__ == "__main__":
    main()
