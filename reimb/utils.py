from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from .models import Receipt, ReceiptType, RiskLevel, ExtractionStatus


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
        r"开票日期[:：\s]*(\d{4})\s*[年/\-.]\s*(\d{1,2})\s*[月/\-.]\s*(\d{1,2})",
        r"乘车日期[:：\s]*(\d{4})\s*[年/\-.]\s*(\d{1,2})\s*[月/\-.]\s*(\d{1,2})",
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
        r"票价[：:]\s*([\d,]+\.?\d*)",
        r"价税合计[：:]\s*[¥￥]?\s*([\d,]+\.?\d*)",
        r"小写[：:]\s*[¥￥]?\s*([\d,]+\.?\d*)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            amount_str = match.group(1).replace(",", "")
            try:
                val = float(amount_str)
                if val > 0:
                    return val
            except ValueError:
                continue
    return None


def match_employee_name(text: str, employee_list: list[str]) -> Optional[str]:
    if not employee_list:
        return None
    for name in employee_list:
        if name and name in text:
            return name
    return None


def classify_receipt_type(text: str) -> str:
    type_keywords = {
        ReceiptType.INVOICE.value: ["发票", "增值税", "普通发票", "专用发票", "invoice"],
        ReceiptType.TRAIN_TICKET.value: ["火车票", "高铁票", "动车票", "铁路", "乘车", "G字头", "D字头"],
        ReceiptType.FLIGHT_TICKET.value: ["机票", "航空", "登机牌", "行程单", "航班", "MU", "CA", "CZ"],
        ReceiptType.HOTEL.value: ["住宿", "酒店", "宾馆", "房费", "hotel", "入住"],
        ReceiptType.TAXI.value: ["出租车", "打车", "滴滴", "taxi", "网约车", "高德", "滴滴出行"],
        ReceiptType.MEAL.value: ["餐饮", "餐费", "饭费", "meal", "用餐", "餐厅", "饭店"],
    }
    text_lower = text.lower()
    for rtype, keywords in type_keywords.items():
        for kw in keywords:
            if kw.lower() in text_lower:
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
        receipt.is_duplicate = False
        receipt.duplicate_of = None
        if receipt.file_hash in hash_map:
            receipt.is_duplicate = True
            receipt.duplicate_of = hash_map[receipt.file_hash]
        else:
            hash_map[receipt.file_hash] = receipt.id

    for i, r1 in enumerate(receipts):
        for j, r2 in enumerate(receipts):
            if i >= j or r2.is_duplicate:
                continue
            if (r1.amount and r2.amount and r1.amount == r2.amount
                    and r1.date and r2.date and r1.date == r2.date
                    and text_similarity(r1.ocr_text, r2.ocr_text) >= threshold):
                r2.is_duplicate = True
                r2.duplicate_of = r1.id

    return receipts


def check_missing_attachments(receipts: list[Receipt],
                              attachment_rules: Optional[dict[str, list]] = None) -> list[Receipt]:
    default_rules = {
        ReceiptType.FLIGHT_TICKET.value: ["行程单", "审批单"],
        ReceiptType.HOTEL.value: ["入住确认单", "审批单"],
        ReceiptType.INVOICE.value: ["合同", "审批单"],
    }
    rules = attachment_rules if attachment_rules else default_rules

    for receipt in receipts:
        receipt.is_missing_attachment = False
        receipt.missing_attachments = []
        required = rules.get(receipt.receipt_type, [])
        if required:
            found = any(att in receipt.ocr_text for att in required)
            if not found:
                receipt.is_missing_attachment = True
                receipt.missing_attachments = list(required)
    return receipts


def assess_risk(receipts: list[Receipt], amount_threshold: float = 5000.0) -> list[Receipt]:
    for receipt in receipts:
        reasons = []
        if receipt.is_duplicate:
            reasons.append("重复票据")
        if receipt.is_missing_attachment:
            reasons.append("缺少附件")
        if receipt.amount and receipt.amount > amount_threshold:
            reasons.append(f"金额超过阈值({format_amount(amount_threshold)})")
        if not receipt.date:
            reasons.append("缺少日期")
        if not receipt.amount:
            reasons.append("缺少金额")
        if not receipt.employee:
            reasons.append("未匹配员工")

        if len(reasons) >= 2:
            receipt.risk_level = RiskLevel.HIGH.value
        elif len(reasons) == 1:
            receipt.risk_level = RiskLevel.MEDIUM.value
        else:
            receipt.risk_level = RiskLevel.LOW.value
        receipt.risk_reason = "；".join(reasons) if reasons else ""
    return receipts


def determine_extraction_status(receipt: Receipt) -> str:
    if receipt.extraction_error:
        return ExtractionStatus.FAILED.value
    if receipt.is_modified:
        return ExtractionStatus.MODIFIED.value
    if not receipt.ocr_text:
        return ExtractionStatus.PENDING.value
    has_all = receipt.date and receipt.amount and receipt.receipt_type != ReceiptType.OTHER.value
    has_some = receipt.date or receipt.amount
    if has_all:
        return ExtractionStatus.SUCCESS.value
    if has_some:
        return ExtractionStatus.PARTIAL.value
    return ExtractionStatus.FAILED.value


def group_by_project(receipts: list[Receipt],
                     project_list: list[str],
                     project_keywords: Optional[dict[str, list]] = None) -> dict[str, list[Receipt]]:
    groups: dict[str, list[Receipt]] = {"未分类": []}
    for p in project_list:
        groups[p] = []

    keywords_dict = project_keywords if project_keywords else {}
    for project in project_list:
        if project not in keywords_dict:
            keywords_dict[project] = [project]

    for receipt in receipts:
        matched = False
        if receipt.project and receipt.project in groups:
            groups[receipt.project].append(receipt)
            matched = True
        else:
            text = receipt.ocr_text.lower()
            for project, keywords in keywords_dict.items():
                for kw in keywords:
                    if kw.lower() in text:
                        receipt.project = project
                        groups[project].append(receipt)
                        matched = True
                        break
                if matched:
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


def generate_batch_id(counter: int) -> str:
    date_str = datetime.now().strftime("%Y%m%d")
    return f"B{date_str}_{counter:03d}"


def check_export_files_exist(export_records: list) -> list[dict]:
    result = []
    for er in export_records:
        if isinstance(er, dict):
            fp = er.get("filepath", "")
            er_dict = dict(er)
        else:
            fp = er.filepath if hasattr(er, "filepath") else ""
            er_dict = er.to_dict() if hasattr(er, "to_dict") else {}
        er_dict["file_exists"] = Path(fp).exists()
        result.append(er_dict)
    return result


def clean_invalid_export_records(export_records: list) -> tuple[list, int]:
    valid = []
    removed = 0
    for er in export_records:
        if isinstance(er, dict):
            fp = er.get("filepath", "")
        else:
            fp = er.filepath if hasattr(er, "filepath") else ""
        if Path(fp).exists():
            valid.append(er)
        else:
            removed += 1
    return valid, removed


def get_export_records_by_batch(export_records: list, batch_id: str) -> list:
    return [er for er in export_records
            if (isinstance(er, dict) and er.get("batch_id") == batch_id)
            or (hasattr(er, "batch_id") and er.batch_id == batch_id)]


def get_available_batches(export_records: list) -> list[dict]:
    batches = {}
    for er in export_records:
        if isinstance(er, dict):
            bid = er.get("batch_id", "")
            op = er.get("operation", "export")
            ts = er.get("timestamp", "")
            mf = er.get("month_filter")
            amt = er.get("total_amount", 0) or 0
            rc = er.get("record_count", 0) or 0
            oper = er.get("operator", "财务人员")
        else:
            bid = er.batch_id if hasattr(er, "batch_id") else ""
            op = er.operation if hasattr(er, "operation") else "export"
            ts = er.timestamp if hasattr(er, "timestamp") else ""
            mf = er.month_filter if hasattr(er, "month_filter") else None
            amt = er.total_amount if hasattr(er, "total_amount") else 0
            rc = er.record_count if hasattr(er, "record_count") else 0
            oper = er.operator if hasattr(er, "operator") else "财务人员"
        if not bid:
            continue
        if bid not in batches:
            batches[bid] = {
                "batch_id": bid,
                "operation": op,
                "first_timestamp": ts,
                "operator": oper,
                "months": set(),
                "file_count": 0,
                "total_amount": 0.0,
                "record_count": 0,
                "monthly_summary": {},
            }
        batches[bid]["file_count"] += 1
        if amt > batches[bid]["total_amount"]:
            batches[bid]["total_amount"] = amt
        if rc > batches[bid]["record_count"]:
            batches[bid]["record_count"] = rc
        if mf:
            batches[bid]["months"].add(mf)
            if mf not in batches[bid]["monthly_summary"]:
                batches[bid]["monthly_summary"][mf] = {
                    "record_count": 0,
                    "total_amount": 0.0,
                    "file_count": 0,
                }
            batches[bid]["monthly_summary"][mf]["file_count"] += 1
            if rc > batches[bid]["monthly_summary"][mf]["record_count"]:
                batches[bid]["monthly_summary"][mf]["record_count"] = rc
            if amt > batches[bid]["monthly_summary"][mf]["total_amount"]:
                batches[bid]["monthly_summary"][mf]["total_amount"] = amt

    for bid, data in batches.items():
        data["months"] = sorted(data["months"])
        data["month_filter"] = data["months"][0] if len(data["months"]) == 1 else None
        data["month_range"] = f"{data['months'][0]}~{data['months'][-1]}" if len(data["months"]) > 1 else (data["months"][0] if data["months"] else "-")
        if len(data["months"]) > 1:
            total_records = sum(m["record_count"] for m in data["monthly_summary"].values())
            total_amount = sum(m["total_amount"] for m in data["monthly_summary"].values())
            data["record_count"] = total_records
            data["total_amount"] = total_amount
        del data["monthly_summary"]

    return sorted(batches.values(), key=lambda x: x["first_timestamp"], reverse=True)
