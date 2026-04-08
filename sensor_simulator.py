import random
import time
import os
from typing import Dict

from dotenv import load_dotenv
from supabase import Client, create_client

MACHINE_ID = "M-101"
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or os.getenv("SUPABASE_ANON_KEY")
    or os.getenv("SUPABASE_KEY", "")
).strip().strip('"').strip("'")
SUPABASE_IOT_SCHEMA = os.getenv("SUPABASE_IOT_SCHEMA", "iot")


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


def generate_reading() -> Dict[str, object]:
    mode = random.choices(
        population=["normal", "warning", "failure"],
        weights=[0.75, 0.18, 0.07],
        k=1,
    )[0]

    if mode == "normal":
        temp_c = round(random.uniform(40, 65), 2)
        current_a = round(random.uniform(3.5, 7.0), 2)
        vibration_rms_g = round(random.uniform(0.006, 0.018), 4)
    elif mode == "warning":
        temp_c = round(random.uniform(70, 82), 2)
        current_a = round(random.uniform(7.5, 10.2), 2)
        vibration_rms_g = round(random.uniform(0.021, 0.035), 4)
    else:
        temp_c = round(random.uniform(86, 98), 2)
        current_a = round(random.uniform(11.5, 14.5), 2)
        vibration_rms_g = round(random.uniform(0.051, 0.075), 4)

    if current_a < 4:
        load_level = "Low"
    elif current_a < 7:
        load_level = "Medium"
    else:
        load_level = "High"

    return {
        "machine_id": MACHINE_ID,
        "temp_c": temp_c,
        "current_a": current_a,
        "vibration_rms_g": vibration_rms_g,
        "load_level": load_level,
        "source": "simulator",
    }


def main() -> None:
    client = get_supabase_client()
    print("Sending sensor readings to Supabase table iot.raw_sensor_readings")
    while True:
        payload = generate_reading()
        try:
            response = (
                client.schema(SUPABASE_IOT_SCHEMA)
                .table("raw_sensor_readings")
                .insert(payload)
                .execute()
            )
            inserted = response.data[0] if response.data else {}
            print("Inserted raw row id=", inserted.get("id"), "payload=", payload)
        except Exception as exc:
            print("Error:", exc)
        time.sleep(2)


if __name__ == "__main__":
    main()
