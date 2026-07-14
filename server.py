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

app = Flask(__name__, static_folder=None)


DEFAULT_MEMBERS = ["Lucas", "Débora", "André", "Daniel", "Helen"]
DEFAULT_EXPENSES = [
    ("Aluguel", "3534.01", ["Lucas", "Débora", "André", "Helen"], "Exemplo vindo da divisão atual"),
    ("Urgtec", "200.00", ["Lucas", "Débora", "André", "Daniel", "Helen"], "Sistema LabFácil/Urgtec"),
    ("Café", "400.00", ["Lucas", "Débora", "André"], "Exemplo de compra compartilhada"),
    ("Água", "161.00", ["Lucas", "Débora", "André", "Daniel", "Helen"], ""),
    ("Luz", "1788.00", ["Lucas", "Débora", "André", "Helen"], ""),
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
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or uuid.uuid4().hex[:8]


def now_iso():
    return datetime.now().replace(microsecond=0).isoformat()


def load_db():
    if not DB_PATH.exists():
        today = date.today().isoformat()
        members = [
            {"id": slug(name), "name": name, "active": True, "created_at": now_iso()}
            for name in DEFAULT_MEMBERS
        ]
        expenses = []
        by_name = {m["name"]: m["id"] for m in members}
        for name, amount, names, note in DEFAULT_EXPENSES:
            expenses.append(
                {
                    "id": uuid.uuid4().hex,
                    "name": name,
                    "amount": as_float(money(amount)),
                    "date": today,
                    "participants": [by_name[n] for n in names if n in by_name],
                    "notes": note,
                    "paid": False,
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                }
            )
        save_db({"members": members, "expenses": expenses})
    with DB_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_db(data):
    tmp = DB_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    tmp.replace(DB_PATH)


def find_member(data, member_id):
    return next((m for m in data["members"] if m["id"] == member_id), None)


def expense_view(expense, members):
    participant_ids = [pid for pid in expense.get("participants", []) if pid in members]
    total = money(expense.get("amount"))
    count = len(participant_ids)
    base = (total / count).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if count else Decimal("0")
    cents_total = int((total * 100).to_integral_value())
    base_cents = cents_total // count if count else 0
    remainder = cents_total - (base_cents * count)
    shares = {}
    for index, member_id in enumerate(participant_ids):
        cents = base_cents + (1 if index < remainder else 0)
        shares[member_id] = cents / 100
    return {
        **expense,
        "amount": as_float(total),
        "participant_count": count,
        "share_amount": as_float(base),
        "shares": shares,
    }


def month_filter(expenses, month):
    if not month:
        return expenses
    return [e for e in expenses if str(e.get("date", "")).startswith(month)]


def dashboard(data, month=None):
    members = {m["id"]: m for m in data["members"]}
    expenses = [expense_view(e, members) for e in month_filter(data["expenses"], month)]
    totals = {m["id"]: 0 for m in data["members"]}
    for expense in expenses:
        for member_id, value in expense["shares"].items():
            totals[member_id] = round(totals.get(member_id, 0) + value, 2)
    return {
        "members": data["members"],
        "expenses": sorted(expenses, key=lambda e: (e.get("date", ""), e.get("created_at", "")), reverse=True),
        "totals": totals,
        "grand_total": round(sum(e["amount"] for e in expenses), 2),
        "month": month,
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


@app.post("/api/members")
def create_member():
    data = load_db()
    payload = request.get_json(force=True)
    name = str(payload.get("name", "")).strip()
    if not name:
        return jsonify({"error": "Nome do sócio é obrigatório."}), 400
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
        return jsonify({"error": "Sócio não encontrado."}), 404
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
        return jsonify({"error": "Nome da despesa é obrigatório."}), 400
    if not participants:
        return jsonify({"error": "Selecione pelo menos um sócio."}), 400
    expense = {
        "id": uuid.uuid4().hex,
        "name": name,
        "amount": as_float(money(payload.get("amount"))),
        "date": payload.get("date") or date.today().isoformat(),
        "participants": participants,
        "notes": str(payload.get("notes", "")).strip(),
        "paid": bool(payload.get("paid", False)),
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
        return jsonify({"error": "Despesa não encontrada."}), 404
    payload = request.get_json(force=True)
    for key in ["name", "date", "notes"]:
        if key in payload:
            expense[key] = str(payload[key]).strip()
    if "amount" in payload:
        expense["amount"] = as_float(money(payload["amount"]))
    if "participants" in payload:
        participants = [pid for pid in payload["participants"] if find_member(data, pid)]
        if not participants:
            return jsonify({"error": "Selecione pelo menos um sócio."}), 400
        expense["participants"] = participants
    if "paid" in payload:
        expense["paid"] = bool(payload["paid"])
    expense["updated_at"] = now_iso()
    save_db(data)
    return jsonify(dashboard(data))


@app.delete("/api/expenses/<expense_id>")
def delete_expense(expense_id):
    data = load_db()
    before = len(data["expenses"])
    data["expenses"] = [e for e in data["expenses"] if e["id"] != expense_id]
    if len(data["expenses"]) == before:
        return jsonify({"error": "Despesa não encontrada."}), 404
    save_db(data)
    return jsonify(dashboard(data))


@app.get("/api/export.csv")
def export_csv():
    data = dashboard(load_db(), request.args.get("month"))
    members = data["members"]
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["Data", "Despesa", "Valor", "Participantes", *[m["name"] for m in members], "Observações"])
    for expense in data["expenses"]:
        writer.writerow(
            [
                expense["date"],
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
