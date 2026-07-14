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
DB_PATH = DATA_DIR / "dividebem.json"

app = Flask(__name__, static_folder=None)


DEFAULT_TEMPLATES = [
    "Aluguel", "Condomínio", "Luz", "Água", "Internet", "Gás", "Mercado",
    "Feira", "Farmácia", "Limpeza", "Faxina", "Manutenção", "Transporte",
    "Combustível", "Estacionamento", "Assinaturas", "Streaming", "Telefone",
    "Seguro", "Imposto", "IPTU", "Material de escritório", "Café",
    "Funcionário", "Prestador de serviço", "Escola", "Pet", "Lazer",
    "Viagem", "Restaurante", "Presentes",
]
DEFAULT_CATEGORIES = [
    "Moradia", "Contas fixas", "Mercado", "Trabalho", "Saúde", "Transporte",
    "Assinaturas", "Manutenção", "Lazer", "Outros",
]
LEGACY_LAB_MEMBER_NAMES = {"Lucas", "Débora", "André", "Daniel", "Helen"}
LEGACY_LAB_TEMPLATES = {
    "Urgtec", "Dental", "Embalagem", "Laundry", "Scanner", "Terreno",
    "Admin", "Comissão", "Correio", "Forno", "Lalomere",
}


def now_iso():
    return datetime.now().replace(microsecond=0).isoformat()


def new_id():
    return uuid.uuid4().hex


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


def norm(text):
    return re.sub(r"\s+", " ", str(text or "").strip())


def seed_db():
    group_id = new_id()
    members = []
    return {
        "app": {
            "name": "DivideBem",
            "tagline": "Contas claras em casa e no trabalho",
            "seed_version": 5,
        },
        "profile": {
            "name": "",
            "phone": "",
            "photo": "",
            "created_at": now_iso(),
        },
        "groups": [{
            "id": group_id,
            "name": "Minha casa",
            "kind": "home",
            "member_ids": [],
            "created_at": now_iso(),
        }],
        "members": members,
        "expenses": [],
        "templates": DEFAULT_TEMPLATES,
        "categories": DEFAULT_CATEGORIES,
    }


def migrate(data):
    changed = False
    if "app" not in data:
        data["app"] = {"name": "DivideBem", "tagline": "Contas claras em casa e no trabalho", "seed_version": 5}
        changed = True
    data["app"]["name"] = "DivideBem"
    data["app"]["tagline"] = "Contas claras em casa e no trabalho"
    data.setdefault("profile", {"name": "", "phone": "", "photo": "", "created_at": now_iso()})
    data.setdefault("members", [])
    data.setdefault("expenses", [])
    data.setdefault("templates", [])
    data.setdefault("categories", [])
    if "groups" not in data:
        group_id = new_id()
        data["groups"] = [{
            "id": group_id,
            "name": "Minha casa",
            "kind": "home",
            "member_ids": [m["id"] for m in data["members"]],
            "created_at": now_iso(),
        }]
        changed = True
    if data["app"].get("seed_version", 1) < 5:
        has_only_legacy_members = data.get("members") and {m.get("name") for m in data["members"]}.issubset(LEGACY_LAB_MEMBER_NAMES)
        has_no_real_expenses = not data.get("expenses")
        if has_only_legacy_members and has_no_real_expenses:
            data["members"] = []
            for group in data["groups"]:
                group["member_ids"] = []
                if group.get("name") == "Casa / Trabalho":
                    group["name"] = "Minha casa"
                    group["kind"] = "home"
            changed = True
        data["templates"] = [t for t in data.get("templates", []) if t not in LEGACY_LAB_TEMPLATES]
        data["app"]["seed_version"] = 5
        changed = True
    for item in DEFAULT_TEMPLATES:
        if item.casefold() not in {t.casefold() for t in data["templates"]}:
            data["templates"].append(item)
            changed = True
    for item in DEFAULT_CATEGORIES:
        if item.casefold() not in {c.casefold() for c in data["categories"]}:
            data["categories"].append(item)
            changed = True
    for expense in data["expenses"]:
        if "group_id" not in expense:
            expense["group_id"] = data["groups"][0]["id"]
            changed = True
        expense.setdefault("category", "Outros")
        expense.setdefault("paid_by", "")
        expense.setdefault("status", "open")
        expense.setdefault("split_mode", "equal")
        expense.setdefault("weights", {})
        expense.setdefault("manual_shares", {})
        expense.setdefault("receipt", "")
        expense.setdefault("recurring", False)
    return changed


def load_db():
    if not DB_PATH.exists():
        data = seed_db()
        save_db(data)
        return data
    with DB_PATH.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if migrate(data):
        save_db(data)
    return data


def save_db(data):
    tmp = DB_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    tmp.replace(DB_PATH)


def by_id(items):
    return {item["id"]: item for item in items}


def remember(data, key, value):
    value = norm(value)
    if not value:
        return
    existing = {item.casefold() for item in data.setdefault(key, [])}
    if value.casefold() not in existing:
        data[key].append(value)


def expense_view(expense, members):
    participant_ids = [pid for pid in expense.get("participants", []) if pid in members]
    total = money(expense.get("amount"))
    shares = {}
    mode = expense.get("split_mode", "equal")
    if participant_ids and mode == "manual":
        assigned = Decimal("0")
        for pid in participant_ids:
            value = money(expense.get("manual_shares", {}).get(pid, 0))
            shares[pid] = as_float(value)
            assigned += value
        diff = total - assigned
        if diff and participant_ids:
            shares[participant_ids[0]] = as_float(money(shares.get(participant_ids[0], 0)) + diff)
    elif participant_ids and mode == "percent":
        weights = expense.get("weights", {})
        weight_sum = sum(Decimal(str(weights.get(pid, 0) or 0)) for pid in participant_ids)
        if weight_sum <= 0:
            weight_sum = Decimal(len(participant_ids))
            weights = {pid: 1 for pid in participant_ids}
        assigned = Decimal("0")
        for index, pid in enumerate(participant_ids):
            if index == len(participant_ids) - 1:
                value = total - assigned
            else:
                value = (total * Decimal(str(weights.get(pid, 0))) / weight_sum).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                assigned += value
            shares[pid] = as_float(value)
    elif participant_ids:
        cents_total = int((total * 100).to_integral_value())
        base = cents_total // len(participant_ids)
        remainder = cents_total - base * len(participant_ids)
        for index, pid in enumerate(participant_ids):
            shares[pid] = (base + (1 if index < remainder else 0)) / 100
    return {
        **expense,
        "amount": as_float(total),
        "participant_count": len(participant_ids),
        "shares": shares,
    }


def filtered(expenses, args):
    month = args.get("month")
    query = norm(args.get("q")).casefold()
    group_id = args.get("group")
    member_id = args.get("member")
    status = args.get("status")
    category = args.get("category")
    day = args.get("date")
    out = []
    for expense in expenses:
        if month and not str(expense.get("date", "")).startswith(month):
            continue
        if group_id and expense.get("group_id") != group_id:
            continue
        if member_id and member_id not in expense.get("participants", []):
            continue
        if status and expense.get("status") != status:
            continue
        if category and expense.get("category") != category:
            continue
        if day and expense.get("date") != day:
            continue
        haystack = f"{expense.get('name', '')} {expense.get('notes', '')} {expense.get('category', '')}".casefold()
        if query and query not in haystack:
            continue
        out.append(expense)
    return out


def dashboard(data, args=None):
    args = args or {}
    members = by_id(data["members"])
    expenses = [expense_view(e, members) for e in filtered(data["expenses"], args)]
    expenses.sort(key=lambda e: (e.get("date", ""), e.get("created_at", "")), reverse=True)
    totals = {m["id"]: 0 for m in data["members"]}
    paid_by = {m["id"]: 0 for m in data["members"]}
    for expense in expenses:
        if expense.get("paid_by") in paid_by:
            paid_by[expense["paid_by"]] = round(paid_by[expense["paid_by"]] + expense["amount"], 2)
        for pid, value in expense["shares"].items():
            totals[pid] = round(totals.get(pid, 0) + value, 2)
    balances = {pid: round(paid_by.get(pid, 0) - totals.get(pid, 0), 2) for pid in totals}
    return {
        "app": data["app"],
        "profile": data["profile"],
        "groups": data["groups"],
        "members": data["members"],
        "expenses": expenses,
        "templates": sorted(data["templates"], key=str.casefold),
        "categories": sorted(data["categories"], key=str.casefold),
        "totals": totals,
        "paid_by": paid_by,
        "balances": balances,
        "grand_total": round(sum(e["amount"] for e in expenses), 2),
    }


@app.get("/")
def index():
    return send_from_directory(APP_DIR, "index.html")


@app.get("/privacy")
def privacy():
    return send_from_directory(APP_DIR, "privacy.html")


@app.get("/<path:path>")
def static_files(path):
    return send_from_directory(APP_DIR, path)


@app.get("/api/dashboard")
def api_dashboard():
    return jsonify(dashboard(load_db(), request.args))


@app.patch("/api/profile")
def update_profile():
    data = load_db()
    payload = request.get_json(force=True)
    for key in ["name", "phone", "photo"]:
        if key in payload:
            data["profile"][key] = payload[key]
    save_db(data)
    return jsonify(dashboard(data, request.args))


@app.post("/api/groups")
def create_group():
    data = load_db()
    payload = request.get_json(force=True)
    group = {
        "id": new_id(),
        "name": norm(payload.get("name")) or "Novo grupo",
        "kind": payload.get("kind") or "home",
        "member_ids": payload.get("member_ids") or [],
        "created_at": now_iso(),
    }
    data["groups"].append(group)
    save_db(data)
    return jsonify(dashboard(data))


@app.post("/api/members")
def create_member():
    data = load_db()
    payload = request.get_json(force=True)
    name = norm(payload.get("name"))
    if not name:
        return jsonify({"error": "Nome do participante é obrigatório."}), 400
    member = {
        "id": new_id(),
        "name": name,
        "phone": norm(payload.get("phone")),
        "photo": payload.get("photo", ""),
        "active": True,
        "created_at": now_iso(),
    }
    data["members"].append(member)
    group_id = payload.get("group_id")
    if group_id:
        for group in data["groups"]:
            if group["id"] == group_id and member["id"] not in group["member_ids"]:
                group["member_ids"].append(member["id"])
    save_db(data)
    return jsonify(dashboard(data))


@app.patch("/api/members/<member_id>")
def update_member(member_id):
    data = load_db()
    member = next((m for m in data["members"] if m["id"] == member_id), None)
    if not member:
        return jsonify({"error": "Participante não encontrado."}), 404
    payload = request.get_json(force=True)
    for key in ["name", "phone", "photo", "active"]:
        if key in payload:
            member[key] = payload[key]
    save_db(data)
    return jsonify(dashboard(data))


@app.post("/api/expenses")
def create_expense():
    data = load_db()
    payload = request.get_json(force=True)
    name = norm(payload.get("name"))
    participants = payload.get("participants") or []
    if not name:
        return jsonify({"error": "Nome da despesa é obrigatório."}), 400
    if not participants:
        return jsonify({"error": "Selecione pelo menos um participante."}), 400
    expense = {
        "id": new_id(),
        "group_id": payload.get("group_id") or data["groups"][0]["id"],
        "name": name,
        "category": payload.get("category") or "Outros",
        "amount": as_float(money(payload.get("amount"))),
        "date": payload.get("date") or date.today().isoformat(),
        "due_date": payload.get("due_date") or "",
        "paid_by": payload.get("paid_by") or "",
        "participants": participants,
        "split_mode": payload.get("split_mode") or "equal",
        "weights": payload.get("weights") or {},
        "manual_shares": payload.get("manual_shares") or {},
        "status": payload.get("status") or "open",
        "recurring": bool(payload.get("recurring", False)),
        "receipt": payload.get("receipt") or "",
        "notes": norm(payload.get("notes")),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    data["expenses"].append(expense)
    remember(data, "templates", name)
    remember(data, "categories", expense["category"])
    save_db(data)
    return jsonify(dashboard(data))


@app.patch("/api/expenses/<expense_id>")
def update_expense(expense_id):
    data = load_db()
    expense = next((e for e in data["expenses"] if e["id"] == expense_id), None)
    if not expense:
        return jsonify({"error": "Despesa não encontrada."}), 404
    payload = request.get_json(force=True)
    for key in ["group_id", "name", "category", "date", "due_date", "paid_by", "split_mode", "status", "notes", "receipt"]:
        if key in payload:
            expense[key] = payload[key]
    for key in ["participants", "weights", "manual_shares"]:
        if key in payload:
            expense[key] = payload[key]
    if "amount" in payload:
        expense["amount"] = as_float(money(payload["amount"]))
    if "recurring" in payload:
        expense["recurring"] = bool(payload["recurring"])
    expense["updated_at"] = now_iso()
    remember(data, "templates", expense.get("name"))
    remember(data, "categories", expense.get("category"))
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
    data = dashboard(load_db(), request.args)
    members = data["members"]
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["Data", "Grupo", "Categoria", "Despesa", "Valor", "Status", *[m["name"] for m in members], "Observações"])
    groups = by_id(data["groups"])
    for expense in data["expenses"]:
        writer.writerow([
            expense.get("date", ""),
            groups.get(expense.get("group_id"), {}).get("name", ""),
            expense.get("category", ""),
            expense.get("name", ""),
            f'{expense["amount"]:.2f}'.replace(".", ","),
            expense.get("status", ""),
            *[f'{expense["shares"].get(m["id"], 0):.2f}'.replace(".", ",") for m in members],
            expense.get("notes", ""),
        ])
    return Response(out.getvalue(), mimetype="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=dividebem.csv"})


if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8798")), debug=True)
