"""
quote_detection.py — OpenClaw Purchasing: Quote Detection & Staging

Polls the Asana "Goods In / Order Requests" project for tasks where
ORDER Status = "💢 To be ordered / Requires attention", compiles a
structured quote email draft for each, writes the results to a SharePoint
Excel staging sheet, updates the Asana task status, and sends a Telegram
notification summary.

Usage:
    python quote_detection.py [--dry-run] [--limit N]

    --dry-run   Skip Asana status updates, Excel writes, and Telegram.
                Just print what would happen.
    --limit N   Process at most N tasks (for testing).
"""

import argparse
import io
import json
import logging
import os
import sys
import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import openpyxl
from dotenv import load_dotenv

# ── Path constants ────────────────────────────────────────────────────────────
OPENCLAW_ROOT = Path(r"C:\Users\GilesHugo\.hermes")   # env/config live here post-decommission
ENV_PATH      = OPENCLAW_ROOT / ".env"
CONFIG_PATH   = OPENCLAW_ROOT / "config.json"
SUPPLIERS_PATH = Path(r"C:\Users\GilesHugo\SES_Workspace\data\suppliers.json")

# ── Load .env ─────────────────────────────────────────────────────────────────
load_dotenv(ENV_PATH)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# ── Asana constants ───────────────────────────────────────────────────────────
ASANA_BASE_URL          = "https://app.asana.com/api/1.0"
GOODS_IN_PROJECT_GID    = "358790955858777"
QUOTE_CONTROL_GID       = "1207766004159548"

ORDER_STATUS_GID        = "1143955334207040"
TO_BE_ORDERED_GID       = "1143955334207041"   # trigger enum option
QUOTE_AUTO_EMAIL_GID    = "1144696382567993"   # post-process enum option

SQR_GID                 = "1211774738554014"
VENDOR_GID              = "854127216887083"
EMAIL_GID               = "1109691357791838"
ORDER_QTY_GID           = "1201447044882111"
SUPPLIER_TYPE_GID       = "1205678172863621"

TASK_OPT_FIELDS         = "gid,name,completed,notes,custom_fields,subtasks,permalink_url"
SUBTASK_OPT_FIELDS      = "gid,name,completed"

# ── Graph / SharePoint constants ──────────────────────────────────────────────
GRAPH_BASE              = "https://graph.microsoft.com/v1.0"
PURCHASING_UPN          = "purchasing@sesameaccess.com"
EXCEL_PATH_IN_ONEDRIVE  = "/Documents/OpenClaw/Quotes/Quote_Staging.xlsx"
EXCEL_CONTENT_TYPE      = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
EXCEL_COLUMNS           = [
    "SQR", "Task Name", "Asana Link", "Supplier Name", "Supplier Email",
    "Order QTY", "Line Items", "Drawing URL", "Email Body Draft",
    "Status", "Decision", "Release Notes",
]

# ── HTTP session factory ──────────────────────────────────────────────────────

def _make_session() -> requests.Session:
    """Return a requests.Session with retry logic for transient errors."""
    session = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


SESSION = _make_session()

# ── Config loading ────────────────────────────────────────────────────────────

def load_config() -> dict:
    """Load config.json. Returns empty dict if file missing."""
    if not CONFIG_PATH.exists():
        log.warning("config.json not found at %s", CONFIG_PATH)
        return {}
    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_suppliers() -> list:
    """Load suppliers.json. Returns empty list on failure."""
    if not SUPPLIERS_PATH.exists():
        log.warning("suppliers.json not found at %s", SUPPLIERS_PATH)
        return []
    with SUPPLIERS_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)

# ── Supplier matching ─────────────────────────────────────────────────────────

def match_supplier(vendor_value: str, suppliers: list) -> dict | None:
    """
    Match the task Vendor field value against the suppliers list.

    Priority:
    1. Exact (case-insensitive) match on supplier's vendor_field.
    2. Partial (case-insensitive) match on any item in supplier's tags list.

    Returns the matched supplier dict, or None.
    """
    if not vendor_value:
        return None
    vendor_lower = vendor_value.strip().lower()
    # Pass 1: vendor_field exact match
    for sup in suppliers:
        vf = (sup.get("vendor_field") or "").strip().lower()
        if vf and vf == vendor_lower:
            return sup
    # Pass 2: partial tag match
    for sup in suppliers:
        for tag in sup.get("tags") or []:
            if tag.strip().lower() in vendor_lower or vendor_lower in tag.strip().lower():
                return sup
    return None

# ── Custom field helpers ──────────────────────────────────────────────────────

def _cf_value(custom_fields: list, gid: str):
    """Return the display value for a custom field by GID. Returns None if absent."""
    for cf in custom_fields:
        if cf.get("gid") == gid:
            cf_type = cf.get("type", "")
            if cf_type == "enum":
                ev = cf.get("enum_value")
                return ev.get("gid") if ev else None
            if cf_type == "multi_enum":
                options = cf.get("multi_enum_values") or []
                return [o.get("gid") for o in options]
            if cf_type == "text":
                return cf.get("text_value") or cf.get("display_value")
            if cf_type == "number":
                return cf.get("number_value")
            # formula / read-only fields expose display_value
            return cf.get("display_value") or cf.get("text_value")
    return None


def _cf_display(custom_fields: list, gid: str) -> str:
    """Return a human-readable display value for a custom field."""
    for cf in custom_fields:
        if cf.get("gid") == gid:
            cf_type = cf.get("type", "")
            if cf_type == "enum":
                ev = cf.get("enum_value")
                return ev.get("name", "") if ev else ""
            if cf_type == "multi_enum":
                options = cf.get("multi_enum_values") or []
                return ", ".join(o.get("name", "") for o in options)
            dv = cf.get("display_value")
            if dv is not None:
                return str(dv)
            tv = cf.get("text_value")
            if tv is not None:
                return str(tv)
            nv = cf.get("number_value")
            if nv is not None:
                return str(nv)
            return ""
    return ""

# ── Asana API calls ───────────────────────────────────────────────────────────

def asana_get(path: str, params: dict = None, token: str = "") -> dict:
    """Perform a GET against the Asana API. Returns parsed JSON data."""
    url = f"{ASANA_BASE_URL}{path}"
    headers = {"Authorization": f"Bearer {token}"}
    resp = SESSION.get(url, headers=headers, params=params or {}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def asana_put(path: str, body: dict, token: str = "") -> dict:
    """Perform a PUT against the Asana API."""
    url = f"{ASANA_BASE_URL}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    resp = SESSION.put(url, headers=headers, json=body, timeout=30)
    resp.raise_for_status()
    return resp.json()


def asana_post(path: str, body: dict, token: str = "") -> dict:
    """Perform a POST against the Asana API."""
    url = f"{ASANA_BASE_URL}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    resp = SESSION.post(url, headers=headers, json=body, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_tasks_to_quote(token: str) -> list:
    """
    Fetch all incomplete tasks in the Goods In project and filter
    client-side for ORDER Status = TO_BE_ORDERED_GID.
    """
    params = {
        "opt_fields": TASK_OPT_FIELDS,
        "completed_since": "now",  # only incomplete tasks
    }
    data = asana_get(f"/projects/{GOODS_IN_PROJECT_GID}/tasks", params=params, token=token)
    tasks = data.get("data", [])
    log.info("Fetched %d incomplete tasks from Goods In project.", len(tasks))

    matching = []
    for task in tasks:
        cfs = task.get("custom_fields") or []
        status_gid = _cf_value(cfs, ORDER_STATUS_GID)
        if status_gid == TO_BE_ORDERED_GID:
            matching.append(task)
    log.info("%d task(s) match ORDER Status = To Be Ordered.", len(matching))
    return matching


def fetch_subtasks(task_gid: str, token: str) -> list:
    """Fetch incomplete subtasks for a task."""
    params = {"opt_fields": SUBTASK_OPT_FIELDS}
    data = asana_get(f"/tasks/{task_gid}/subtasks", params=params, token=token)
    all_subs = data.get("data", [])
    incomplete = [s for s in all_subs if not s.get("completed", False)]
    return incomplete


def update_task_status(task_gid: str, token: str, dry_run: bool) -> bool:
    """Set ORDER Status to 'Quote - Auto Email'."""
    if dry_run:
        log.info("[DRY RUN] Would update task %s ORDER Status → Quote - Auto Email", task_gid)
        return True
    try:
        body = {
            "data": {
                "custom_fields": {
                    ORDER_STATUS_GID: QUOTE_AUTO_EMAIL_GID,
                }
            }
        }
        asana_put(f"/tasks/{task_gid}", body=body, token=token)
        time.sleep(0.1)
        log.info("Task %s: ORDER Status updated → Quote - Auto Email", task_gid)
        return True
    except requests.HTTPError as exc:
        log.error("Failed to update ORDER Status for task %s: %s", task_gid, exc)
        return False


def add_task_to_quote_control(task_gid: str, token: str, dry_run: bool) -> bool:
    """Add the task to the Quote Control project."""
    if dry_run:
        log.info("[DRY RUN] Would add task %s to Quote Control project", task_gid)
        return True
    try:
        body = {"data": {"project": QUOTE_CONTROL_GID}}
        asana_post(f"/tasks/{task_gid}/addProject", body=body, token=token)
        time.sleep(0.1)
        log.info("Task %s: added to Quote Control project.", task_gid)
        return True
    except requests.HTTPError as exc:
        log.error("Failed to add task %s to Quote Control: %s", task_gid, exc)
        return False

# ── Email body builder ────────────────────────────────────────────────────────

def build_email_body(sqr: str, task_name: str, line_items: list, order_qty: str,
                     supplier_contact: str) -> str:
    """Compile a plain-text email body for the quote request."""
    contact_name = supplier_contact.strip() if supplier_contact else "Supplier"

    if sqr:
        subject = f"Quote Request - {sqr} - {task_name}"
    else:
        subject = f"Quote Request - {task_name}"

    if line_items:
        items_text = "\n".join(f"{i + 1}. {item}" for i, item in enumerate(line_items))
    else:
        items_text = "(No line items specified)"

    qty_text = str(order_qty) if order_qty not in (None, "", "None") else "See above"

    body = (
        f"Subject: {subject}\n"
        "\n"
        f"Dear {contact_name},\n"
        "\n"
        "Please could you provide a quotation for the following items:\n"
        "\n"
        f"{items_text}\n"
        "\n"
        f"Order Quantity: {qty_text}\n"
        "\n"
        "Please confirm your best price and lead time for each item.\n"
        "\n"
        "Kind regards,\n"
        "Sesame Access Systems\n"
        "purchasing@sesameaccess.com"
    )
    return body

# ── Graph API auth ────────────────────────────────────────────────────────────

def acquire_graph_token(config: dict) -> str:
    """
    Acquire an access token for Microsoft Graph via client_credentials grant.
    Raises SystemExit on failure.
    """
    tenant_id    = config.get("tenantId", "")
    client_id    = config.get("clientId", "")
    # Prefer env var over config.json for the client secret
    client_secret = (
        os.environ.get("PURCHASING_API_KEY")
        or os.environ.get("purchasing_Email_Api_Key")
        or config.get("clientSecret", "")
    )

    if not all([tenant_id, client_id, client_secret]):
        log.error("Graph API credentials incomplete. Check config.json / env vars.")
        sys.exit(1)

    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    data = {
        "grant_type":    "client_credentials",
        "client_id":     client_id,
        "client_secret": client_secret,
        "scope":         "https://graph.microsoft.com/.default",
    }
    try:
        resp = SESSION.post(token_url, data=data, timeout=30)
        resp.raise_for_status()
        token = resp.json().get("access_token", "")
        if not token:
            log.error("Graph token response did not contain access_token.")
            sys.exit(1)
        log.info("Graph API token acquired successfully.")
        return token
    except requests.HTTPError as exc:
        log.error("Failed to acquire Graph API token: %s", exc)
        sys.exit(1)

# ── Excel helpers ─────────────────────────────────────────────────────────────

def _graph_headers(graph_token: str) -> dict:
    return {"Authorization": f"Bearer {graph_token}"}


def _excel_file_url(path_segment: str = "") -> str:
    base = (
        f"{GRAPH_BASE}/users/{PURCHASING_UPN}/drive"
        f"/root:{EXCEL_PATH_IN_ONEDRIVE}"
    )
    return base + path_segment if path_segment else base


def _build_blank_workbook() -> bytes:
    """Create a minimal .xlsx file with the header row using openpyxl."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(EXCEL_COLUMNS)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


def ensure_excel_file(graph_token: str) -> tuple[str, str]:
    """
    Ensure the Excel staging file exists in OneDrive.
    Returns (item_id, web_url).
    Creates the file (with header row) if it does not exist.
    """
    check_url = _excel_file_url()
    headers = _graph_headers(graph_token)

    resp = SESSION.get(check_url, headers=headers, timeout=30)
    if resp.status_code == 200:
        meta = resp.json()
        log.info("Excel staging file exists (item ID: %s).", meta.get("id"))
        return meta.get("id", ""), meta.get("webUrl", "")

    if resp.status_code == 404:
        log.info("Excel staging file not found — creating...")
        xlsx_bytes = _build_blank_workbook()
        upload_url = _excel_file_url(":/content")
        up_resp = SESSION.put(
            upload_url,
            headers={**headers, "Content-Type": EXCEL_CONTENT_TYPE},
            data=xlsx_bytes,
            timeout=60,
        )
        up_resp.raise_for_status()
        meta = up_resp.json()
        log.info("Excel staging file created (item ID: %s).", meta.get("id"))
        return meta.get("id", ""), meta.get("webUrl", "")

    resp.raise_for_status()
    return "", ""


def _append_row_via_excel_api(graph_token: str, item_id: str, row_values: list) -> bool:
    """
    Attempt to append a row using the Graph workbook/Excel API.
    Returns True on success, False on failure (caller should use fallback).
    """
    headers = _graph_headers(graph_token)
    used_range_url = (
        f"{GRAPH_BASE}/users/{PURCHASING_UPN}/drive/items/{item_id}"
        f"/workbook/worksheets/Sheet1/usedRange"
    )
    resp = SESSION.get(used_range_url, headers=headers, timeout=30)
    if resp.status_code in (404, 423):
        log.warning("Excel API usedRange returned %s — will use fallback.", resp.status_code)
        return False
    try:
        resp.raise_for_status()
    except requests.HTTPError:
        log.warning("Excel API usedRange error %s — will use fallback.", resp.status_code)
        return False

    row_count = resp.json().get("rowCount", 1)
    next_row = row_count + 1  # 1-indexed; header is row 1

    col_end = chr(ord("A") + len(EXCEL_COLUMNS) - 1)  # "L" for 12 columns
    address = f"A{next_row}:{col_end}{next_row}"
    patch_url = (
        f"{GRAPH_BASE}/users/{PURCHASING_UPN}/drive/items/{item_id}"
        f"/workbook/worksheets/Sheet1/range(address='{address}')"
    )
    body = {"values": [row_values]}
    patch_resp = SESSION.patch(patch_url, headers=headers, json=body, timeout=30)
    if patch_resp.status_code in (404, 423):
        log.warning("Excel API PATCH returned %s — will use fallback.", patch_resp.status_code)
        return False
    try:
        patch_resp.raise_for_status()
        return True
    except requests.HTTPError:
        log.warning("Excel API PATCH error %s — will use fallback.", patch_resp.status_code)
        return False


def _append_row_via_download_upload(graph_token: str, row_values: list) -> bool:
    """
    Fallback: download the Excel file, append the row with openpyxl, re-upload.
    Returns True on success.
    """
    headers = _graph_headers(graph_token)
    download_url = _excel_file_url(":/content")

    dl_resp = SESSION.get(download_url, headers=headers, timeout=60)
    dl_resp.raise_for_status()

    wb = openpyxl.load_workbook(io.BytesIO(dl_resp.content))
    ws = wb.active
    ws.append(row_values)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    upload_url = _excel_file_url(":/content")
    up_resp = SESSION.put(
        upload_url,
        headers={**headers, "Content-Type": EXCEL_CONTENT_TYPE},
        data=buf.read(),
        timeout=60,
    )
    up_resp.raise_for_status()
    log.info("Fallback upload complete.")
    return True


def append_row_to_excel(
    graph_token: str, item_id: str, row_values: list, dry_run: bool
) -> bool:
    """Append a row to the Excel staging sheet. Uses API first, falls back to upload."""
    if dry_run:
        log.info("[DRY RUN] Would append row to Excel: %s", row_values[:3])
        return True
    if not _append_row_via_excel_api(graph_token, item_id, row_values):
        log.info("Using download/re-upload fallback for Excel append.")
        return _append_row_via_download_upload(graph_token, row_values)
    return True

# ── Telegram notification ─────────────────────────────────────────────────────

def send_quote_summary(n: int, web_url: str, task_labels: list, dry_run: bool):
    """Send a single Telegram summary after all tasks are processed."""
    if n == 0 or dry_run:
        return
    try:
        from notify_telegram import send_telegram  # local import to avoid hard dep at module level
    except ImportError:
        log.warning("notify_telegram not importable — skipping Telegram notification.")
        return

    tasks_str = ", ".join(task_labels) if task_labels else "(none)"
    message = (
        "🔔 <b>Quote Detection Run Complete</b>\n"
        f"Processed: {n} task(s) → Quote staging sheet updated.\n"
        f"Review: {web_url}\n"
        f"Tasks: {tasks_str}"
    )
    ok = send_telegram(message)
    if ok:
        log.info("Telegram summary sent.")
    else:
        log.warning("Telegram summary failed to send.")

# ── Main processing ───────────────────────────────────────────────────────────

def process_task(task: dict, suppliers: list, graph_token: str, item_id: str,
                 asana_token: str, dry_run: bool) -> dict | None:
    """
    Process a single Asana task: match supplier, build email, write to Excel,
    update Asana status, add to Quote Control.
    Returns a summary dict, or None if processing failed fatally.
    """
    task_gid  = task.get("gid", "")
    task_name = task.get("name", "")
    permalink = task.get("permalink_url", "")
    cfs       = task.get("custom_fields") or []

    log.info("Processing task GID=%s name=%r", task_gid, task_name)

    sqr       = _cf_display(cfs, SQR_GID)
    vendor    = _cf_display(cfs, VENDOR_GID)
    email_cf  = _cf_display(cfs, EMAIL_GID)
    order_qty = _cf_display(cfs, ORDER_QTY_GID)

    # ── Supplier matching ──────────────────────────────────────────────────
    matched_supplier = match_supplier(vendor, suppliers)
    if matched_supplier:
        supplier_name    = matched_supplier.get("name") or vendor
        supplier_email   = matched_supplier.get("email") or email_cf
        supplier_contact = matched_supplier.get("contact") or ""
        log.info("Task %s: supplier matched → %r", task_gid, supplier_name)
    else:
        supplier_name    = vendor
        supplier_email   = email_cf
        supplier_contact = ""
        log.info("Task %s: no supplier match — using Vendor field %r", task_gid, vendor)

    # ── Subtasks (line items) ──────────────────────────────────────────────
    try:
        subtasks = fetch_subtasks(task_gid, asana_token)
    except requests.HTTPError as exc:
        log.error("Task %s: failed to fetch subtasks: %s — skipping.", task_gid, exc)
        return None

    line_item_names = [s.get("name", "") for s in subtasks]
    log.info("Task %s: %d incomplete line item(s).", task_gid, len(line_item_names))

    # ── Email body ─────────────────────────────────────────────────────────
    email_body = build_email_body(sqr, task_name, line_item_names, order_qty, supplier_contact)

    # TODO: Drawing URL — always empty; Dropbox API integration not yet configured.
    drawing_url = ""

    line_items_str = "; ".join(line_item_names)

    row_values = [
        sqr,
        task_name,
        permalink,
        supplier_name,
        supplier_email,
        order_qty,
        line_items_str,
        drawing_url,
        email_body,
        "Pending Review",
        "",
        "",
    ]

    # ── Write to Excel ─────────────────────────────────────────────────────
    excel_ok = append_row_to_excel(graph_token, item_id, row_values, dry_run)
    if excel_ok:
        log.info("Task %s: row appended to Excel staging sheet.", task_gid)
    else:
        log.error("Task %s: failed to append row to Excel — continuing.", task_gid)

    # ── Update Asana status ────────────────────────────────────────────────
    status_ok = update_task_status(task_gid, asana_token, dry_run)
    if status_ok:
        log.info("Task %s: Asana status updated.", task_gid)

    # ── Add to Quote Control project ───────────────────────────────────────
    add_task_to_quote_control(task_gid, asana_token, dry_run)

    label = sqr if sqr else task_name
    return {
        "gid":           task_gid,
        "name":          task_name,
        "sqr":           sqr,
        "label":         label,
        "supplier":      supplier_name,
        "line_items":    len(line_item_names),
        "excel_ok":      excel_ok,
        "status_ok":     status_ok,
    }


def main():
    parser = argparse.ArgumentParser(
        description="OpenClaw Quote Detection — polls Asana, stages quotes in Excel."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip Asana writes, Excel writes, and Telegram. Print what would happen.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Process at most N tasks (for testing).",
    )
    args = parser.parse_args()
    dry_run = args.dry_run

    if dry_run:
        log.info("*** DRY RUN MODE — no writes will be performed ***")

    # ── Asana token ────────────────────────────────────────────────────────
    asana_token = os.environ.get("ASANA_PERSONAL_ACCESS_TOKEN", "")
    if not asana_token:
        log.error(
            "ASANA_PERSONAL_ACCESS_TOKEN not set. "
            "Export it as an environment variable or add it to %s", ENV_PATH
        )
        sys.exit(1)

    # ── Graph config & token ───────────────────────────────────────────────
    config = load_config()
    graph_token = acquire_graph_token(config)

    # ── Ensure Excel file exists ───────────────────────────────────────────
    if not dry_run:
        try:
            item_id, web_url = ensure_excel_file(graph_token)
        except requests.HTTPError as exc:
            log.error("Failed to ensure Excel staging file: %s", exc)
            sys.exit(1)
    else:
        item_id  = "DRY_RUN_ITEM_ID"
        web_url  = "https://sharepoint.example.com/dry-run"
        log.info("[DRY RUN] Skipping Excel file check.")

    # ── Suppliers ──────────────────────────────────────────────────────────
    suppliers = load_suppliers()
    log.info("Loaded %d supplier(s).", len(suppliers))

    # ── Fetch matching tasks ───────────────────────────────────────────────
    try:
        tasks = fetch_tasks_to_quote(asana_token)
    except requests.HTTPError as exc:
        log.error("Failed to fetch tasks from Asana: %s", exc)
        sys.exit(1)

    if args.limit is not None:
        tasks = tasks[: args.limit]
        log.info("Limiting to %d task(s) as requested.", len(tasks))

    if not tasks:
        log.info("No tasks requiring quotes. Nothing to do.")
        print("Quote detection complete: 0 tasks processed.")
        return

    # ── Process each task ──────────────────────────────────────────────────
    results = []
    for task in tasks:
        try:
            result = process_task(
                task, suppliers, graph_token, item_id, asana_token, dry_run
            )
            if result:
                results.append(result)
        except Exception as exc:
            log.error(
                "Unexpected error processing task GID=%s: %s — skipping.",
                task.get("gid"), exc,
            )
            continue

    # ── Telegram summary ───────────────────────────────────────────────────
    n = len(results)
    task_labels = [r["label"] for r in results]
    send_quote_summary(n, web_url, task_labels, dry_run)

    # ── Final summary ──────────────────────────────────────────────────────
    print(f"\nQuote detection complete: {n} task(s) processed.")
    for r in results:
        status = "OK" if r.get("status_ok") else "STATUS UPDATE FAILED"
        excel  = "Excel OK" if r.get("excel_ok") else "EXCEL FAILED"
        print(
            f"  [{status}] [{excel}] GID={r['gid']} "
            f"SQR={r['sqr'] or '(none)'} supplier={r['supplier']} "
            f"line_items={r['line_items']}"
        )


if __name__ == "__main__":
    main()
