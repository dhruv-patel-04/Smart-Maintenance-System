import os
import time
from datetime import datetime, timezone

import joblib
import pandas as pd
from dotenv import load_dotenv
from supabase import Client, create_client

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(BASE_DIR, "smart_maintenance_model.pkl")
LABEL_ENCODER_PATH = os.path.join(BASE_DIR, "label_encoder.pkl")

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or os.getenv("SUPABASE_ANON_KEY")
    or os.getenv("SUPABASE_KEY", "")
).strip().strip('"').strip("'")
SUPABASE_IOT_SCHEMA = os.getenv("SUPABASE_IOT_SCHEMA", "iot")
SUPABASE_ANALYTICS_SCHEMA = os.getenv("SUPABASE_ANALYTICS_SCHEMA", "analytics")
POLL_SECONDS = int(os.getenv("WORKER_POLL_SECONDS", "2"))
BATCH_SIZE = int(os.getenv("WORKER_BATCH_SIZE", "25"))
MODEL_VERSION = os.getenv("MODEL_VERSION", "rf_v1")


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


def load_model_artifacts():
    if not os.path.exists(MODEL_PATH) or not os.path.exists(LABEL_ENCODER_PATH):
        raise FileNotFoundError(
            "Model files not found. Run 'python train_model.py' first."
        )
    model = joblib.load(MODEL_PATH)
    label_encoder = joblib.load(LABEL_ENCODER_PATH)
    return model, label_encoder


def recommend_action(status: str) -> str:
    if status == "Failure":
        return "Immediate shutdown and urgent inspection required"
    if status == "Warning":
        return "Schedule maintenance inspection soon"
    return "Continue operation and monitor routinely"


def hybrid_override(temp_c: float, current_a: float, vibration_rms_g: float):
    if temp_c > 90 or vibration_rms_g > 0.06 or current_a > 13:
        return "Failure", 0.99
    if temp_c > 78 or vibration_rms_g > 0.028 or current_a > 9.5:
        return "Warning", 0.95
    return None, None


def predict_status(model, label_encoder, temp_c: float, current_a: float, vibration_rms_g: float, load_level: str):
    override_status, override_conf = hybrid_override(temp_c, current_a, vibration_rms_g)
    if override_status:
        return override_status, override_conf

    input_df = pd.DataFrame(
        [
            {
                "Temp_C": temp_c,
                "Current_A": current_a,
                "Vibration_RMS_g": vibration_rms_g,
                "Load_Level": load_level,
            }
        ]
    )

    pred_encoded = model.predict(input_df)[0]
    probabilities = model.predict_proba(input_df)[0]
    confidence = float(max(probabilities))
    pred_label = str(label_encoder.inverse_transform([pred_encoded])[0])
    return pred_label, confidence


def fetch_unprocessed_rows(client: Client) -> list[dict]:
    response = (
        client.schema(SUPABASE_IOT_SCHEMA)
        .table("raw_sensor_readings")
        .select("id,machine_id,event_ts,temp_c,current_a,vibration_rms_g,load_level")
        .eq("processed", False)
        .order("id")
        .limit(BATCH_SIZE)
        .execute()
    )
    return response.data or []


def mark_processed(client: Client, raw_id: int) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    (
        client.schema(SUPABASE_IOT_SCHEMA)
        .table("raw_sensor_readings")
        .update({"processed": True, "processed_at": now_iso})
        .eq("id", raw_id)
        .execute()
    )


def insert_prediction(client: Client, row: dict, predicted_status: str, confidence: float) -> None:
    payload = {
        "raw_reading_id": row["id"],
        "machine_id": row["machine_id"],
        "event_ts": row["event_ts"],
        "temp_c": row["temp_c"],
        "current_a": row["current_a"],
        "vibration_rms_g": row["vibration_rms_g"],
        "load_level": row["load_level"],
        "predicted_status": predicted_status,
        "confidence": confidence,
        "alert_flag": predicted_status in {"Warning", "Failure"},
        "maintenance_action": recommend_action(predicted_status),
        "model_version": MODEL_VERSION,
    }
    (
        client.schema(SUPABASE_ANALYTICS_SCHEMA)
        .table("sensor_predictions")
        .insert(payload)
        .execute()
    )


def process_batch(client: Client, model, label_encoder) -> int:
    rows = fetch_unprocessed_rows(client)
    if not rows:
        return 0

    processed_count = 0
    for row in rows:
        try:
            predicted_status, confidence = predict_status(
                model,
                label_encoder,
                float(row["temp_c"]),
                float(row["current_a"]),
                float(row["vibration_rms_g"]),
                str(row["load_level"]),
            )
            insert_prediction(client, row, predicted_status, confidence)
            mark_processed(client, int(row["id"]))
            processed_count += 1
            print(
                f"Processed raw_id={row['id']} status={predicted_status} confidence={confidence:.4f}"
            )
        except Exception as exc:
            print(f"Failed processing raw_id={row.get('id')}: {exc}")

    return processed_count


def main() -> None:
    model, label_encoder = load_model_artifacts()
    client = get_supabase_client()
    print("ML worker started. Watching iot.raw_sensor_readings for new rows...")

    while True:
        processed = process_batch(client, model, label_encoder)
        if processed == 0:
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
