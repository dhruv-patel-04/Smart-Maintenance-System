import os
import joblib
import pandas as pd

from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, LabelEncoder

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(BASE_DIR, "smart_maintenance_dataset.csv")
MODEL_PATH = os.path.join(BASE_DIR, "smart_maintenance_model.pkl")
LABEL_ENCODER_PATH = os.path.join(BASE_DIR, "label_encoder.pkl")


def main() -> None:
    if not os.path.exists(DATA_PATH):
        raise FileNotFoundError(f"Dataset not found: {DATA_PATH}")

    df = pd.read_csv(DATA_PATH)

    required_columns = ["Temp_C", "Current_A", "Vibration_RMS_g", "Load_Level", "Status"]
    missing = [c for c in required_columns if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    X = df[["Temp_C", "Current_A", "Vibration_RMS_g", "Load_Level"]].copy()
    y = df["Status"].astype(str)

    label_encoder = LabelEncoder()
    y_encoded = label_encoder.fit_transform(y)

    numeric_features = ["Temp_C", "Current_A", "Vibration_RMS_g"]
    categorical_features = ["Load_Level"]

    preprocessor = ColumnTransformer(
        transformers=[
            ("num", "passthrough", numeric_features),
            ("cat", OneHotEncoder(handle_unknown="ignore"), categorical_features),
        ]
    )

    model = RandomForestClassifier(
        n_estimators=200,
        max_depth=12,
        min_samples_split=4,
        min_samples_leaf=2,
        random_state=42,
        n_jobs=-1,
        class_weight="balanced_subsample",
    )

    pipeline = Pipeline(
        steps=[
            ("preprocessor", preprocessor),
            ("model", model),
        ]
    )

    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y_encoded,
        test_size=0.2,
        random_state=42,
        stratify=y_encoded,
    )

    pipeline.fit(X_train, y_train)
    y_pred = pipeline.predict(X_test)

    print("\nClassification Report\n")
    print(classification_report(y_test, y_pred, target_names=label_encoder.classes_))

    print("\nConfusion Matrix\n")
    print(confusion_matrix(y_test, y_pred))

    joblib.dump(pipeline, MODEL_PATH)
    joblib.dump(label_encoder, LABEL_ENCODER_PATH)

    print(f"\nSaved model to: {MODEL_PATH}")
    print(f"Saved label encoder to: {LABEL_ENCODER_PATH}")


if __name__ == "__main__":
    main()
