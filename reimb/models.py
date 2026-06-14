from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from enum import Enum
from typing import Optional, Any
import uuid


class ReceiptType(Enum):
    INVOICE = "发票"
    TRAIN_TICKET = "火车票"
    FLIGHT_TICKET = "机票"
    HOTEL = "住宿费"
    TAXI = "出租车票"
    MEAL = "餐饮票"
    OTHER = "其他"


class RiskLevel(Enum):
    LOW = "低"
    MEDIUM = "中"
    HIGH = "高"


class TaskStatus(Enum):
    INIT = "已初始化"
    SCANNED = "已扫描"
    EXTRACTED = "已提取"
    CHECKED = "已检查"
    GROUPED = "已归类"
    EXPORTED = "已导出"
    REVIEWED = "已审核"


class ExtractionStatus(Enum):
    PENDING = "待处理"
    PROCESSING = "处理中"
    SUCCESS = "识别成功"
    PARTIAL = "部分识别"
    FAILED = "识别失败"
    MODIFIED = "已人工修正"


@dataclass
class FieldModification:
    field: str
    old_value: Any
    new_value: Any
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    operator: str = "财务人员"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> FieldModification:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ExportRecord:
    export_type: str
    format: str
    filepath: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    record_count: int = 0
    total_amount: float = 0.0
    month_filter: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ExportRecord:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ProjectKeyword:
    project: str
    keywords: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ProjectKeyword:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class AttachmentRule:
    receipt_type: str
    required_attachments: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> AttachmentRule:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Receipt:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    filename: str = ""
    stored_filename: str = ""
    source_path: str = ""
    source_subdir: str = ""
    file_hash: str = ""
    file_type: str = ""
    receipt_type: str = ReceiptType.OTHER.value
    ocr_text: str = ""
    extraction_status: str = ExtractionStatus.PENDING.value
    extraction_error: str = ""
    date: Optional[str] = None
    amount: Optional[float] = None
    employee: Optional[str] = None
    project: Optional[str] = None
    description: str = ""
    is_duplicate: bool = False
    duplicate_of: Optional[str] = None
    is_missing_attachment: bool = False
    missing_attachments: list = field(default_factory=list)
    risk_level: str = RiskLevel.LOW.value
    risk_reason: str = ""
    is_modified: bool = False
    modified_fields: list = field(default_factory=list)
    field_modifications: list = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    extracted_at: Optional[str] = None
    reviewed_at: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Receipt:
        known = {k for k in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in known}
        obj = cls(**filtered)
        mods = data.get("field_modifications", [])
        obj.field_modifications = [
            FieldModification.from_dict(m) if isinstance(m, dict) else m
            for m in mods
        ]
        return obj

    def modify_field(self, field: str, new_value: Any) -> FieldModification:
        old_value = getattr(self, field, None)
        mod = FieldModification(field=field, old_value=old_value, new_value=new_value)
        self.field_modifications.append(mod)
        setattr(self, field, new_value)
        self.is_modified = True
        if field not in self.modified_fields:
            self.modified_fields.append(field)
        self.extraction_status = ExtractionStatus.MODIFIED.value
        self.reviewed_at = datetime.now().isoformat()
        return mod


@dataclass
class TaskConfig:
    task_name: str = ""
    source_dir: str = ""
    employee_list: list = field(default_factory=list)
    project_list: list = field(default_factory=list)
    project_keywords: list = field(default_factory=list)
    attachment_rules: list = field(default_factory=list)
    month_filter: Optional[str] = None
    duplicate_threshold: float = 0.95
    amount_warning_threshold: float = 5000.0
    rule_version: int = 1

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> TaskConfig:
        known = {k for k in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in known}
        obj = cls(**filtered)
        pk = data.get("project_keywords", [])
        obj.project_keywords = [
            ProjectKeyword.from_dict(p) if isinstance(p, dict) else p
            for p in pk
        ]
        ar = data.get("attachment_rules", [])
        obj.attachment_rules = [
            AttachmentRule.from_dict(r) if isinstance(r, dict) else r
            for r in ar
        ]
        return obj

    def get_attachment_rules_dict(self) -> dict[str, list]:
        result = {}
        for rule in self.attachment_rules:
            if isinstance(rule, AttachmentRule):
                result[rule.receipt_type] = rule.required_attachments
            elif isinstance(rule, dict):
                result[rule.get("receipt_type", "")] = rule.get("required_attachments", [])
        return result

    def get_project_keywords_dict(self) -> dict[str, list]:
        result = {}
        for pk in self.project_keywords:
            if isinstance(pk, ProjectKeyword):
                result[pk.project] = pk.keywords
            elif isinstance(pk, dict):
                result[pk.get("project", "")] = pk.get("keywords", [])
        for project in self.project_list:
            if project not in result:
                result[project] = [project]
            else:
                if project not in result[project]:
                    result[project].append(project)
        return result


@dataclass
class TaskState:
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    task_name: str = ""
    status: str = TaskStatus.INIT.value
    config: dict = field(default_factory=dict)
    receipts: list = field(default_factory=list)
    groups: dict = field(default_factory=dict)
    logs: list = field(default_factory=list)
    export_records: list = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> TaskState:
        known = {k for k in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in known}
        obj = cls(**filtered)
        er = data.get("export_records", [])
        obj.export_records = [
            ExportRecord.from_dict(e) if isinstance(e, dict) else e
            for e in er
        ]
        return obj


@dataclass
class ProcessLog:
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    action: str = ""
    detail: str = ""
    receipt_id: Optional[str] = None
    old_value: Any = None
    new_value: Any = None
    field: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ProcessLog:
        known = {k for k in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)
