"""Flask app for Sift, a local-first subscription auditor."""

from __future__ import annotations

import csv
import io
import os
from datetime import date
from pathlib import Path
from typing import Any, Dict

from flask import Flask, Response, jsonify, render_template, request, send_file

from . import coach, detector, storage


ROOT = Path(__file__).resolve().parent.parent


def _env_path(name: str, default: Path) -> Path:
    value = os.getenv(name)
    return Path(value).expanduser().resolve() if value else default


def create_app(test_config: Dict[str, Any] = None) -> Flask:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    app = Flask(__name__)
    app.config.update(
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        JSON_SORT_KEYS=False,
    )
    if test_config:
        app.config.update(test_config)

    data_dir = _env_path("SIFT_DATA_DIR", ROOT / "data")
    db_path = _env_path("SIFT_DB_PATH", data_dir / "sift.db")
    sample_path = _env_path("SIFT_SAMPLE_PATH", data_dir / "sample_transactions.csv")
    data_dir.mkdir(parents=True, exist_ok=True)
    storage.init_db(db_path)

    app.config["SIFT_DB_PATH"] = db_path
    app.config["SIFT_SAMPLE_PATH"] = sample_path

    def add_plan(analysis: Dict[str, Any]) -> Dict[str, Any]:
        cancel = [sub for sub in analysis["subscriptions"] if sub.get("decision") == "cancel"]
        keep = [sub for sub in analysis["subscriptions"] if sub.get("decision") == "keep"]
        review = [sub for sub in analysis["subscriptions"] if sub.get("decision") == "review"]
        analysis["plan"] = {
            "cancelling": len(cancel),
            "keeping": len(keep),
            "reviewing": len(review),
            "unreviewed": len(analysis["subscriptions"]) - len(cancel) - len(keep) - len(review),
            "confirmed_annual_savings": round(sum(sub["annual_cost"] for sub in cancel), 2),
            "confirmed_monthly_savings": round(sum(sub["monthly_cost"] for sub in cancel), 2),
            "review_annual_value": round(sum(sub["annual_cost"] for sub in review), 2),
        }
        return analysis

    def analyze_text(text: str, source: str, is_demo: bool = False) -> Dict[str, Any]:
        transactions, meta = detector.parse_csv(text, source=source)
        meta["source"] = source
        analysis = detector.build_analysis(transactions, meta, today=date.today())
        analysis["is_demo"] = bool(is_demo)
        analysis["insights"] = coach.local_insights(analysis)
        add_plan(analysis)
        return storage.save_analysis(db_path, analysis)

    def seed_demo() -> None:
        if storage.get_latest(db_path) is not None:
            return
        if not sample_path.exists():
            return
        try:
            analyze_text(
                sample_path.read_text(encoding="utf-8"),
                source=sample_path.name,
                is_demo=True,
            )
        except Exception as exc:  # keep the server up even if sample data is broken
            app.logger.warning("Could not seed demo analysis: %s", exc)

    seed_demo()

    @app.get("/")
    def index() -> str:
        return render_template("index.html")

    @app.get("/api/state")
    def state() -> Response:
        analysis = storage.get_latest(db_path)
        if not analysis:
            return jsonify({"analysis": None})
        add_plan(analysis)
        return jsonify({"analysis": analysis})

    @app.post("/api/analyze")
    def analyze() -> Response:
        upload = request.files.get("file")
        if not upload or not upload.filename:
            return jsonify({"error": "Choose a CSV file first."}), 400
        if not upload.filename.lower().endswith(".csv"):
            return jsonify({"error": "Sift accepts CSV files. Download the sample to see the format."}), 400
        raw = upload.read()
        if not raw:
            return jsonify({"error": "That file is empty."}), 400
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        safety_name = Path(upload.filename).name[:80]
        try:
            analysis = analyze_text(text, source=safety_name, is_demo=False)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"analysis": analysis})

    @app.post("/api/demo")
    def demo() -> Response:
        if not sample_path.exists():
            return jsonify({"error": "Sample CSV is missing."}), 404
        analysis = analyze_text(
            sample_path.read_text(encoding="utf-8"),
            source=sample_path.name,
            is_demo=True,
        )
        return jsonify({"analysis": analysis})

    @app.post("/api/insights")
    def insights() -> Response:
        analysis = storage.get_latest(db_path)
        if not analysis:
            return jsonify({"error": "Analyse a CSV first."}), 404
        result = coach.live_insights(analysis)
        analysis["insights"] = result
        add_plan(analysis)
        storage.update_analysis(db_path, int(analysis["id"]), analysis)
        return jsonify({"analysis": analysis})

    @app.post("/api/decision")
    def decision() -> Response:
        analysis = storage.get_latest(db_path)
        if not analysis:
            return jsonify({"error": "Analyse a CSV first."}), 404
        payload = request.get_json(silent=True) or {}
        merchant_id = str(payload.get("id", ""))
        decision = str(payload.get("decision", "")).lower()
        usage = payload.get("usage")
        if decision not in {"unreviewed", "keep", "review", "cancel"}:
            return jsonify({"error": "Decision must be keep, review or cancel."}), 400
        if usage is not None and usage not in {"daily", "sometimes", "rarely", "never", ""}:
            return jsonify({"error": "Usage must be daily, sometimes, rarely or never."}), 400
        found = False
        for sub in analysis["subscriptions"]:
            if sub["id"] == merchant_id:
                sub["decision"] = decision
                if "usage" in payload:
                    sub["usage"] = usage or None
                sub["note"] = str(payload.get("note", ""))[:240]
                found = True
                break
        if not found:
            return jsonify({"error": "Subscription not found."}), 404
        add_plan(analysis)
        storage.update_analysis(db_path, int(analysis["id"]), analysis)
        return jsonify({"analysis": analysis})

    @app.post("/api/email")
    def email() -> Response:
        analysis = storage.get_latest(db_path)
        if not analysis:
            return jsonify({"error": "Analyse a CSV first."}), 404
        payload = request.get_json(silent=True) or {}
        merchant_id = str(payload.get("id", ""))
        sub = next((item for item in analysis["subscriptions"] if item["id"] == merchant_id), None)
        if not sub:
            return jsonify({"error": "Subscription not found."}), 404
        draft = coach.draft_email(sub, str(payload.get("reason", ""))[:400])
        return jsonify({"email": draft})

    @app.get("/api/export.csv")
    def export_csv() -> Response:
        analysis = storage.get_latest(db_path)
        if not analysis:
            return jsonify({"error": "Nothing to export yet."}), 404
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "merchant",
                "category",
                "cadence",
                "amount",
                "currency",
                "monthly_cost",
                "annual_cost",
                "next_renewal",
                "confidence",
                "decision",
                "usage",
            ]
        )
        for sub in analysis["subscriptions"]:
            writer.writerow(
                [
                    sub["merchant"],
                    sub["category"],
                    sub["cadence_label"],
                    sub["amount"],
                    sub["currency"],
                    sub["monthly_cost"],
                    sub["annual_cost"],
                    sub["next_renewal"],
                    sub["confidence"],
                    sub["decision"],
                    sub["usage"] or "",
                ]
            )
        response = Response("\ufeff" + output.getvalue(), mimetype="text/csv; charset=utf-8")
        response.headers["Content-Disposition"] = "attachment; filename=sift-subscriptions.csv"
        return response

    @app.get("/api/sample.csv")
    def sample_csv() -> Response:
        if not sample_path.exists():
            return jsonify({"error": "Sample CSV is missing."}), 404
        return send_file(sample_path, as_attachment=True, download_name="sample_transactions.csv")

    @app.get("/api/health")
    def health() -> Response:
        return jsonify({"ok": True, "app": "sift", "date": date.today().isoformat()})

    @app.errorhandler(413)
    def too_large(_error: Exception) -> Response:
        return jsonify({"error": "That CSV is larger than 2 MB. Try a shorter date range."}), 413

    @app.errorhandler(404)
    def not_found(error: Exception) -> Response:
        if request.path.startswith("/api/"):
            return jsonify({"error": "Not found."}), 404
        return render_template("index.html"), 404

    @app.errorhandler(500)
    def server_error(error: Exception) -> Response:
        app.logger.exception("Unhandled error: %s", error)
        if request.path.startswith("/api/"):
            return jsonify({"error": "Something went wrong on the server. Check the terminal."}), 500
        return "Something went wrong.", 500

    return app
