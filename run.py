"""Run Sift locally.

    python run.py

The app binds to http://127.0.0.1:5050 by default. Use PORT to change it.
"""

from __future__ import annotations

import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5050"))
    app.run(host="127.0.0.1", port=port, debug=os.getenv("FLASK_DEBUG") == "1")
