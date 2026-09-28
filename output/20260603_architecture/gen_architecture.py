#!/usr/bin/env python3
"""K3 workflow architecture, generated with the locally installed drawer skill."""
import os
import sys
import shutil
import subprocess
from pathlib import Path

OUT = Path(__file__).resolve().parent
# Portable discovery: override via env, otherwise use the user's local Pi skill.
SKILL = Path(os.environ.get("ARCHITECTURE_DRAWER_HOME", str(Path.home() / ".pi/agent/skills/architecture-drawer")))
sys.path.insert(0, str(SKILL / "scripts"))
from svg_utils import SVGDrawer, save_svg, rasterize_svg
from design_brief import DesignBrief, ColorSpec
from evaluator import evaluate_svg, auto_refine
from semantic_qa import run_semantic_qa

# Step 1: single source of truth for the design brief.
W, H = 1280, 800
MARGIN, GUTTER = 40, 60
COL_W = (W - 2 * MARGIN - 2 * GUTTER) / 3
ROW_H, TOP = 170, 160
F_TITLE, F_NODE, F_BODY, F_NOTE = 24, 18, 14, 12
INK, SUB, EDGE = "#000000", "#333333", "#4D4D4D"
BLUE = ("#D1E6F1", "#0072B2")
AMBER = ("#FAEED1", "#E69F00")
GREEN = ("#D1EEE6", "#009E73")
PALETTE = {"main": BLUE, "daemon": AMBER, "worker": GREEN, "storage": AMBER, "pipeline": GREEN}
BRIEF = DesignBrief(scheme="S2", layout="node", flow="left-right",
    palette_role={key: ColorSpec(*color) for key, color in PALETTE.items()},
    flow_chain=("main", "daemon", "worker"))

D = SVGDrawer(W, H, bg="#FFFFFF")
D.arrow_head("arrowhead", EDGE)
D.text(W / 2, 40, "K3 Agent Workflow", font_size=F_TITLE, weight="bold", fill=INK)
D.text(W / 2, 78, "Asynchronous orchestration for Pi — hardware-free MVP", font_size=F_BODY, fill=SUB)

X = [MARGIN + i * (COL_W + GUTTER) for i in range(3)]

def card(nid, col, y, title, lines, h=ROW_H):
    x = X[col]
    fill, stroke = PALETTE[nid]
    D.rect(x, y, COL_W, h, rx=10, fill=fill, stroke=stroke, node_id=nid)
    D.text(x + COL_W / 2, y + 34, title, font_size=F_NODE, weight="bold", fill=INK)
    for i, line in enumerate(lines):
        D.text(x + COL_W / 2, y + 76 + i * 26, line, font_size=F_BODY, fill=SUB)

card("main", 0, TOP, "Pi Main Agent / Astra", ["workflow extension", "Submit • inspect • cancel", "Completion → follow-up"])
card("daemon", 1, TOP, "workflowd / TypeScript", ["Authenticated local HTTP", "Queue • budget • idempotency", "Validate results before success"])
card("worker", 2, TOP, "Build / Test Workers", ["Default: deterministic demo", "Optional: Pi RPC / Luna", "Fresh process for each RPC job"])
LOWER = TOP + ROW_H + 150
card("storage", 1, LOWER, "Durable state & artifacts", ["SQLite tasks + replayable events", "Source snapshot + SHA-256", "Stage results + RPC logs"])
card("pipeline", 2, LOWER, "Build → test", ["Test waits for validated artifact", "One slot per stage", "SIMULATED results only"])

D.connect("main", "right", "daemon", "left", stroke=EDGE, marker_end="arrowhead")
D.connect("daemon", "right", "worker", "left", stroke=EDGE, marker_end="arrowhead")
D.connect("daemon", "bottom", "storage", "top", stroke=EDGE, marker_end="arrowhead")
D.connect("worker", "bottom", "pipeline", "top", stroke=EDGE, marker_end="arrowhead")
# Completion return uses a separate parallel port in the inter-node corridor.
D.connect("daemon", "left", "main", "right", stroke=EDGE, dashed=True, marker_end="arrowhead")
D.text((X[0] + COL_W + X[1]) / 2, TOP - 24, "HTTP", font_size=F_NOTE, fill=SUB)
D.text((X[1] + COL_W + X[2]) / 2, TOP - 24, "JSONL", font_size=F_NOTE, fill=SUB)
D.text(X[0] + COL_W / 2, LOWER + 30, "Completion channel: SSE", font_size=F_NODE, weight="bold", fill=INK)
D.text(X[0] + COL_W / 2, LOWER + 72, "Replay cursor • reconnect • deduplicate", font_size=F_NOTE, fill=SUB)
D.text(X[0] + COL_W / 2, LOWER + 108, "Auto continuation is OFF by default", font_size=F_NOTE, fill=SUB)
D.text(W / 2, H - 76, "Solid: task / state flow     Dashed: asynchronous completion notification", font_size=F_BODY, fill=SUB)
D.text(W / 2, H - 38, "Future board adapter: SSH / serial + explicit authorization + safe recovery. NOT implemented in this MVP.", font_size=F_NOTE, fill=SUB)

D.check_collisions()
score, report = evaluate_svg(D)
if any("[FAIL]" in str(line) for line in report):
    score, report, fixes = auto_refine(D, max_iter=1)
    print("Auto-refine:", fixes)
qa = run_semantic_qa(D, expected_size=(W, H), brief=BRIEF)
lines = [f"Score: {score}", *map(str, report), *map(str, qa.report())]
print("\n".join(lines))
(OUT / "validation.txt").write_text("\n".join(lines) + "\n")
save_svg(D.render(), OUT / "architecture.svg")
if shutil.which("rsvg-convert"):
    rasterize_svg(OUT / "architecture.svg", OUT / "architecture.png", W)
elif shutil.which("convert"):
    subprocess.run(["convert", "-background", "white", "-font", "DejaVu-Sans", str(OUT / "architecture.svg"), str(OUT / "architecture.png")], check=True)
else:
    print("PNG export unavailable: no rasterizer installed (SVG is the README artifact)")
try:
    from svg2pptx import svg_to_pptx
    svg_to_pptx(OUT / "architecture.svg", OUT / "architecture.pptx")
except ModuleNotFoundError as error:
    print(f"Optional editable PPTX unavailable: {error}; no dependencies installed")
BRIEF.write(OUT / "brief.json")
DOCS = OUT.parent.parent / "docs"
DOCS.mkdir(exist_ok=True)
shutil.copyfile(OUT / "architecture.svg", DOCS / "architecture.svg")
if score < 80 or qa.has_fail or any("[FAIL]" in str(line) for line in report):
    raise SystemExit("Diagram needs correction; see validation.txt")
