from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from enum import Enum
from typing import Optional
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


@dataclass
class Receipt:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    filename: str = ""
    file_hash: str = ""
    file_type: str = ""
    receipt_type: str = ReceiptType.OTHER.value
    ocr_text: str = ""
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
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Receipt:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class TaskConfig:
    task_name: str = ""
    source_dir: str = ""
    employee_list: list = field(default_factory=list)
    project_list: list = field(default_factory=list)
    month_filter: Optional[str] = None
    duplicate_threshold: float = 0.95
    amount_warning_threshold: float = 5000.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> TaskConfig:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class TaskState:
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    task_name: str = ""
    status: str = TaskStatus.INIT.value
    config: dict = field(default_factory=dict)
    receipts: list = field(default_factory=list)
    groups: dict = field(default_factory=dict)
    logs: list = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> TaskState:
        known = {k for k in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


@dataclass
class ProcessLog:
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    action: str = ""
    detail: str = ""
    receipt_id: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)
