# Smart Maintenance System (IoT + ML)

A full starter implementation for an IoT and ML based smart maintenance system.

## Features
- Flask web dashboard
- Supabase (Postgres) database with separate raw and prediction tables
- ML model training using the generated dataset
- Standalone IoT ingestion (ESP32/simulator can write directly to Supabase)
- Real-time prediction for `Normal`, `Warning`, `Failure`
- Sensor simulator to emulate ESP32/IoT data flow

## Project Structure
- `app.py` - Flask dashboard + predict API (reads prediction rows from Supabase)
- `train_model.py` - trains the ML model
- `sensor_simulator.py` - sends sample raw sensor rows directly to Supabase
- `ml_worker.py` - processes only new raw rows and writes ML output to Supabase
- `supabase_schema.sql` - SQL script to create schemas/tables/indexes in Supabase
- `templates/` - Bootstrap UI
- `smart_maintenance_dataset.csv` - generated research-grade dataset
- `smart_maintenance_model.pkl` - trained model artifact (generated after training)
- `label_encoder.pkl` - label encoder artifact

## Setup

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# Linux / macOS
source venv/bin/activate

pip install -r requirements.txt
python train_model.py

# Run this SQL in Supabase SQL editor
# (copy from supabase_schema.sql)

# Set environment variables (PowerShell)
$env:SUPABASE_URL="https://YOUR_PROJECT_ID.supabase.co"
$env:SUPABASE_SERVICE_ROLE_KEY="YOUR_SUPABASE_SERVICE_ROLE_JWT_KEY"

# Start ML worker (polls new raw rows only)
python ml_worker.py

# In another terminal, start Flask dashboard
python app.py

# Optional simulator to mimic ESP32 writing raw rows
python sensor_simulator.py
```

Open:
- Dashboard: http://127.0.0.1:5000/
- API health: http://127.0.0.1:5000/api/health

## Data Flow

1. ESP32/simulator inserts into `iot.raw_sensor_readings`.
2. `ml_worker.py` fetches only rows where `processed = false`.
3. Worker predicts status using the ML model and inserts into `analytics.sensor_predictions`.
4. Worker marks raw row as processed.
5. Flask dashboard reads from `analytics.sensor_predictions`.

## API Endpoints

### 1) Predict without storing
**POST** `/api/predict`

```json
{
  "machine_id": "M-101",
  "temp_c": 72.4,
  "current_a": 8.2,
  "vibration_rms_g": 0.031,
  "load_level": "High"
}
```

```json
{
  "temp_c": 65.0,
  "current_a": 6.5,
  "vibration_rms_g": 0.018,
  "load_level": "Medium"
}
```

## Notes
- This version uses a Random Forest classifier for a strong baseline.
- Keep `SUPABASE_SERVICE_ROLE_KEY` server-side only. Do not embed service role key in public clients.
- Do not use `sb_publishable_...` key in backend Python scripts. Use anon/service-role JWT key from Supabase API settings.
- You can later replace polling in `ml_worker.py` with webhook/realtime triggers.
