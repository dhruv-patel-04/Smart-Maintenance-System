import io
from datetime import datetime

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image,
    PageBreak,
)


def _safe_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _statistics(series):
    if series.empty:
        return {
            "average": None,
            "minimum": None,
            "maximum": None,
        }

    return {
        "average": round(float(series.mean()), 4),
        "minimum": round(float(series.min()), 4),
        "maximum": round(float(series.max()), 4),
    }


def _trend(series):
    if len(series) < 2:
        return "Insufficient data"

    first = float(series.iloc[0])
    last = float(series.iloc[-1])

    if abs(first) < 1e-9:
        return "Stable"

    change = ((last - first) / abs(first)) * 100

    if change > 5:
        return "Increasing"
    if change < -5:
        return "Decreasing"

    return "Stable"


def _overall_status(normal_count, warning_count, failure_count):
    total = normal_count + warning_count + failure_count

    if total == 0:
        return "Unknown"

    failure_percentage = (failure_count / total) * 100
    warning_percentage = (warning_count / total) * 100

    # Significant number of failure predictions
    if failure_percentage >= 10:
        return "Failure"

    # Some sustained abnormal behavior
    elif failure_percentage >= 5 or warning_percentage >= 10:
        return "Warning"

    # Predominantly healthy operation
    else:
        return "Normal"


def _build_summary(
    overall_status,
    total_readings,
    normal_count,
    warning_count,
    failure_count,
    temp_trend,
    current_trend,
    vibration_trend,
):
    if total_readings == 0:
        return "No sensor prediction data was available for the selected period."

    if overall_status == "Failure":
        opening = (
            "The motor showed failure-level prediction events during the "
            "selected monitoring period."
        )
    elif overall_status == "Warning":
        opening = (
            "The motor operated with some warning-level events during the "
            "selected monitoring period."
        )
    else:
        opening = (
            "The motor operated primarily under normal predicted conditions "
            "during the selected monitoring period."
        )

    trends = []

    if temp_trend != "Stable" and temp_trend != "Insufficient data":
        trends.append(f"temperature was {temp_trend.lower()}")

    if current_trend != "Stable" and current_trend != "Insufficient data":
        trends.append(f"current was {current_trend.lower()}")

    if vibration_trend != "Stable" and vibration_trend != "Insufficient data":
        trends.append(f"vibration was {vibration_trend.lower()}")

    if trends:
        trend_text = " During the period, " + ", ".join(trends) + "."
    else:
        trend_text = " The measured parameters remained relatively stable."

    return (
        f"{opening} "
        f"A total of {total_readings:,} readings were analyzed, including "
        f"{normal_count:,} normal, {warning_count:,} warning, and "
        f"{failure_count:,} failure predictions."
        f"{trend_text}"
    )


def build_report_data(rows, machine_id, period_start, period_end):
    """
    Convert sensor prediction rows into structured report data.

    rows must contain:
      event_ts
      temp_c
      current_a
      vibration_rms_g
      predicted_status
      confidence
    """

    if not rows:
        df = pd.DataFrame(
            columns=[
                "event_ts",
                "temp_c",
                "current_a",
                "vibration_rms_g",
                "predicted_status",
                "confidence",
            ]
        )
    else:
        df = pd.DataFrame(rows)

    if not df.empty:
        df["event_ts"] = pd.to_datetime(df["event_ts"], errors="coerce")
        df["temp_c"] = pd.to_numeric(df["temp_c"], errors="coerce")
        df["current_a"] = pd.to_numeric(df["current_a"], errors="coerce")
        df["vibration_rms_g"] = pd.to_numeric(
            df["vibration_rms_g"], errors="coerce"
        )
        df["confidence"] = pd.to_numeric(
            df["confidence"], errors="coerce"
        )

        df = df.dropna(subset=["event_ts"])

    total_readings = len(df)

    if total_readings:
        status_counts = (
            df["predicted_status"]
            .fillna("Unknown")
            .value_counts()
            .to_dict()
        )
    else:
        status_counts = {}

    normal_count = int(status_counts.get("Normal", 0))
    warning_count = int(status_counts.get("Warning", 0))
    failure_count = int(status_counts.get("Failure", 0))

    normal_percentage = (normal_count / total_readings) * 100 if total_readings else 0
    warning_percentage = (warning_count / total_readings) * 100 if total_readings else 0
    failure_percentage = (failure_count / total_readings) * 100 if total_readings else 0

    temp_stats = _statistics(df["temp_c"].dropna())
    current_stats = _statistics(df["current_a"].dropna())
    vibration_stats = _statistics(df["vibration_rms_g"].dropna())

    temp_trend = _trend(df["temp_c"].dropna())
    current_trend = _trend(df["current_a"].dropna())
    vibration_trend = _trend(df["vibration_rms_g"].dropna())

    overall_status = _overall_status(
        normal_count,
        warning_count,
        failure_count,
    )

    if total_readings and df["confidence"].notna().any():
        average_confidence = round(
            float(df["confidence"].dropna().mean()),
            4,
        )
    else:
        average_confidence = None

    # Keep chart data reasonably small.
    chart_df = df.copy()

    if len(chart_df) > 300:
        step = max(len(chart_df) // 300, 1)
        chart_df = chart_df.iloc[::step].copy()

    chart_data = {
        "labels": [
            timestamp.strftime("%Y-%m-%d %H:%M:%S")
            for timestamp in chart_df["event_ts"]
        ],
        "temperature": [
            _safe_float(value)
            for value in chart_df["temp_c"]
        ],
        "current": [
            _safe_float(value)
            for value in chart_df["current_a"]
        ],
        "vibration": [
            _safe_float(value)
            for value in chart_df["vibration_rms_g"]
        ],
    }

    summary = _build_summary(
        overall_status,
        total_readings,
        normal_count,
        warning_count,
        failure_count,
        temp_trend,
        current_trend,
        vibration_trend,
    )

    return {
        "machine_id": machine_id,
        "period_start": str(period_start),
        "period_end": str(period_end),

        "total_readings": total_readings,

        "status_counts": {
            "normal": normal_count,
            "warning": warning_count,
            "failure": failure_count,
        },

        "status_percentages": {
            "normal": round(normal_percentage, 2),
            "warning": round(warning_percentage, 2),
            "failure": round(failure_percentage, 2),
        },

        "average_confidence": average_confidence,

        "overall_status": overall_status,

        "temperature": {
            **temp_stats,
            "trend": temp_trend,
        },

        "current": {
            **current_stats,
            "trend": current_trend,
        },

        "vibration": {
            **vibration_stats,
            "trend": vibration_trend,
        },

        "summary": summary,

        "charts": chart_data,
    }


def _fmt(value, suffix=""):
    if value is None:
        return "-"

    return f"{value:.2f}{suffix}"


def _create_chart_image(labels, values, title, y_label):
    """
    Create a line chart image for embedding into the PDF.
    Returns a BytesIO object or None.
    """

    if not labels or not values:
        return None

    # Remove None values while keeping labels aligned
    valid_data = [
        (label, value)
        for label, value in zip(labels, values)
        if value is not None
    ]

    if len(valid_data) < 2:
        return None

    labels, values = zip(*valid_data)

    fig, ax = plt.subplots(figsize=(8, 3))

    x = range(len(values))

    ax.plot(
        x,
        values,
        linewidth=1.8,
    )

    ax.set_title(
        title,
        fontsize=11,
        fontweight="bold",
        pad=8,
    )

    ax.set_ylabel(y_label)

    # Keep timestamp labels readable
    max_labels = 8

    if len(labels) <= max_labels:
        tick_positions = list(range(len(labels)))
    else:
        step = max(len(labels) // max_labels, 1)
        tick_positions = list(range(0, len(labels), step))

        if tick_positions[-1] != len(labels) - 1:
            tick_positions.append(len(labels) - 1)

    ax.set_xticks(tick_positions)

    ax.set_xticklabels(
        [labels[i] for i in tick_positions],
        rotation=30,
        ha="right",
        fontsize=7,
    )

    ax.grid(
        True,
        alpha=0.25,
        linewidth=0.7,
    )

    fig.tight_layout()

    image_buffer = io.BytesIO()

    fig.savefig(
        image_buffer,
        format="png",
        dpi=150,
        bbox_inches="tight",
    )

    plt.close(fig)

    image_buffer.seek(0)

    return image_buffer


def _add_page_number(canvas, document):
    canvas.saveState()

    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.grey)

    canvas.drawString(
        15 * mm,
        8 * mm,
        "Smart Maintenance System • Motor Performance Report",
    )

    canvas.drawRightString(
        A4[0] - 15 * mm,
        8 * mm,
        f"Page {document.page}",
    )

    canvas.restoreState()


def generate_pdf(report_data):
    """
    Generate a PDF from structured report data.

    Returns:
        bytes
    """

    output = io.BytesIO()

    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title="Motor Performance Report",
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        alignment=TA_CENTER,
        fontSize=20,
        leading=24,
        spaceAfter=8,
    )

    subtitle_style = ParagraphStyle(
        "ReportSubtitle",
        parent=styles["Normal"],
        alignment=TA_CENTER,
        fontSize=10,
        textColor=colors.grey,
        spaceAfter=18,
    )

    heading_style = ParagraphStyle(
        "ReportHeading",
        parent=styles["Heading2"],
        fontSize=13,
        spaceBefore=12,
        spaceAfter=8,
    )

    body_style = ParagraphStyle(
        "ReportBody",
        parent=styles["BodyText"],
        fontSize=9.5,
        leading=14,
        spaceAfter=8,
    )

    story = []

    story.append(
        Paragraph(
            "SMART MAINTENANCE SYSTEM",
            title_style,
        )
    )

    story.append(
        Paragraph(
            "Motor Performance Report",
            subtitle_style,
        )
    )

    machine_id = report_data["machine_id"]
    period_start = report_data["period_start"]
    period_end = report_data["period_end"]

    story.append(
        Table(
            [
                ["Machine", machine_id],
                ["Monitoring Period", f"{period_start} to {period_end}"],
                [
                    "Generated",
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ],
            ],
            colWidths=[45 * mm, 125 * mm],
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f5f9")),
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d8e2ee")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("PADDING", (0, 0), (-1, -1), 7),
                ]
            ),
        )
    )

    story.append(Spacer(1, 10))

    story.append(Paragraph("Overall Performance", heading_style))

    status = report_data["overall_status"]

    overview = [
        ["Overall Status", status],
        ["Total Readings", f"{report_data['total_readings']:,}"],
        [
            "Average Confidence",
            _fmt(
                (
                    report_data["average_confidence"] * 100
                    if report_data["average_confidence"] is not None
                    else None
                ),
                "%",
            ),
        ],
        [
            "Normal Predictions",
            f"{report_data['status_counts']['normal']:,}",
        ],
        [
            "Warning Predictions",
            f"{report_data['status_counts']['warning']:,}",
        ],
        [
            "Failure Predictions",
            f"{report_data['status_counts']['failure']:,}",
        ],
    ]

    story.append(
        Table(
            overview,
            colWidths=[75 * mm, 95 * mm],
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f6f9fc")),
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d8e2ee")),
                    ("PADDING", (0, 0), (-1, -1), 7),
                ]
            ),
        )
    )

    story.append(Paragraph("Sensor Performance", heading_style))

    sensor_table = [
        [
            "Parameter",
            "Average",
            "Minimum",
            "Maximum",
            "Trend",
        ],
        [
            "Temperature",
            _fmt(report_data["temperature"]["average"], " °C"),
            _fmt(report_data["temperature"]["minimum"], " °C"),
            _fmt(report_data["temperature"]["maximum"], " °C"),
            report_data["temperature"]["trend"],
        ],
        [
            "Current",
            _fmt(report_data["current"]["average"], " A"),
            _fmt(report_data["current"]["minimum"], " A"),
            _fmt(report_data["current"]["maximum"], " A"),
            report_data["current"]["trend"],
        ],
        [
            "Vibration",
            _fmt(report_data["vibration"]["average"], " g"),
            _fmt(report_data["vibration"]["minimum"], " g"),
            _fmt(report_data["vibration"]["maximum"], " g"),
            report_data["vibration"]["trend"],
        ],
    ]

    story.append(
        Table(
            sensor_table,
            colWidths=[35 * mm, 32 * mm, 32 * mm, 32 * mm, 39 * mm],
            repeatRows=1,
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d8e2ee")),
                    ("PADDING", (0, 0), (-1, -1), 6),
                    ("ALIGN", (1, 1), (-2, -1), "RIGHT"),
                ]
            ),
        )
    )

    story.append(PageBreak())

    story.append(
    Paragraph(
        "Performance Trends",
        heading_style,
    )
    )

    charts = report_data.get("charts", {})

    chart_labels = charts.get("labels", [])

    temperature_image = _create_chart_image(
        chart_labels,
        charts.get("temperature", []),
        "Temperature Trend",
        "Temperature (°C)",
    )

    current_image = _create_chart_image(
        chart_labels,
        charts.get("current", []),
        "Current Trend",
        "Current (A)",
    )

    vibration_image = _create_chart_image(
        chart_labels,
        charts.get("vibration", []),
        "Vibration Trend",
        "Vibration (g)",
    )


    if temperature_image:
        story.append(
            Image(
                temperature_image,
                width=170 * mm,
                height=63 * mm,
            )
        )

        story.append(Spacer(1, 8))


    if current_image:
        story.append(
            Image(
                current_image,
                width=170 * mm,
                height=63 * mm,
            )
        )

        story.append(Spacer(1, 8))


    if vibration_image:
        story.append(
            Image(
                vibration_image,
                width=170 * mm,
                height=63 * mm,
            )
        )

        story.append(Spacer(1, 8))


    story.append(PageBreak())

    story.append(Paragraph("Performance Summary", heading_style))
    story.append(
        Paragraph(
            report_data["summary"],
            body_style,
        )
    )

    story.append(Paragraph("Prediction Distribution", heading_style))

    distribution = [
        ["Status", "Count", "Percentage"],

        [
            "Normal",
            f"{report_data['status_counts']['normal']:,}",
            f"{report_data['status_percentages']['normal']:.2f}%"
        ],

        [
            "Warning",
            f"{report_data['status_counts']['warning']:,}",
            f"{report_data['status_percentages']['warning']:.2f}%"
        ],

        [
            "Failure",
            f"{report_data['status_counts']['failure']:,}",
            f"{report_data['status_percentages']['failure']:.2f}%"
        ],
    ]

    story.append(
        Table(
            distribution,
            colWidths=[60 * mm, 55 * mm, 55 * mm],
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d8e2ee")),
                    ("PADDING", (0, 0), (-1, -1), 7),
                ]
            ),
        )
    )

    document.build(
        story,
        onFirstPage=_add_page_number,
        onLaterPages=_add_page_number,
    )

    return output.getvalue()