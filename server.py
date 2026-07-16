import csv
import io
import json
import os
import re
import uuid
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", APP_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "financeiro.json"
ADMIN_PIN = os.environ.get("ADMIN_PIN", "inove2026")

app = Flask(__name__, static_folder=None)


DEFAULT_MEMBERS = ["Lucas", "Debora", "Andre", "Daniel", "Helen"]
DEFAULT_EXPENSES = [
    ("Aluguel", "3534.01", ["Lucas", "Debora", "Andre", "Helen"], "Exemplo vindo da divisao atual"),
    ("Urgtec", "200.00", ["Lucas", "Debora", "Andre", "Daniel", "Helen"], "Sistema LabFacil/Urgtec"),
    ("Cafe", "400.00", ["Lucas", "Debora", "Andre"], "Exemplo de compra compartilhada"),
    ("Agua", "161.00", ["Lucas", "Debora", "Andre", "Daniel", "Helen"], ""),
    ("Luz", "1788.00", ["Lucas", "Debora", "Andre", "Helen"], ""),
]


def money(value):
    text = str(value or "0").strip()
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        number = Decimal(text)
    except Exception:
        number = Decimal("0")
    return number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def as_float(value):
    return float(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def slug(text):
    normalized = (
        str(text)
        .lower()
        .replace("á", "a")
        .replace("à", "a")
        .replace("ã", "a")
        .replace("â", "a")
        .replace("é", "e")
        .replace("ê", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ô", "o")
        .replace("õ", "o")
        .replace("ú", "u")
        .replace("ç", "c")
    )
    return re.sub(r"[^a-z0-9]+", "-", normalized).strip("-") or uuid.uuid4().hex[:8]


def now_iso():
    return datetime.now().replace(microsecond=0).isoformat()


def current_month():
    return date.today().isoformat()[:7]


def next_month(month):
    year, month_number = [int(part) for part in month.split("-")]
    month_number += 1
    if month_number == 13:
        month_number = 1
        year += 1
    return f"{year:04d}-{month_number:02d}"


def month_first_day(month):
    return f"{month}-01"


def normalize_db(data):
    data.setdefault("members", [])
    data.setdefault("expenses", [])
    data.setdefault("recurring", [])
    data.setdefault("movements", [])
    for expense in data["expenses"]:
        expense.setdefault("kind", expense.get("type") or "debit")
        expense.setdefault("paid", False)
        expense.setdefault("recurring_template_id", None)
        expense.setdefault("created_at", now_iso())
        expense.setdefault("updated_at", now_iso())
    for template in data["recurring"]:
        template.setdefault("active", True)
        template.setdefault("kind", "debit")
        template.setdefault("day", 1)
    for movement in data["movements"]:
        movement.setdefault("created_at", now_iso())
    return data


def load_db():
    if not DB_PATH.exists():
        today = date.today().isoformat()
        members = [
            {"id": slug(name), "name": name, "active": True, "created_at": now_iso()}
            for name in DEFAULT_MEMBERS
        ]
        by_name = {m["name"]: m["id"] for m in members}
        expenses = []
        for name, amount, names, note in DEFAULT_EXPENSES:
            expenses.append(
                {
                    "id": uuid.uuid4().hex,
                    "name": name,
                    "amount": as_float(money(amount)),
                    "kind": "debit",
                    "date": today,
                    "participants": [by_name[n] for n in names if n in by_name],
                    "notes": note,
                    "paid": False,
                    "recurring_template_id": None,
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                }
            )
        save_db({"members": members, "expenses": expenses, "recurring": [], "movements": []})
    with DB_PATH.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    data = normalize_db(data)
    save_db(data)
    return data


def save_db(data):
    tmp = DB_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(normalize_db(data), fh, ensure_ascii=False, indent=2)
    tmp.replace(DB_PATH)


def find_member(data, member_id):
    return next((m for m in data["members"] if m["id"] == member_id), None)


def require_admin():
    pin = request.headers.get("X-Admin-Pin", "")
    if pin != ADMIN_PIN:
        return jsonify({"error": "PIN do admin incorreto."}), 403
    return None


def expense_view(expense, members):
    participant_ids = [pid for pid in expense.get("participants", []) if pid in members]
    total = money(expense.get("amount"))
    kind = "credit" if expense.get("kind") == "credit" else "debit"
    sign = Decimal("-1") if kind == "credit" else Decimal("1")
    count = len(participant_ids)
    cents_total = int((total * 100).to_integral_value())
    base_cents = cents_total // count if count else 0
    remainder = cents_total - (base_cents * count)
    shares = {}
    for index, member_id in enumerate(participant_ids):
        cents = base_cents + (1 if index < remainder else 0)
        shares[member_id] = as_float((Decimal(cents) / Decimal("100")) * sign)
    share_amount = (total / count).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if count else Decimal("0")
    return {
        **expense,
        "kind": kind,
        "amount": as_float(total),
        "signed_amount": as_float(total * sign),
        "participant_count": count,
        "share_amount": as_float(share_amount * sign),
        "shares": shares,
    }


def movement_view(movement):
    kind = movement.get("kind", "production")
    amount = money(movement.get("amount"))
    sign = Decimal("1") if kind in ("production", "credit") else Decimal("-1")
    return {**movement, "kind": kind, "amount": as_float(amount), "signed_amount": as_float(amount * sign)}


def month_filter(items, month):
    if not month:
        return items
    return [item for item in items if str(item.get("date", "")).startswith(month)]


def dashboard(data, month=None):
    month = month or current_month()
    members = {m["id"]: m for m in data["members"]}
    expenses = [expense_view(e, members) for e in month_filter(data["expenses"], month)]
    movements = [movement_view(m) for m in month_filter(data["movements"], month)]

    expense_totals = {m["id"]: 0 for m in data["members"]}
    for expense in expenses:
        for member_id, value in expense["shares"].items():
            expense_totals[member_id] = round(expense_totals.get(member_id, 0) + value, 2)

    movement_totals = {
        m["id"]: {"production": 0, "credit": 0, "debit": 0, "net": 0}
        for m in data["members"]
    }
    for movement in movements:
        member_id = movement.get("member_id")
        if member_id not in movement_totals:
            continue
        kind = movement.get("kind")
        amount = movement.get("amount", 0)
        signed = movement.get("signed_amount", 0)
        if kind in movement_totals[member_id]:
            movement_totals[member_id][kind] = round(movement_totals[member_id][kind] + amount, 2)
        movement_totals[member_id]["net"] = round(movement_totals[member_id]["net"] + signed, 2)

    admin_balances = {}
    for member in data["members"]:
        member_id = member["id"]
        expenses_due = expense_totals.get(member_id, 0)
        movements_net = movement_totals[member_id]["net"]
        admin_balances[member_id] = {
            "expenses": round(expenses_due, 2),
            "production": movement_totals[member_id]["production"],
            "credit": movement_totals[member_id]["credit"],
            "debit": movement_totals[member_id]["debit"],
            "balance": round(movements_net - expenses_due, 2),
        }

    return {
        "members": data["members"],
        "expenses": sorted(expenses, key=lambda e: (e.get("date", ""), e.get("created_at", "")), reverse=True),
        "recurring": sorted(data["recurring"], key=lambda e: e.get("name", "")),
        "movements": sorted(movements, key=lambda e: (e.get("date", ""), e.get("created_at", "")), reverse=True),
        "totals": expense_totals,
        "movement_totals": movement_totals,
        "admin_balances": admin_balances,
        "grand_total": round(sum(e.get("signed_amount", e["amount"]) for e in expenses), 2),
        "debit_total": round(sum(e["amount"] for e in expenses if e.get("kind") != "credit"), 2),
        "credit_total": round(sum(e["amount"] for e in expenses if e.get("kind") == "credit"), 2),
        "month": month,
        "next_month": next_month(month),
    }


def template_from_payload(payload, participants):
    return {
        "id": uuid.uuid4().hex,
        "name": str(payload.get("name", "")).strip(),
        "amount": as_float(money(payload.get("amount"))),
        "kind": "credit" if payload.get("kind") == "credit" else "debit",
        "participants": participants,
        "notes": str(payload.get("notes", "")).strip(),
        "day": int(payload.get("day") or 1),
        "active": True,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }


def expense_from_template(template, month):
    day = max(1, min(28, int(template.get("day") or 1)))
    return {
        "id": uuid.uuid4().hex,
        "name": template["name"],
        "amount": template["amount"],
        "kind": template.get("kind", "debit"),
        "date": f"{month}-{day:02d}",
        "participants": template.get("participants", []),
        "notes": template.get("notes", ""),
        "paid": False,
        "recurring_template_id": template["id"],
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }


@app.get("/")
def index():
    return send_from_directory(APP_DIR, "index.html")


@app.get("/<path:path>")
def static_files(path):
    return send_from_directory(APP_DIR, path)


@app.get("/api/dashboard")
def api_dashboard():
    return jsonify(dashboard(load_db(), request.args.get("month")))


@app.post("/api/admin/unlock")
def admin_unlock():
    error = require_admin()
    if error:
        return error
    return jsonify({"ok": True})


@app.post("/api/members")
def create_member():
    data = load_db()
    payload = request.get_json(force=True)
    name = str(payload.get("name", "")).strip()
    if not name:
        return jsonify({"error": "Nome do socio e obrigatorio."}), 400
    member_id = slug(name)
    existing_ids = {m["id"] for m in data["members"]}
    original = member_id
    counter = 2
    while member_id in existing_ids:
        member_id = f"{original}-{counter}"
        counter += 1
    data["members"].append({"id": member_id, "name": name, "active": True, "created_at": now_iso()})
    save_db(data)
    return jsonify(dashboard(data))


@app.patch("/api/members/<member_id>")
def update_member(member_id):
    data = load_db()
    member = find_member(data, member_id)
    if not member:
        return jsonify({"error": "Socio nao encontrado."}), 404
    payload = request.get_json(force=True)
    if "name" in payload:
        member["name"] = str(payload["name"]).strip() or member["name"]
    if "active" in payload:
        member["active"] = bool(payload["active"])
    save_db(data)
    return jsonify(dashboard(data))


@app.post("/api/expenses")
def create_expense():
    data = load_db()
    payload = request.get_json(force=True)
    name = str(payload.get("name", "")).strip()
    participants = [pid for pid in payload.get("participants", []) if find_member(data, pid)]
    if not name:
        return jsonify({"error": "Nome da despesa e obrigatorio."}), 400
    if not participants:
        return jsonify({"error": "Selecione pelo menos um socio."}), 400
    recurring_template_id = None
    if payload.get("recurring"):
        template = template_from_payload(payload, participants)
        data["recurring"].append(template)
        recurring_template_id = template["id"]
    expense = {
        "id": uuid.uuid4().hex,
        "name": name,
        "amount": as_float(money(payload.get("amount"))),
        "kind": "credit" if payload.get("kind") == "credit" else "debit",
        "date": payload.get("date") or date.today().isoformat(),
        "participants": participants,
        "notes": str(payload.get("notes", "")).strip(),
        "paid": bool(payload.get("paid", False)),
        "recurring_template_id": recurring_template_id,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    data["expenses"].append(expense)
    save_db(data)
    return jsonify(dashboard(data))


@app.patch("/api/expenses/<expense_id>")
def update_expense(expense_id):
    data = load_db()
    expense = next((e for e in data["expenses"] if e["id"] == expense_id), None)
    if not expense:
        return jsonify({"error": "Despesa nao encontrada."}), 404
    payload = request.get_json(force=True)
    for key in ["name", "date", "notes"]:
        if key in payload:
            expense[key] = str(payload[key]).strip()
    if "kind" in payload:
        expense["kind"] = "credit" if payload.get("kind") == "credit" else "debit"
    if "amount" in payload:
        expense["amount"] = as_float(money(payload["amount"]))
    if "participants" in payload:
        participants = [pid for pid in payload["participants"] if find_member(data, pid)]
        if not participants:
            return jsonify({"error": "Selecione pelo menos um socio."}), 400
        expense["participants"] = participants
    if "paid" in payload:
        expense["paid"] = bool(payload["paid"])
    if payload.get("recurring") and not expense.get("recurring_template_id"):
        template = template_from_payload(expense, expense.get("participants", []))
        data["recurring"].append(template)
        expense["recurring_template_id"] = template["id"]
    expense["updated_at"] = now_iso()
    save_db(data)
    return jsonify(dashboard(data))


@app.post("/api/expenses/<expense_id>/recurring")
def make_expense_recurring(expense_id):
    data = load_db()
    expense = next((e for e in data["expenses"] if e["id"] == expense_id), None)
    if not expense:
        return jsonify({"error": "Despesa nao encontrada."}), 404
    if expense.get("recurring_template_id"):
        return jsonify(dashboard(data))
    template = template_from_payload(expense, expense.get("participants", []))
    template["day"] = int(str(expense.get("date", month_first_day(current_month()))).split("-")[-1] or 1)
    data["recurring"].append(template)
    expense["recurring_template_id"] = template["id"]
    expense["updated_at"] = now_iso()
    save_db(data)
    return jsonify(dashboard(data))


@app.post("/api/recurring/generate")
def generate_recurring():
    data = load_db()
    payload = request.get_json(silent=True) or {}
    target_month = payload.get("month") or next_month(current_month())
    created = 0
    existing_keys = {
        (e.get("recurring_template_id"), str(e.get("date", ""))[:7])
        for e in data["expenses"]
        if e.get("recurring_template_id")
    }
    for template in data["recurring"]:
        if not template.get("active", True):
            continue
        key = (template["id"], target_month)
        if key in existing_keys:
            continue
        data["expenses"].append(expense_from_template(template, target_month))
        created += 1
    save_db(data)
    result = dashboard(data, target_month)
    result["created_recurring"] = created
    return jsonify(result)


@app.patch("/api/recurring/<template_id>")
def update_recurring(template_id):
    data = load_db()
    template = next((e for e in data["recurring"] if e["id"] == template_id), None)
    if not template:
        return jsonify({"error": "Recorrencia nao encontrada."}), 404
    payload = request.get_json(force=True)
    if "active" in payload:
        template["active"] = bool(payload["active"])
    template["updated_at"] = now_iso()
    save_db(data)
    return jsonify(dashboard(data, request.args.get("month")))


@app.delete("/api/expenses/<expense_id>")
def delete_expense(expense_id):
    data = load_db()
    before = len(data["expenses"])
    data["expenses"] = [e for e in data["expenses"] if e["id"] != expense_id]
    if len(data["expenses"]) == before:
        return jsonify({"error": "Despesa nao encontrada."}), 404
    save_db(data)
    return jsonify(dashboard(data))


@app.post("/api/admin/movements")
def create_movement():
    error = require_admin()
    if error:
        return error
    data = load_db()
    payload = request.get_json(force=True)
    member_id = payload.get("member_id")
    if not find_member(data, member_id):
        return jsonify({"error": "Socio nao encontrado."}), 404
    kind = payload.get("kind")
    if kind not in ("production", "credit", "debit"):
        return jsonify({"error": "Tipo invalido."}), 400
    movement = {
        "id": uuid.uuid4().hex,
        "member_id": member_id,
        "kind": kind,
        "amount": as_float(money(payload.get("amount"))),
        "date": payload.get("date") or date.today().isoformat(),
        "description": str(payload.get("description", "")).strip(),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    data["movements"].append(movement)
    save_db(data)
    return jsonify(dashboard(data, request.args.get("month")))


@app.delete("/api/admin/movements/<movement_id>")
def delete_movement(movement_id):
    error = require_admin()
    if error:
        return error
    data = load_db()
    before = len(data["movements"])
    data["movements"] = [m for m in data["movements"] if m["id"] != movement_id]
    if len(data["movements"]) == before:
        return jsonify({"error": "Lancamento admin nao encontrado."}), 404
    save_db(data)
    return jsonify(dashboard(data, request.args.get("month")))


@app.get("/api/export.csv")
def export_csv():
    data = dashboard(load_db(), request.args.get("month"))
    members = data["members"]
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["Data", "Tipo", "Despesa", "Valor", "Participantes", *[m["name"] for m in members], "Observacoes"])
    for expense in data["expenses"]:
        writer.writerow(
            [
                expense["date"],
                "Credito" if expense.get("kind") == "credit" else "Debito",
                expense["name"],
                f'{expense["amount"]:.2f}'.replace(".", ","),
                expense["participant_count"],
                *[f'{expense["shares"].get(m["id"], 0):.2f}'.replace(".", ",") for m in members],
                expense.get("notes", ""),
            ]
        )
    return Response(
        out.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=atelier-inove-financeiro.csv"},
    )


if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8798")), debug=True)
