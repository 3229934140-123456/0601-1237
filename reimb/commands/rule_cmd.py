from __future__ import annotations

from pathlib import Path
from typing import Optional, Any

from ..config import load_task_config, save_task_config, append_log
from ..models import TaskConfig, ProjectKeyword, AttachmentRule, ReceiptType


def get_current_rules(task_dir: Path) -> dict:
    config = load_task_config(task_dir)
    attachment_rules = config.get_attachment_rules_dict()
    project_keywords = config.get_project_keywords_dict()

    return {
        "amount_threshold": config.amount_warning_threshold,
        "duplicate_threshold": config.duplicate_threshold,
        "rule_version": config.rule_version,
        "project_list": config.project_list,
        "employee_list": config.employee_list,
        "project_keywords": project_keywords,
        "attachment_rules": attachment_rules,
        "month_filter": config.month_filter,
    }


def _bump_version(config: TaskConfig) -> None:
    config.rule_version += 1


def set_amount_threshold(task_dir: Path, threshold: float) -> dict:
    config = load_task_config(task_dir)
    old = config.amount_warning_threshold
    config.amount_warning_threshold = threshold
    _bump_version(config)
    save_task_config(task_dir, config)
    append_log(
        task_dir, "rule",
        f"更新金额阈值: {old} -> {threshold} (规则v{config.rule_version})",
        field="amount_warning_threshold",
        old_value=old,
        new_value=threshold,
    )
    return {"old": old, "new": threshold, "version": config.rule_version}


def set_duplicate_threshold(task_dir: Path, threshold: float) -> dict:
    config = load_task_config(task_dir)
    old = config.duplicate_threshold
    config.duplicate_threshold = threshold
    _bump_version(config)
    save_task_config(task_dir, config)
    append_log(
        task_dir, "rule",
        f"更新重复阈值: {old} -> {threshold} (规则v{config.rule_version})",
        field="duplicate_threshold",
        old_value=old,
        new_value=threshold,
    )
    return {"old": old, "new": threshold, "version": config.rule_version}


def add_project(task_dir: Path, project: str, keywords: list[str] = None) -> dict:
    config = load_task_config(task_dir)
    if project in config.project_list:
        return {"added": False, "reason": "项目已存在"}

    config.project_list.append(project)
    if keywords:
        pk = ProjectKeyword(project=project, keywords=list(keywords))
        config.project_keywords.append(pk)
    else:
        pk = ProjectKeyword(project=project, keywords=[project])
        config.project_keywords.append(pk)

    _bump_version(config)
    save_task_config(task_dir, config)
    append_log(
        task_dir, "rule",
        f"添加项目: {project}, 关键词={keywords or [project]} (规则v{config.rule_version})",
    )
    return {"added": True, "project": project, "keywords": keywords, "version": config.rule_version}


def remove_project(task_dir: Path, project: str) -> dict:
    config = load_task_config(task_dir)
    if project not in config.project_list:
        return {"removed": False, "reason": "项目不存在"}

    config.project_list.remove(project)
    config.project_keywords = [
        pk for pk in config.project_keywords
        if (isinstance(pk, ProjectKeyword) and pk.project != project)
        or (isinstance(pk, dict) and pk.get("project") != project)
    ]

    _bump_version(config)
    save_task_config(task_dir, config)
    append_log(
        task_dir, "rule",
        f"移除项目: {project} (规则v{config.rule_version})",
    )
    return {"removed": True, "project": project, "version": config.rule_version}


def set_project_keywords(task_dir: Path, project: str, keywords: list[str]) -> dict:
    config = load_task_config(task_dir)
    if project not in config.project_list:
        return {"updated": False, "reason": "项目不存在"}

    found = False
    for i, pk in enumerate(config.project_keywords):
        pk_project = pk.project if isinstance(pk, ProjectKeyword) else pk.get("project", "")
        if pk_project == project:
            if isinstance(pk, ProjectKeyword):
                pk.keywords = list(keywords)
            else:
                config.project_keywords[i] = ProjectKeyword(project=project, keywords=list(keywords))
            found = True
            break

    if not found:
        config.project_keywords.append(ProjectKeyword(project=project, keywords=list(keywords)))

    _bump_version(config)
    save_task_config(task_dir, config)
    append_log(
        task_dir, "rule",
        f"设置项目关键词: {project} -> {keywords} (规则v{config.rule_version})",
    )
    return {"updated": True, "project": project, "keywords": keywords, "version": config.rule_version}


def set_attachment_rule(task_dir: Path, receipt_type: str, attachments: list[str]) -> dict:
    config = load_task_config(task_dir)
    valid_types = [e.value for e in ReceiptType]
    if receipt_type not in valid_types:
        return {"updated": False, "reason": f"无效票据类型，可选: {', '.join(valid_types)}"}

    found = False
    for i, ar in enumerate(config.attachment_rules):
        ar_type = ar.receipt_type if isinstance(ar, AttachmentRule) else ar.get("receipt_type", "")
        if ar_type == receipt_type:
            if isinstance(ar, AttachmentRule):
                ar.required_attachments = list(attachments)
            else:
                config.attachment_rules[i] = AttachmentRule(
                    receipt_type=receipt_type, required_attachments=list(attachments)
                )
            found = True
            break

    if not found:
        config.attachment_rules.append(AttachmentRule(
            receipt_type=receipt_type, required_attachments=list(attachments)
        ))

    _bump_version(config)
    save_task_config(task_dir, config)
    append_log(
        task_dir, "rule",
        f"设置附件规则: {receipt_type} -> {attachments} (规则v{config.rule_version})",
    )
    return {"updated": True, "receipt_type": receipt_type, "attachments": attachments, "version": config.rule_version}


def add_employee(task_dir: Path, name: str) -> dict:
    config = load_task_config(task_dir)
    if name in config.employee_list:
        return {"added": False, "reason": "员工已存在"}

    config.employee_list.append(name)
    _bump_version(config)
    save_task_config(task_dir, config)
    append_log(
        task_dir, "rule",
        f"添加员工: {name} (规则v{config.rule_version})",
    )
    return {"added": True, "employee": name, "version": config.rule_version}


def remove_employee(task_dir: Path, name: str) -> dict:
    config = load_task_config(task_dir)
    if name not in config.employee_list:
        return {"removed": False, "reason": "员工不存在"}

    config.employee_list.remove(name)
    _bump_version(config)
    save_task_config(task_dir, config)
    append_log(
        task_dir, "rule",
        f"移除员工: {name} (规则v{config.rule_version})",
    )
    return {"removed": True, "employee": name, "version": config.rule_version}


def reset_rules(task_dir: Path) -> dict:
    from .init_clean_cmd import DEFAULT_ATTACHMENT_RULES
    config = load_task_config(task_dir)
    old_version = config.rule_version
    config.duplicate_threshold = 0.95
    config.amount_warning_threshold = 5000.0
    config.month_filter = None
    config.attachment_rules = list(DEFAULT_ATTACHMENT_RULES)
    keywords = [ProjectKeyword(project=p, keywords=[p]) for p in config.project_list]
    config.project_keywords = keywords
    config.rule_version = 1
    save_task_config(task_dir, config)
    append_log(
        task_dir, "rule",
        f"重置规则: v{old_version} -> v1",
    )
    return {"reset": True, "old_version": old_version, "new_version": 1}
