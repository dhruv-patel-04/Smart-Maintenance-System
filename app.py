import os
from datetime import datetime
from types import SimpleNamespace

import joblib
import pandas as pd
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, url_for
from supabase import Client, create_client

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(BASE_DIR, "smart_maintenance_model.pkl")
LABEL_ENCODER_PATH = os.path.join(BASE_DIR, "label_encoder.pkl")

load_dotenv()

app = Flask(__name__)
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or os.getenv("SUPABASE_ANON_KEY")
    or os.getenv("SUPABASE_KEY", "")
).strip().strip('"').strip("'")
SUPABASE_PUBLIC_SCHEMA = os.getenv("SUPABASE_PUBLIC_SCHEMA", "public")
SUPABASE_ANALYTICS_SCHEMA = os.getenv("SUPABASE_ANALYTICS_SCHEMA", "analytics")
CHART_RANGE_OPTIONS = {
    "1h": ("Last 1 Hour", 60),
    "6h": ("Last 6 Hours", 6 * 60),
    "24h": ("Last 24 Hours", 24 * 60),
    "7d": ("Last 7 Days", 7 * 24 * 60),
    "30d": ("Last 30 Days", 30 * 24 * 60),
}


def get_supabase_client() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError(
            "Missing SUPABASE_URL and Supabase API key. Set SUPABASE_SERVICE_ROLE_KEY "
            "(recommended) or SUPABASE_ANON_KEY in .env."
        )
    if SUPABASE_KEY.startswith("sb_publishable_"):
        raise RuntimeError(
            "SUPABASE key is publishable (sb_publishable_...). Use service role or anon JWT key "
            "from Supabase Settings > API instead."
        )
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def parse_timestamp(value: str | None) -> datetime:
    if not value:
        return datetime.utcnow()
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def row_to_view_model(row: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id=row.get("id"),
        machine_id=row.get("machine_id", ""),
        timestamp=parse_timestamp(row.get("event_ts")),
        temp_c=float(row.get("temp_c", 0.0)),
        current_a=float(row.get("current_a", 0.0)),
        vibration_rms_g=float(row.get("vibration_rms_g", 0.0)),
        load_level=row.get("load_level", ""),
        predicted_status=row.get("predicted_status", ""),
        confidence=float(row.get("confidence", 0.0)),
        alert_flag=bool(row.get("alert_flag", False)),
        maintenance_action=row.get("maintenance_action", ""),
    )


def count_predictions(client: Client, status: str | None = None) -> int:
    query = (
        client.schema(SUPABASE_ANALYTICS_SCHEMA)
        .table("sensor_predictions")
        .select("id", count="exact")
    )
    if status:
        query = query.eq("predicted_status", status)
    response = query.execute()
    return int(response.count or 0)


def fetch_prediction_rows(limit: int | None = None) -> list[dict]:
    client = get_supabase_client()
    query = (
        client.schema(SUPABASE_ANALYTICS_SCHEMA)
        .table("sensor_predictions")
        .select("*")
        .order("event_ts", desc=True)
    )
    if limit:
        query = query.limit(limit)
    response = query.execute()
    return response.data or []


def fetch_prediction_rows_since(minutes: int, limit: int = 1000) -> list[dict]:
    client = get_supabase_client()
    cutoff = datetime.utcnow().timestamp() - (minutes * 60)
    cutoff_iso = datetime.utcfromtimestamp(cutoff).isoformat()
    response = (
        client.schema(SUPABASE_ANALYTICS_SCHEMA)
        .table("sensor_predictions")
        .select("*")
        .gte("event_ts", cutoff_iso)
        .order("event_ts", desc=False)
        .limit(limit)
        .execute()
    )
    return response.data or []


def fetch_prediction_page(limit: int, offset: int) -> list[dict]:
    client = get_supabase_client()
    response = (
        client.schema(SUPABASE_ANALYTICS_SCHEMA)
        .table("sensor_predictions")
        .select("*")
        .order("event_ts", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    return response.data or []


def fetch_device_configs() -> list[dict]:
    client = get_supabase_client()
    response = (
        client.schema(SUPABASE_PUBLIC_SCHEMA)
        .table("device_config")
        .select("*")
        .order("device_id")
        .execute()
    )
    return response.data or []


def row_to_config_view_model(row: dict) -> SimpleNamespace:
    return SimpleNamespace(
        device_id=row.get("device_id", ""),
        calibration=float(row.get("calibration", 0.0)),
        turns=float(row.get("turns", 0.0)),
        alpha=float(row.get("alpha", 0.0)),
    )


def upsert_device_config(device_id: str, calibration: float, turns: float, alpha: float) -> None:
    client = get_supabase_client()
    payload = {
        "device_id": device_id,
        "calibration": calibration,
        "turns": turns,
        "alpha": alpha,
    }
    (
        client.schema(SUPABASE_PUBLIC_SCHEMA)
        .table("device_config")
        .upsert(payload, on_conflict="device_id")
        .execute()
    )


def delete_device_config(device_id: str) -> None:
    client = get_supabase_client()
    (
        client.schema(SUPABASE_PUBLIC_SCHEMA)
        .table("device_config")
        .delete()
        .eq("device_id", device_id)
        .execute()
    )


def to_dashboard_payload() -> dict:
    client = get_supabase_client()

    dashboard_rows = fetch_prediction_rows(limit=30)
    latest_vm = row_to_view_model(dashboard_rows[0]) if dashboard_rows else None
    recent_vm = [row_to_view_model(r) for r in dashboard_rows]
    chart_rows = [row_to_view_model(r) for r in reversed(dashboard_rows)]

    latest = None
    if latest_vm:
        latest = {
            "machine_id": latest_vm.machine_id,
            "timestamp": latest_vm.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "temp_c": latest_vm.temp_c,
            "current_a": latest_vm.current_a,
            "vibration_rms_g": latest_vm.vibration_rms_g,
            "load_level": latest_vm.load_level,
            "predicted_status": latest_vm.predicted_status,
            "confidence": latest_vm.confidence,
            "maintenance_action": latest_vm.maintenance_action,
        }

    recent = [
        {
            "timestamp": r.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "machine_id": r.machine_id,
            "temp_c": r.temp_c,
            "current_a": r.current_a,
            "vibration_rms_g": r.vibration_rms_g,
            "load_level": r.load_level,
            "predicted_status": r.predicted_status,
            "confidence": r.confidence,
        }
        for r in recent_vm
    ]

    chart_data = {
        "labels": [r.timestamp.strftime("%H:%M:%S") for r in chart_rows],
        "temp": [r.temp_c for r in chart_rows],
        "current": [r.current_a for r in chart_rows],
        "vibration": [r.vibration_rms_g for r in chart_rows],
    }

    counts = {
        "total": count_predictions(client),
        "normal_count": count_predictions(client, "Normal"),
        "warning_count": count_predictions(client, "Warning"),
        "failure_count": count_predictions(client, "Failure"),
    }

    return {
        "latest": latest,
        "recent": recent,
        "chart_data": chart_data,
        "counts": counts,
    }


def build_chart_payload(range_key: str) -> dict:
    normalized_range = range_key if range_key in CHART_RANGE_OPTIONS else "24h"
    _, minutes = CHART_RANGE_OPTIONS[normalized_range]
    rows = [row_to_view_model(r) for r in fetch_prediction_rows_since(minutes)]

    return {
        "range": normalized_range,
        "range_options": [
            {"value": key, "label": label}
            for key, (label, _) in CHART_RANGE_OPTIONS.items()
        ],
        "labels": [r.timestamp.strftime("%Y-%m-%d %H:%M:%S") for r in rows],
        "temp": [r.temp_c for r in rows],
        "current": [r.current_a for r in rows],
        "vibration": [r.vibration_rms_g for r in rows],
    }


def load_model_artifacts():
    if not os.path.exists(MODEL_PATH) or not os.path.exists(LABEL_ENCODER_PATH):
        raise FileNotFoundError(
            "Model files not found. Run 'python train_model.py' first."
        )
    model = joblib.load(MODEL_PATH)
    label_encoder = joblib.load(LABEL_ENCODER_PATH)
    return model, label_encoder


MODEL, LABEL_ENCODER = load_model_artifacts()


def recommend_action(status: str) -> str:
    if status == "Failure":
        return "Immediate shutdown and urgent inspection required"
    if status == "Warning":
        return "Schedule maintenance inspection soon"
    return "Continue operation and monitor routinely"


def hybrid_override(temp_c: float, current_a: float, vibration_rms_g: float):
    # Rule-based safety override
    if temp_c > 90 or vibration_rms_g > 0.06 or current_a > 13:
        return "Failure", 0.99
    if temp_c > 78 or vibration_rms_g > 0.028 or current_a > 9.5:
        return "Warning", 0.95
    return None, None


def predict_status(temp_c: float, current_a: float, vibration_rms_g: float, load_level: str):
    override_status, override_conf = hybrid_override(temp_c, current_a, vibration_rms_g)
    if override_status:
        return override_status, override_conf

    input_df = pd.DataFrame(
        [{
            "Temp_C": temp_c,
            "Current_A": current_a,
            "Vibration_RMS_g": vibration_rms_g,
            "Load_Level": load_level,
        }]
    )

    pred_encoded = MODEL.predict(input_df)[0]
    probabilities = MODEL.predict_proba(input_df)[0]
    confidence = float(max(probabilities))
    pred_label = str(LABEL_ENCODER.inverse_transform([pred_encoded])[0])
    return pred_label, confidence


@app.route("/")
def dashboard():
    payload = to_dashboard_payload()

    latest = None
    if payload["latest"]:
        latest = SimpleNamespace(**payload["latest"])

    recent = [SimpleNamespace(**r) for r in payload["recent"]]
    counts = payload["counts"]

    return render_template(
        "index.html",
        latest=latest,
        recent=recent,
        total=counts["total"],
        normal_count=counts["normal_count"],
        warning_count=counts["warning_count"],
        failure_count=counts["failure_count"],
        chart_data=payload["chart_data"],
    )


@app.route("/history")
def history():
    page_size = 50
    page = request.args.get("page", default=1, type=int)
    page = max(page, 1)
    total_records = count_predictions(get_supabase_client())
    total_pages = max((total_records + page_size - 1) // page_size, 1)
    if page > total_pages:
        page = total_pages

    offset = (page - 1) * page_size
    records = [row_to_view_model(r) for r in fetch_prediction_page(page_size, offset)]
    start_record = offset + 1 if total_records else 0
    end_record = offset + len(records)

    page_window_start = max(1, page - 2)
    page_window_end = min(total_pages, page + 2)
    if page_window_end - page_window_start < 4:
        if page_window_start == 1:
            page_window_end = min(total_pages, page_window_start + 4)
        elif page_window_end == total_pages:
            page_window_start = max(1, total_pages - 4)

    page_numbers = list(range(page_window_start, page_window_end + 1))

    return render_template(
        "history.html",
        records=records,
        page=page,
        total_pages=total_pages,
        page_size=page_size,
        total_records=total_records,
        start_record=start_record,
        end_record=end_record,
        page_numbers=page_numbers,
    )


@app.route("/charts")
def charts():
    range_key = request.args.get("range", "24h")
    chart_payload = build_chart_payload(range_key)
    return render_template(
        "charts.html",
        chart_payload=chart_payload,
    )


@app.route("/settings")
def settings():
    message = request.args.get("message", "")
    error = request.args.get("error", "")
    configs = [row_to_config_view_model(r) for r in fetch_device_configs()]
    return render_template(
        "settings.html",
        configs=configs,
        message=message,
        error=error,
    )


@app.route("/settings/save", methods=["POST"])
def save_settings():
    form = request.form
    device_id = str(form.get("device_id", "")).strip()

    try:
        calibration = float(form.get("calibration", ""))
        turns = float(form.get("turns", ""))
        alpha = float(form.get("alpha", ""))
    except ValueError:
        return redirect(
            url_for("settings", error="Calibration, turns, and alpha must be valid numbers.")
        )

    if not device_id:
        return redirect(url_for("settings", error="Device ID is required."))

    try:
        upsert_device_config(device_id, calibration, turns, alpha)
    except Exception as exc:
        return redirect(url_for("settings", error=f"Failed to save settings: {exc}"))

    return redirect(
        url_for("settings", message=f"Configuration saved for {device_id}.")
    )


@app.route("/settings/delete", methods=["POST"])
def delete_settings():
    device_id = str(request.form.get("device_id", "")).strip()
    if not device_id:
        return redirect(url_for("settings", error="Device ID is required for delete."))

    try:
        delete_device_config(device_id)
    except Exception as exc:
        return redirect(url_for("settings", error=f"Failed to delete configuration: {exc}"))

    return redirect(
        url_for("settings", message=f"Configuration deleted for {device_id}.")
    )


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "message": "Smart Maintenance API is running"})


@app.route("/api/dashboard-data")
def api_dashboard_data():
    return jsonify(to_dashboard_payload())


@app.route("/api/chart-data")
def api_chart_data():
    range_key = request.args.get("range", "24h")
    return jsonify(build_chart_payload(range_key))


@app.route("/api/predict", methods=["POST"])
def api_predict():
    data = request.get_json(force=True)
    try:
        temp_c = float(data["temp_c"])
        current_a = float(data["current_a"])
        vibration_rms_g = float(data["vibration_rms_g"])
        load_level = str(data["load_level"]).title()
    except (KeyError, ValueError, TypeError) as exc:
        return jsonify({"error": f"Invalid input: {exc}"}), 400

    predicted_status, confidence = predict_status(
        temp_c, current_a, vibration_rms_g, load_level
    )
    action = recommend_action(predicted_status)

    return jsonify(
        {
            "predicted_status": predicted_status,
            "confidence": round(confidence, 4),
            "maintenance_action": action,
        }
    )


@app.route("/api/ingest", methods=["POST"])
def api_ingest():
    return jsonify(
        {
            "message": (
                "Direct ingest is disabled in Flask mode. Send data from ESP32/simulator "
                "to Supabase iot.raw_sensor_readings and run ml_worker.py for predictions."
            )
        }
    ), 410


@app.route("/seed-demo")
def seed_demo():
    return redirect(url_for("dashboard"))


if __name__ == "__main__":
    app.run(debug=True)
