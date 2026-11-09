"""Tiny SQLite persistence layer. One row holds one analysis as JSON."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path), timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def init_db(db_path: Path) -> None:
    with _connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                source TEXT NOT NULL,
                is_demo INTEGER NOT NULL DEFAULT 0,
                payload TEXT NOT NULL
            )
            """
        )
        connection.commit()


def save_analysis(db_path: Path, analysis: Dict[str, Any]) -> Dict[str, Any]:
    now = datetime.now().isoformat(timespec="seconds")
    analysis["created_at"] = analysis.get("created_at") or now
    with _connect(db_path) as connection:
        cursor = connection.execute(
            "INSERT INTO analyses (created_at, source, is_demo, payload) VALUES (?, ?, ?, ?)",
            (
                analysis["created_at"],
                analysis.get("source", "transactions.csv"),
                1 if analysis.get("is_demo") else 0,
                json.dumps(analysis, ensure_ascii=False),
            ),
        )
        analysis_id = int(cursor.lastrowid)
        connection.commit()
    analysis["id"] = analysis_id
    # Re-save with the id embedded so /state remains self-contained.
    update_analysis(db_path, analysis_id, analysis)
    return analysis


def update_analysis(db_path: Path, analysis_id: int, analysis: Dict[str, Any]) -> None:
    analysis["id"] = int(analysis_id)
    with _connect(db_path) as connection:
        connection.execute(
            "UPDATE analyses SET payload = ?, source = ?, is_demo = ? WHERE id = ?",
            (
                json.dumps(analysis, ensure_ascii=False),
                analysis.get("source", "transactions.csv"),
                1 if analysis.get("is_demo") else 0,
                int(analysis_id),
            ),
        )
        connection.commit()


def get_latest(db_path: Path) -> Optional[Dict[str, Any]]:
    with _connect(db_path) as connection:
        row = connection.execute(
            "SELECT id, payload FROM analyses ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    data = json.loads(row["payload"])
    data["id"] = int(row["id"])
    return data


def count_analyses(db_path: Path) -> int:
    with _connect(db_path) as connection:
        row = connection.execute("SELECT COUNT(*) AS count FROM analyses").fetchone()
    return int(row["count"])
