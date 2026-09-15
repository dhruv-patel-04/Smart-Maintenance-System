import io
import os
from datetime import datetime, date, timedelta
from functools import wraps
from zoneinfo import ZoneInfo
from types import SimpleNamespace

import joblib
import pandas as pd
from dotenv import load_dotenv
from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
    send_file,
    flash,
    session,
)
from report_generator import build_report_data, generate_pdf
from supabase import Client, create_client

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(BASE_DIR, "smart_maintenance_model.pkl")
LABEL_ENCODER_PATH = os.path.join(BASE_DIR, "label_encoder.pkl")

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False,    #Make This TRUE during deployment
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("login"))
        
        return f(*args, **kwargs)

    return decorated_function

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or os.getenv("SUPABASE_ANON_KEY")
    or os.getenv("SUPABASE_KEY", "")
).strip().strip('"').strip("'")
SUPABASE_PUBLIC_SCHEMA = os.getenv("SUPABASE_PUBLIC_SCHEMA", "public")
SUPABASE_ANALYTICS_SCHEMA = os.getenv("SUPABASE_ANALYTICS_SCHEMA", "analytics")
REPORT_TYPE_PERFORMANCE = "performance"
REPORT_STORAGE_BUCKET = "maintenance-reports"
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


def get_report_machines() -> list[str]:
    """
    Return machines that have prediction data.
    """
    client = get_supabase_client()

    response = (
        client.schema(SUPABASE_ANALYTICS_SCHEMA)
        .table("sensor_predictions")
        .select("machine_id")
        .order("machine_id")
        .execute()
    )

    machines = sorted(
        {
            str(row.get("machine_id"))
            for row in (response.data or [])
            if row.get("machine_id")
        }
    )

    return machines


def fetch_existing_report(
    machine_id: str,
    period_start: date,
    period_end: date,
) -> dict | None:
    client = get_supabase_client()

    response = (
        client.schema(SUPABASE_ANALYTICS_SCHEMA)
        .table("generated_reports")
        .select("*")
        .eq("machine_id", machine_id)
        .eq("report_type", REPORT_TYPE_PERFORMANCE)
        .eq("period_start", period_start.isoformat())
        .eq("period_end", period_end.isoformat())
        .order("id", desc=True)
        .limit(1)
        .execute()
    )

    rows = response.data or []

    return rows[0] if rows else None


def fetch_report_prediction_rows(
    machine_id: str,
    period_start: date,
    period_end: date,
) -> list[dict]:
    client = get_supabase_client()

    start_iso = f"{period_start.isoformat()}T00:00:00"
    end_exclusive = period_end + timedelta(days=1)
    end_iso = f"{end_exclusive.isoformat()}T00:00:00"

    response = (
        client.schema(SUPABASE_ANALYTICS_SCHEMA)
        .table("sensor_predictions")
        .select(
            "event_ts,temp_c,current_a,vibration_rms_g,"
            "predicted_status,confidence"
        )
        .eq("machine_id", machine_id)
        .gte("event_ts", start_iso)
        .lt("event_ts", end_iso)
        .order("event_ts", desc=False)
        .execute()
    )

    return response.data or []


def save_generated_report(
    machine_id: str,
    period_start: date,
    period_end: date,
    report_data: dict,
    file_path: str,
) -> dict:
    client = get_supabase_client()

    payload = {
        "machine_id": machine_id,
        "report_type": REPORT_TYPE_PERFORMANCE,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),

        "generated_at": datetime.now().isoformat(),

        "total_readings": report_data["total_readings"],

        "normal_count": report_data["status_counts"]["normal"],
        "warning_count": report_data["status_counts"]["warning"],
        "failure_count": report_data["status_counts"]["failure"],

        "normal_percentage": report_data["status_percentages"]["normal"],
        "warning_percentage": report_data["status_percentages"]["warning"],
        "failure_percentage": report_data["status_percentages"]["failure"],

        "temperature_avg": report_data["temperature"]["average"],
        "temperature_min": report_data["temperature"]["minimum"],
        "temperature_max": report_data["temperature"]["maximum"],

        "current_avg": report_data["current"]["average"],
        "current_min": report_data["current"]["minimum"],
        "current_max": report_data["current"]["maximum"],

        "vibration_avg": report_data["vibration"]["average"],
        "vibration_min": report_data["vibration"]["minimum"],
        "vibration_max": report_data["vibration"]["maximum"],

        "overall_status": report_data["overall_status"],
        "summary": report_data["summary"],

        "report_data": report_data,

        "file_path": file_path,
    }

    response = (
        client.schema(SUPABASE_ANALYTICS_SCHEMA)
        .table("generated_reports")
        .insert(payload)
        .execute()
    )

    rows = response.data or []

    if not rows:
        raise RuntimeError("Report was not saved to Supabase.")

    return rows[0]


def create_report(
    machine_id: str,
    period_start: date,
    period_end: date,
) -> dict:
    rows = fetch_report_prediction_rows(
        machine_id,
        period_start,
        period_end,
    )

    if not rows:
        raise RuntimeError(
            "No prediction data was found for the selected machine and period."
        )

    report_data = build_report_data(
        rows,
        machine_id,
        period_start,
        period_end,
    )

    pdf_bytes = generate_pdf(report_data)

    file_path = upload_report_pdf(
        machine_id,
        period_start,
        period_end,
        pdf_bytes,
    )

    return {
        "report_data": report_data,
        "file_path": file_path,
    }


def upload_report_pdf(
    machine_id: str,
    period_start: date,
    period_end: date,
    pdf_bytes: bytes,
) -> str:
    client = get_supabase_client()

    safe_machine_id = "".join(
        character
        if character.isalnum() or character in "-_"
        else "_"
        for character in machine_id
    )

    generated_timestamp = datetime.now(ZoneInfo("Asia/Kolkata")).strftime(
        "%Y-%m-%d_%H-%M-%S"
    )

    file_path = (
        f"{safe_machine_id}/"
        f"{period_start.isoformat()}_"
        f"{period_end.isoformat()}_"
        f"generated_{generated_timestamp}.pdf"
    )

    client.storage.from_(REPORT_STORAGE_BUCKET).upload(
        file_path,
        pdf_bytes,
        {
            "content-type": "application/pdf",
            "upsert": "false",
        },
    )

    return file_path


def fetch_generated_reports(machine_id: str | None = None) -> list[dict]:
    client = get_supabase_client()

    query = (
        client.schema(SUPABASE_ANALYTICS_SCHEMA)
        .table("generated_reports")
        .select(
            "id,machine_id,report_type,period_start,period_end,"
            "generated_at,overall_status,total_readings,file_path"
        )
        .eq("report_type", REPORT_TYPE_PERFORMANCE)
        .order("period_start", desc=True)
    )

    if machine_id:
        query = query.eq("machine_id", machine_id)

    response = query.execute()

    return response.data or []




# =========================================================
# AUTHENTICATION
# =========================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if session.get("user"):
            return redirect(url_for("dashboard"))

        return render_template("login.html")

    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")

    if not email or not password:
        return render_template(
            "login.html",
            error="Email and password are required.",
            email=email,
        )

    try:
        client = get_supabase_client()

        response = client.auth.sign_in_with_password(
            {
                "email": email,
                "password": password,
            }
        )

        if not response.user or not response.session:
            return render_template(
                "login.html",
                error="Invalid email or password.",
                email=email,
            )

        session["user"] = {
            "id": response.user.id,
            "email": response.user.email,
        }

        session["access_token"] = response.session.access_token
        session["refresh_token"] = response.session.refresh_token
        session.permanent = True

        return redirect(url_for("dashboard"))

    except Exception as exc:
        app.logger.exception("Login failed")

        return render_template(
            "login.html",
            error="Invalid email or password.",
            email=email,
        )


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "GET":
        if session.get("user"):
            return redirect(url_for("dashboard"))

        return render_template("signup.html")

    full_name = request.form.get("full_name", "").strip()
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")

    if not full_name or not email or not password:
        return render_template(
            "signup.html",
            error="All fields are required.",
            full_name=full_name,
            email=email,
        )

    if password != confirm_password:
        return render_template(
            "signup.html",
            error="Passwords do not match.",
            full_name=full_name,
            email=email,
        )

    if len(password) < 6:
        return render_template(
            "signup.html",
            error="Password must be at least 6 characters.",
            full_name=full_name,
            email=email,
        )

    try:
        client = get_supabase_client()

        response = client.auth.sign_up(
            {
                "email": email,
                "password": password,
                "options": {
                    "data": {
                        "full_name": full_name,
                    }
                },
            }
        )

        if not response.user:
            return render_template(
                "signup.html",
                error="Unable to create account.",
                full_name=full_name,
                email=email,
            )

        # If email confirmation is disabled, Supabase may return
        # an active session immediately.
        if response.session:
            session["user"] = {
                "id": response.user.id,
                "email": response.user.email,
            }

            session["access_token"] = response.session.access_token
            session["refresh_token"] = response.session.refresh_token
            session.permanent = True

            return redirect(url_for("dashboard"))

        # If email confirmation is enabled, send the user to login.
        return render_template(
            "login.html",
            message="Account created successfully. Please check your email to confirm your account, then log in.",
            email=email,
        )

    except Exception as exc:
        app.logger.exception("Signup failed")

        return render_template(
            "signup.html",
            error="Unable to create account. Please try again.",
            full_name=full_name,
            email=email,
        )


@app.route("/logout")
def logout():
    try:
        client = get_supabase_client()
        client.auth.sign_out()
    except Exception:
        pass

    session.clear()

    return redirect(url_for("login"))



@app.route("/")
@login_required
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
@login_required
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
@login_required
def charts():
    range_key = request.args.get("range", "24h")
    chart_payload = build_chart_payload(range_key)
    return render_template(
        "charts.html",
        chart_payload=chart_payload,
    )


@app.route("/reports")
@login_required
def reports():
    machine_id = request.args.get("machine_id", "").strip()

    machines = get_report_machines()

    selected_machine = machine_id if machine_id in machines else (
        machines[0] if machines else ""
    )

    generated_reports = fetch_generated_reports(
        selected_machine if selected_machine else None
    )

    return render_template(
        "reports.html",
        machines=machines,
        selected_machine=selected_machine,
        generated_reports=generated_reports,
        today=date.today().isoformat(),
    )


@app.route("/reports/generate", methods=["POST"])
@login_required
def generate_report():
    machine_id = request.form.get("machine_id", "").strip()
    start_value = request.form.get("period_start", "").strip()
    end_value = request.form.get("period_end", "").strip()

    if not machine_id or not start_value or not end_value:
        return redirect(
            url_for(
                "reports",
                machine_id=machine_id,
            )
        )

    try:
        period_start = date.fromisoformat(start_value)
        period_end = date.fromisoformat(end_value)
    except ValueError:
        return redirect(
            url_for(
                "reports",
                machine_id=machine_id,
                error="Invalid date range.",
            )
        )

    if period_end < period_start:
        return redirect(
            url_for(
                "reports",
                machine_id=machine_id,
                error="End date must be on or after start date.",
            )
        )

    try:
        # -----------------------------------------
        # 1. Check if report already exists
        # -----------------------------------------
        existing_report = fetch_existing_report(
            machine_id,
            period_start,
            period_end,
        )

        if existing_report:
            return render_template(
                "report_exists.html",
                existing_report=existing_report,
                machine_id=machine_id,
                period_start=period_start,
                period_end=period_end,
            )

        # -----------------------------------------
        # 2. Fetch prediction data
        # -----------------------------------------
        rows = fetch_report_prediction_rows(
            machine_id,
            period_start,
            period_end,
        )

        if not rows:
            return redirect(
                url_for(
                    "reports",
                    machine_id=machine_id,
                    error=(
                        "No prediction data was found for the selected "
                        "machine and period."
                    ),
                )
            )

        # -----------------------------------------
        # 3. Build structured report
        # -----------------------------------------
        report_data = build_report_data(
            rows,
            machine_id,
            period_start,
            period_end,
        )

        # -----------------------------------------
        # 4. Generate PDF
        # -----------------------------------------
        pdf_bytes = generate_pdf(report_data)

        # -----------------------------------------
        # 5. Upload PDF
        # -----------------------------------------
        file_path = upload_report_pdf(
            machine_id,
            period_start,
            period_end,
            pdf_bytes,
        )

        # -----------------------------------------
        # 6. Save metadata + structured data
        # -----------------------------------------
        saved_report = save_generated_report(
            machine_id,
            period_start,
            period_end,
            report_data,
            file_path,
        )

        return redirect(
            url_for(
                "report_preview",
                report_id=saved_report["id"],
            )
        )

    except Exception as exc:
        app.logger.exception("Report generation failed")

        return redirect(
            url_for(
                "reports",
                machine_id=machine_id,
                error=f"Failed to generate report: {exc}",
            )
        )


@app.route("/reports/generate-new", methods=["POST"])
@login_required
def generate_new_report():
    machine_id = request.form.get("machine_id", "").strip()
    start_value = request.form.get("period_start", "").strip()
    end_value = request.form.get("period_end", "").strip()

    try:
        period_start = date.fromisoformat(start_value)
        period_end = date.fromisoformat(end_value)
    except ValueError:
        return redirect(
            url_for(
                "reports",
                machine_id=machine_id,
                error="Invalid date range.",
            )
        )

    if period_end < period_start:
        return redirect(
            url_for(
                "reports",
                machine_id=machine_id,
                error="End date must be on or after start date.",
            )
        )

    try:
        result = create_report(
            machine_id,
            period_start,
            period_end,
        )

        saved_report = save_generated_report(
            machine_id,
            period_start,
            period_end,
            result["report_data"],
            result["file_path"],
        )

        return redirect(
            url_for(
                "report_preview",
                report_id=saved_report["id"],
            )
        )

    except Exception as exc:
        app.logger.exception(
            "New report generation failed"
        )

        return redirect(
            url_for(
                "reports",
                machine_id=machine_id,
                error=f"Failed to generate report: {exc}",
            )
        )


@app.route("/reports/<int:report_id>")
@login_required
def report_preview(report_id):
    client = get_supabase_client()

    response = (
        client.schema(SUPABASE_ANALYTICS_SCHEMA)
        .table("generated_reports")
        .select("*")
        .eq("id", report_id)
        .limit(1)
        .execute()
    )

    rows = response.data or []

    if not rows:
        return redirect(
            url_for(
                "reports",
                error="Report not found.",
            )
        )

    report = rows[0]

    return render_template(
        "report_preview.html",
        report=report,
        report_data=report["report_data"],
    )


@app.route(
    "/reports/<int:report_id>/update",
    methods=["POST"]
)
@login_required
def update_existing_report(report_id):
    client = get_supabase_client()

    existing_response = (
        client
        .schema(SUPABASE_ANALYTICS_SCHEMA)
        .table("generated_reports")
        .select("*")
        .eq("id", report_id)
        .eq("report_type", "performance")
        .single()
        .execute()
    )

    existing_report = existing_response.data

    if not existing_report:
        return redirect(
            url_for(
                "reports",
                error="Report not found.",
            )
        )

    machine_id = existing_report["machine_id"]
    period_start = date.fromisoformat(
        existing_report["period_start"]
    )
    period_end = date.fromisoformat(
        existing_report["period_end"]
    )

    try:
        result = create_report(
            machine_id,
            period_start,
            period_end,
        )

        report_data = result["report_data"]

        update_payload = {
            "generated_at": datetime.now().isoformat(),

            "total_readings": report_data["total_readings"],

            "normal_count": report_data["status_counts"]["normal"],
            "warning_count": report_data["status_counts"]["warning"],
            "failure_count": report_data["status_counts"]["failure"],

            "normal_percentage": (
                report_data["status_percentages"]["normal"]
            ),
            "warning_percentage": (
                report_data["status_percentages"]["warning"]
            ),
            "failure_percentage": (
                report_data["status_percentages"]["failure"]
            ),

            "temperature_avg": (
                report_data["temperature"]["average"]
            ),
            "temperature_min": (
                report_data["temperature"]["minimum"]
            ),
            "temperature_max": (
                report_data["temperature"]["maximum"]
            ),

            "current_avg": (
                report_data["current"]["average"]
            ),
            "current_min": (
                report_data["current"]["minimum"]
            ),
            "current_max": (
                report_data["current"]["maximum"]
            ),

            "vibration_avg": (
                report_data["vibration"]["average"]
            ),
            "vibration_min": (
                report_data["vibration"]["minimum"]
            ),
            "vibration_max": (
                report_data["vibration"]["maximum"]
            ),

            "overall_status": report_data["overall_status"],
            "summary": report_data["summary"],
            "report_data": report_data,

            "file_path": result["file_path"],
        }

        (
            client
            .schema(SUPABASE_ANALYTICS_SCHEMA)
            .table("generated_reports")
            .update(update_payload)
            .eq("id", report_id)
            .execute()
        )

        return redirect(
            url_for(
                "report_preview",
                report_id=report_id,
            )
        )

    except Exception as exc:
        app.logger.exception(
            "Existing report update failed"
        )

        return redirect(
            url_for(
                "report_preview",
                report_id=report_id,
                error=f"Failed to update report: {exc}",
            )
        )


@app.route("/reports/<int:report_id>/download")
@login_required
def download_report(report_id):
    client = get_supabase_client()

    response = (
        client.schema(SUPABASE_ANALYTICS_SCHEMA)
        .table("generated_reports")
        .select("machine_id,period_start,period_end,file_path")
        .eq("id", report_id)
        .limit(1)
        .execute()
    )

    rows = response.data or []

    if not rows:
        return redirect(
            url_for(
                "reports",
                error="Report not found.",
            )
        )

    report = rows[0]

    file_path = report.get("file_path")

    if not file_path:
        return redirect(
            url_for(
                "reports",
                error="PDF file is not available for this report.",
            )
        )

    try:
        pdf_bytes = (
            client.storage
            .from_(REPORT_STORAGE_BUCKET)
            .download(file_path)
        )

        filename = (
            f"{report['machine_id']}_"
            f"performance_"
            f"{report['period_start']}_"
            f"{report['period_end']}.pdf"
        )

        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename,
        )

    except Exception as exc:
        app.logger.exception("Report download failed")

        return redirect(
            url_for(
                "reports",
                error=f"Failed to download report: {exc}",
            )
        )


@app.route("/settings")
@login_required
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
@login_required
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
@login_required
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
@login_required
def api_dashboard_data():
    return jsonify(to_dashboard_payload())


@app.route("/api/chart-data")
@login_required
def api_chart_data():
    range_key = request.args.get("range", "24h")
    return jsonify(build_chart_payload(range_key))


@app.route("/api/predict", methods=["POST"])
@login_required
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
@login_required
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
@login_required
def seed_demo():
    return redirect(url_for("dashboard"))


if __name__ == "__main__":
    app.run(debug=True)
