"""Render docs/submission.md to docs/submission.pdf with headless Chrome.

Usage:
    cd sift
    python docs/build_pdf.py

Only the Python standard library is required. Set CHROME_BIN if Chrome is
installed somewhere unusual.
"""

from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


DOCS = Path(__file__).resolve().parent
SOURCE = DOCS / "submission.md"
OUTPUT = DOCS / "submission.pdf"
TEMP_HTML = DOCS / ".submission.html"


CSS = """
@page { size: A4; margin: 16mm 15mm 16mm; }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body {
  margin: 0;
  color: #20221c;
  background: #ffffff;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  font-size: 10.2pt;
  line-height: 1.58;
}
.doc-banner {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 22px;
  padding-bottom: 9px;
  border-bottom: 2px solid #b9e94e;
  color: #555b4d;
  font-size: 8pt;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}
h1, h2, h3, h4 {
  color: #171914;
  font-family: Georgia, "Times New Roman", serif;
  font-weight: 600;
  line-height: 1.16;
  break-after: avoid;
}
h1 { margin: 0 0 8px; font-size: 30pt; letter-spacing: -0.035em; }
h2 {
  margin: 30px 0 12px;
  padding-top: 12px;
  border-top: 1px solid #d9ddcf;
  font-size: 19pt;
  letter-spacing: -0.02em;
}
h3 { margin: 22px 0 7px; font-size: 13.5pt; }
h4 { margin: 16px 0 5px; font-size: 11pt; }
p { margin: 0 0 9px; orphans: 3; widows: 3; }
strong { color: #11130f; }
em { color: #3d4338; }
a { color: #3d5b00; text-decoration: none; }
code {
  padding: 1px 4px;
  border: 1px solid #dfe3d6;
  border-radius: 3px;
  background: #f4f6ef;
  color: #253000;
  font-family: "SF Mono", Menlo, Consolas, monospace;
  font-size: 8.7pt;
}
pre {
  margin: 10px 0 14px;
  padding: 12px 14px;
  border: 1px solid #dfe3d6;
  border-radius: 5px;
  background: #f5f7f0;
  white-space: pre-wrap;
  break-inside: avoid;
}
pre code { padding: 0; border: 0; background: transparent; font-size: 8.6pt; }
blockquote {
  margin: 14px 0;
  padding: 10px 14px;
  border-left: 3px solid #b9e94e;
  background: #f7f9f1;
  color: #3b4134;
}
blockquote p { margin: 0 0 6px; }
blockquote p:last-child { margin-bottom: 0; }
ul, ol { margin: 6px 0 11px; padding-left: 21px; }
li { margin: 3px 0; }
hr { margin: 22px 0; border: 0; border-top: 1px solid #d9ddcf; }
table {
  width: 100%;
  margin: 10px 0 14px;
  border-collapse: collapse;
  font-size: 9.2pt;
  break-inside: avoid;
}
th {
  padding: 6px 7px;
  border-bottom: 2px solid #aeb5a2;
  background: #f0f3e9;
  text-align: left;
  font-weight: 700;
}
td { padding: 5px 7px; border-bottom: 1px solid #e1e4da; vertical-align: top; }
tr:nth-child(even) td { background: #fafbf7; }
figure { margin: 14px 0 18px; break-inside: avoid; }
figure img {
  width: 100%;
  max-height: 170mm;
  object-fit: contain;
  object-position: top;
  border: 1px solid #cfd4c5;
  border-radius: 6px;
  background: #f4f1e8;
}
figcaption {
  margin-top: 5px;
  color: #626858;
  font-size: 8.5pt;
  line-height: 1.4;
}
.pagebreak { break-before: page; }
"""


def inline(text: str) -> str:
    """Small, dependency-free Markdown inline renderer."""
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


def is_table_start(lines, index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    first = lines[index].strip()
    second = lines[index + 1].strip()
    return (
        first.startswith("|")
        and first.endswith("|")
        and bool(re.match(r"^\|?[\s:\-|]+\|?$", second))
    )


def split_row(line: str):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def render(markdown: str) -> str:
    lines = markdown.splitlines()
    output: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if not stripped:
            index += 1
            continue

        if stripped == "<!-- pagebreak -->":
            output.append('<div class="pagebreak"></div>')
            index += 1
            continue

        if stripped.startswith("```"):
            language = stripped[3:].strip()
            index += 1
            code_lines = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index])
                index += 1
            index += 1
            escaped = html.escape("\n".join(code_lines))
            css_class = f' class="language-{html.escape(language)}"' if language else ""
            output.append(f"<pre><code{css_class}>{escaped}</code></pre>")
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            level = len(heading.group(1))
            output.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
            index += 1
            continue

        if re.match(r"^\s*([-*_])\s*\1\s*\1[\s\-*_]*$", line):
            output.append("<hr>")
            index += 1
            continue

        if stripped.startswith(">"):
            quote_lines = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quote_lines.append(lines[index].strip()[1:].strip())
                index += 1
            quote = " ".join(part for part in quote_lines if part)
            output.append(f"<blockquote><p>{inline(quote)}</p></blockquote>")
            continue

        image = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$", stripped)
        if image:
            alt, src = image.group(1), image.group(2)
            output.append(
                f'<figure><img src="{html.escape(src)}" alt="{html.escape(alt)}">'
                f"<figcaption>{inline(alt)}</figcaption></figure>"
            )
            index += 1
            continue

        if is_table_start(lines, index):
            header = split_row(lines[index])
            index += 2
            rows = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(split_row(lines[index]))
                index += 1
            head_html = "".join(f"<th>{inline(cell)}</th>" for cell in header)
            body_html = "".join(
                "<tr>" + "".join(f"<td>{inline(cell)}</td>" for cell in row) + "</tr>"
                for row in rows
            )
            output.append(
                f"<table><thead><tr>{head_html}</tr></thead><tbody>{body_html}</tbody></table>"
            )
            continue

        bullet = re.match(r"^\s*[-*]\s+(.*)$", line)
        if bullet:
            items = []
            while index < len(lines):
                match = re.match(r"^\s*[-*]\s+(.*)$", lines[index])
                if not match:
                    break
                items.append(f"<li>{inline(match.group(1))}</li>")
                index += 1
            output.append("<ul>" + "".join(items) + "</ul>")
            continue

        numbered = re.match(r"^\s*\d+\.\s+(.*)$", line)
        if numbered:
            items = []
            while index < len(lines):
                match = re.match(r"^\s*\d+\.\s+(.*)$", lines[index])
                if not match:
                    break
                items.append(f"<li>{inline(match.group(1))}</li>")
                index += 1
            output.append("<ol>" + "".join(items) + "</ol>")
            continue

        paragraph = [stripped]
        index += 1
        while index < len(lines):
            candidate = lines[index].strip()
            if (
                not candidate
                or candidate.startswith(("```", ">", "|", "![", "<!--"))
                or re.match(r"^(#{1,6})\s+", candidate)
                or re.match(r"^\s*[-*]\s+", candidate)
                or re.match(r"^\s*\d+\.\s+", candidate)
                or re.match(r"^\s*([-*_])\s*\1\s*\1[\s\-*_]*$", candidate)
            ):
                break
            paragraph.append(candidate)
            index += 1
        output.append(f"<p>{inline(' '.join(paragraph))}</p>")

    body = "\n".join(output)
    banner = (
        '<div class="doc-banner">'
        "<span>Sift · assignment submission</span>"
        "<span>Make AI Solve Something Real · 2026</span>"
        "</div>"
    )
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<title>Sift — submission</title>"
        f"<style>{CSS}</style></head><body>{banner}{body}</body></html>"
    )


def find_chrome() -> str:
    candidates = [
        os.getenv("CHROME_BIN", ""),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        shutil.which("google-chrome") or "",
        shutil.which("chromium") or "",
        shutil.which("chromium-browser") or "",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise SystemExit(
        "Chrome was not found. Install Google Chrome or set CHROME_BIN, "
        "then run this script again."
    )


def main() -> int:
    if not SOURCE.exists():
        raise SystemExit(f"Missing source: {SOURCE}")

    TEMP_HTML.write_text(render(SOURCE.read_text(encoding="utf-8")), encoding="utf-8")
    chrome = find_chrome()
    command = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--no-pdf-header-footer",
        "--run-all-compositor-stages-before-draw",
        "--virtual-time-budget=3000",
        f"--print-to-pdf={OUTPUT}",
        TEMP_HTML.as_uri(),
    ]
    try:
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    finally:
        TEMP_HTML.unlink(missing_ok=True)

    if not OUTPUT.exists():
        raise SystemExit("Chrome did not create a PDF.")
    print(f"Wrote {OUTPUT.relative_to(DOCS.parent)} ({OUTPUT.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
