from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from .models import Receipt, ReceiptType


def compute_file_hash(filepath: Path, algorithm: str = "sha256") -> str:
    h = hashlib.new(algorithm)
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_date_from_text(text: str) -> Optional[str]:
    patterns = [
        r"(\d{4})\s*[年/\-.]\s*(\d{1,2})\s*[月/\-.]\s*(\d{1,2})\s*日?",
        r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?",
        r"(\d{2})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{1,2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            parts = [match.group(i) for i in range(1, 4)]
            if len(parts[0]) == 2:
                parts[0] = "20" + parts[0]
            try:
                y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
                if 2000 <= y <= 2099 and 1 <= m <= 12 and 1 <= d <= 31:
                    return f"{y}-{m:02d}-{d:02d}"
            except (ValueError, IndexError):
                continue
    return None


def extract_amount_from_text(text: str) -> Optional[float]:
    patterns = [
        r"[¥￥]\s*([\d,]+\.?\d*)",
        r"金额[：:]\s*([\d,]+\.?\d*)",
        r"合计[：:]\s*([\d,]+\.?\d*)",
        r"总计[：:]\s*([\d,]+\.?\d*)",
        r"amount[：:]\s*([\d,]+\.?\d*)",
        r"([\d,]+\.?\d*)\s*元",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            amount_str = match.group(1).replace(",", "")
            try:
                return float(amount_str)
            except ValueError:
                continue
    return None


def match_employee_name(text: str, employee_list: list[str]) -> Optional[str]:
    if not employee_list:
        return None
    for name in employee_list:
        if name in text:
            return name
    return None


def classify_receipt_type(text: str) -> str:
    type_keywords = {
        ReceiptType.INVOICE.value: ["发票", "增值税", "普通发票", "专用发票", "invoice"],
        ReceiptType.TRAIN_TICKET.value: ["火车票", "高铁票", "动车票", "铁路", "乘车"],
        ReceiptType.FLIGHT_TICKET.value: ["机票", "航空", "登机牌", "行程单", "航班"],
        ReceiptType.HOTEL.value: ["住宿", "酒店", "宾馆", "房费", "hotel"],
        ReceiptType.TAXI.value: ["出租车", "打车", "滴滴", "taxi", "网约车"],
        ReceiptType.MEAL.value: ["餐饮", "餐费", "饭费", "meal", "用餐"],
    }
    text_lower = text.lower()
    for rtype, keywords in type_keywords.items():
        for kw in keywords:
            if kw in text_lower:
                return rtype
    return ReceiptType.OTHER.value


def text_similarity(text1: str, text2: str) -> float:
    if not text1 or not text2:
        return 0.0
    set1 = set(text1)
    set2 = set(text2)
    intersection = set1 & set2
    union = set1 | set2
    if not union:
        return 0.0
    return len(intersection) / len(union)


def detect_duplicates(receipts: list[Receipt], threshold: float = 0.95) -> list[Receipt]:
    hash_map: dict[str, str] = {}
    for receipt in receipts:
        if receipt.file_hash in hash_map:
            receipt.is_duplicate = True
            receipt.duplicate_of = hash_map[receipt.file_hash]
        else:
            hash_map[receipt.file_hash] = receipt.id

    for i, r1 in enumerate(receipts):
        for j, r2 in enumerate(receipts):
            if i >= j or r1.is_duplicate:
                continue
            if (r1.amount and r2.amount and r1.amount == r2.amount
                    and r1.date and r2.date and r1.date == r2.date
                    and text_similarity(r1.ocr_text, r2.ocr_text) >= threshold):
                r2.is_duplicate = True
                r2.duplicate_of = r1.id

    return receipts


REQUIRED_ATTACHMENTS = {
    ReceiptType.FLIGHT_TICKET.value: ["行程单", "审批单"],
    ReceiptType.HOTEL.value: ["入住确认单", "审批单"],
    ReceiptType.INVOICE.value: ["合同", "审批单"],
}


def check_missing_attachments(receipts: list[Receipt]) -> list[Receipt]:
    for receipt in receipts:
        required = REQUIRED_ATTACHMENTS.get(receipt.receipt_type, [])
        if required:
            found = any(att in receipt.ocr_text for att in required)
            if not found:
                receipt.is_missing_attachment = True
                receipt.missing_attachments = required
    return receipts


def assess_risk(receipts: list[Receipt], amount_threshold: float = 5000.0) -> list[Receipt]:
    from .models import RiskLevel
    for receipt in receipts:
        reasons = []
        if receipt.is_duplicate:
            reasons.append("重复票据")
        if receipt.is_missing_attachment:
            reasons.append("缺少附件")
        if receipt.amount and receipt.amount > amount_threshold:
            reasons.append("金额超过阈值")
        if not receipt.date:
            reasons.append("缺少日期")
        if not receipt.amount:
            reasons.append("缺少金额")

        if len(reasons) >= 2:
            receipt.risk_level = RiskLevel.HIGH.value
        elif len(reasons) == 1:
            receipt.risk_level = RiskLevel.MEDIUM.value
        else:
            receipt.risk_level = RiskLevel.LOW.value
        receipt.risk_reason = "；".join(reasons) if reasons else ""
    return receipts


def group_by_project(receipts: list[Receipt], project_list: list[str]) -> dict[str, list[Receipt]]:
    groups: dict[str, list[Receipt]] = {"未分类": []}
    for p in project_list:
        groups[p] = []

    for receipt in receipts:
        matched = False
        if receipt.project and receipt.project in groups:
            groups[receipt.project].append(receipt)
            matched = True
        else:
            text = receipt.ocr_text.lower()
            for project in project_list:
                if project.lower() in text:
                    receipt.project = project
                    groups[project].append(receipt)
                    matched = True
                    break
        if not matched:
            groups["未分类"].append(receipt)
    return groups


def filter_by_month(receipts: list[Receipt], month_filter: str) -> list[Receipt]:
    filtered = []
    for receipt in receipts:
        if receipt.date and receipt.date.startswith(month_filter):
            filtered.append(receipt)
    return filtered


def format_amount(amount: Optional[float]) -> str:
    if amount is None:
        return ""
    return f"¥{amount:,.2f}"
