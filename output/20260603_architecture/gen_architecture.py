#!/usr/bin/env python3
"""Actual Pi RPC-demo cooperation and wire sequence; no hardware implementation implied."""
import os
import sys
import shutil
import subprocess
from pathlib import Path

OUT = Path(__file__).resolve().parent
SKILL = Path(os.environ.get("ARCHITECTURE_DRAWER_HOME", str(Path.home() / ".pi/agent/skills/architecture-drawer")))
sys.path.insert(0, str(SKILL / "scripts"))
from svg_utils import SVGDrawer, save_svg, rasterize_svg
from design_brief import DesignBrief, ColorSpec
from evaluator import evaluate_svg, auto_refine
from semantic_qa import run_semantic_qa

# Design brief: S2 role palette, common typography, calculated three/five-column grids.
W, H = 1440, 1240
MARGIN, CARD_W = 40, 340
GAP = (W - 2 * MARGIN - 3 * CARD_W) / 2
X = [MARGIN + i * (CARD_W + GAP) for i in range(3)]
TITLE, HEADER, BODY, NOTE = 28, 22, 18, 14
INK, SUB, EDGE = "#000000", "#333333", "#4D4D4D"
BLUE = ("#D1E6F1", "#0072B2")
AMBER = ("#FAEED1", "#E69F00")
GREEN = ("#D1EEE6", "#009E73")
FONT = "Arial, sans-serif"


def text(d, x, y, value, size=BODY, bold=False, anchor="middle"):
    d.text(x, y, value, font_size=size, font_family=FONT, fill=INK if bold else SUB,
           weight="bold" if bold else "normal", anchor=anchor)


def box(d, nid, x, y, w, h, title, lines, palette, dashed=False):
    d.rect(x, y, w, h, rx=10, fill=palette[0], stroke=palette[1], node_id=nid, dashed=dashed)
    text(d, x + w / 2, y + 32, title, HEADER, True)
    for i, value in enumerate(lines):
        if value:
            text(d, x + w / 2, y + 72 + i * 29, value, NOTE)


def arrow(d, start, end, label, label_xy, dashed=False):
    # Deliberately use distinct physical ports; every endpoint is on a registered box border.
    d.line(*start, *end, stroke=EDGE, stroke_width=1.5, marker_end="arrowhead",
           register_edge=True, dashed=dashed)
    text(d, *label_xy, label, NOTE)


def architecture():
    palette = {"main": BLUE, "extension": BLUE, "daemon": AMBER, "build": GREEN,
               "test": GREEN, "storage": AMBER, "future": GREEN}
    brief = DesignBrief(scheme="S2", layout="node", flow="none",
        palette_role={k: ColorSpec(*v) for k, v in palette.items()}, flow_chain=())
    d = SVGDrawer(W, H, bg="#FFFFFF")
    d.arrow_head("arrowhead", EDGE)
    text(d, W / 2, 40, "Three agents, one deterministic coordinator", TITLE, True)
    text(d, W / 2, 82, "RPC demo topology • all processes run on the host • results are SIMULATED", BODY)
    l, c, r = X
    box(d, "main", l, 140, CARD_W, 180, "Main Agent · Astra", [
        "Propose / edit assembly candidates", "Read results; decide the next experiment", "Interactive Pi session"], BLUE)
    box(d, "extension", l, 460, CARD_W, 220, "workflow extension", [
        "INSIDE the main Pi process", "workflow_submit / result / status / cancel", "HTTP client + background SSE listener", "Not a fourth agent"], BLUE)
    box(d, "daemon", c, 200, CARD_W, 580, "workflowd", [
        "TypeScript service — NOT an LLM", "", "Accept + snapshot candidate bytes", "Persist task and event in SQLite", "", "Dispatch BUILD (one slot)", "Validate artifact + source SHA-256", "Dispatch TEST (one slot)", "Validate metrics + artifact identity", "", "Persist terminal state → publish event", "Never trust 'done' text alone"], AMBER)
    box(d, "build", r, 160, CARD_W, 230, "Build Agent · Luna", [
        "Independent Pi RPC child / build job", "Reads candidate.s", "Writes artifact.txt + result.json", "Demo only: NO compiler"], GREEN)
    box(d, "test", r, 540, CARD_W, 230, "Test Agent · Luna", [
        "Independent Pi RPC child / test job", "Reads registered artifact path + hash", "Writes test/result.json", "Demo only: synthetic samples"], GREEN)
    box(d, "storage", c, 930, CARD_W, 170, "Durable experiment data", [
        "SQLite: states, budget, replayable events", "Files: source, artifacts, logs, results", "Worker-to-worker handoff = path + hash"], AMBER)
    box(d, "future", r, 930, CARD_W, 170, "FUTURE ONLY · k3-auto", [
        "integrations/k3-auto submodule", "Skills → SSH / serial → board benchmark", "Not loaded; no board access today"], GREEN, dashed="2,5")

    # Same-process tool calls and asynchronous custom messages are separate directions.
    arrow(d, (l + 65, 320), (l + 65, 460), "tool call ↓", (l + 165, 362))
    arrow(d, (l + CARD_W - 45, 460), (l + CARD_W - 45, 320), "↑ sendMessage", (l + 165, 414), True)
    # Three independent contracts between extension and daemon.
    mid = (l + CARD_W + c) / 2
    arrow(d, (l + CARD_W, 505), (c, 505), "HTTP POST + JSON", (mid, 485))
    arrow(d, (c, 570), (l + CARD_W, 570), "HTTP 202 + exp ID", (mid, 549), True)
    arrow(d, (c, 645), (l + CARD_W, 645), "SSE: event JSON", (mid, 624), True)
    # Two distinct RPC child processes; same protocol, independent streams.
    mid = (c + CARD_W + r) / 2
    for top in [160, 540]:
        arrow(d, (c + CARD_W, top + 90), (r, top + 90), "RPC stdin: prompt", (mid, top + 69))
        arrow(d, (r, top + 174), (c + CARD_W, top + 174), "stdout: JSONL", (mid, top + 152), True)
    arrow(d, (c + CARD_W / 2, 780), (c + CARD_W / 2, 930), "state + files", (c + 85, 855))
    arrow(d, (r + CARD_W / 2, 770), (r + CARD_W / 2, 930), "future adapter", (r + 83, 855), "2,5")

    # Left lower panel is explanation, not a fictitious component.
    text(d, l, 775, "No direct agent-to-agent chat", HEADER, True, "start")
    for i, value in enumerate([
        "Astra delegates through registered tools.", "Only workflowd starts worker processes.",
        "Build never calls Test directly.", "Test starts only after artifact validation.",
        "Fresh Pi context for each RPC stage.", "Default mode replaces both Luna agents",
        "with deterministic in-process demo workers.",
    ]):
        text(d, l, 815 + i * 31, value, NOTE, anchor="start")
    text(d, W / 2, 1145, "Solid = request / state flow     Dashed = response / event     Dotted = future integration", NOTE)
    text(d, W / 2, 1182, "Control: tools + HTTP/SSE + Pi RPC     Data: local files + SHA-256     Persistence: SQLite", BODY)
    return d, brief


def sequence():
    width, height = 1440, 1540
    margin, lane_w = 40, 240
    gap = (width - 2 * margin - 5 * lane_w) / 4
    xs = [margin + lane_w / 2 + i * (lane_w + gap) for i in range(5)]
    actors = [("s-main", "Astra / Pi", BLUE), ("s-extension", "Pi extension", BLUE),
              ("s-daemon", "workflowd", AMBER), ("s-build", "Build · Luna", GREEN), ("s-test", "Test · Luna", GREEN)]
    brief = DesignBrief(scheme="S2", layout="node", flow="none",
        palette_role={nid: ColorSpec(*color) for nid, _, color in actors}, flow_chain=())
    d = SVGDrawer(width, height, bg="#FFFFFF")
    d.arrow_head("arrowhead", EDGE)
    text(d, width / 2, 40, "One experiment: request → validation → asynchronous result", TITLE, True)
    text(d, width / 2, 80, "Five participants • build and test never communicate directly • RPC-demo mode", BODY)
    for i, (nid, title, color) in enumerate(actors):
        d.rect(xs[i] - lane_w / 2, 120, lane_w, 65, rx=8, fill=color[0], stroke=color[1], node_id=nid)
        text(d, xs[i], 151, title, HEADER, True)
    # Fixed-pitch rows keep message labels clear of lifelines. Validation rows have real visible cards.
    rows = [
        (1, 2, "0  GET events?after=N (SSE open)", False),
        (0, 1, "1  workflow_submit(candidate, key)", False),
        (1, 2, "2  HTTP POST /experiments", False),
        (2, 2, "Snapshot + SQLite commit", False),
        (2, 1, "3  HTTP 202 {id, queued}", True),
        (1, 0, "4  Tool result: experiment ID", True),
        (2, 3, "5  stdin JSONL: prompt", False),
        (3, 2, "6  response: accepted only", True),
        (3, 2, "7  message_end + agent_settled", True),
        (2, 2, "Check build files + SHA-256", False),
        (2, 4, "8  stdin JSONL: prompt(artifact path + hash)", False),
        (4, 2, "9  response: accepted only", True),
        (4, 2, "10  message_end + agent_settled", True),
        (2, 2, "Validate test; persist result", False),
        (2, 1, "11  SSE: experiment.succeeded", True),
        (1, 0, "12  sendMessage / followUp", True),
        (0, 1, "13  Pi transcript message_end", True),
    ]
    y0, pitch = 235, 70
    validation_rows = {i for i, row in enumerate(rows) if row[0] == row[1]}
    notes = [
        (4, 480, "202 is not completion"),
        (4, 770, "Build writes local files"),
        (4, 798, "artifact.txt + result.json"),
        (0, 1110, "Test writes local result.json"),
        (0, 1138, "Artifacts stay on disk"),
    ]
    for i, (src, dst, label, dashed) in enumerate(rows):
        y = y0 + i * pitch
        # Dashed lane guides are intentionally interrupted around all label bands.
        for lane, x in enumerate(xs):
            if lane == 2 and i in validation_rows:
                continue
            if any(note_lane == lane and note_y + 12 >= y + 10 and note_y - 12 <= y + pitch - 33
                   for note_lane, note_y, _ in notes):
                continue
            d.line(x, y + 10, x, y + pitch - 33, stroke="#CCCCCC", dashed=True, role="decoration")
        if src == dst:
            d.rect(xs[src] - 135, y - 23, 270, 46, rx=7, fill=AMBER[0], stroke=AMBER[1], node_id=f"gate-{i}")
            text(d, xs[src], y, label, NOTE, True)
        else:
            for suffix, lane in [("from", src), ("to", dst)]:
                d.circle(xs[lane], y, 3, fill=EDGE, stroke=EDGE, node_id=f"p-{i}-{suffix}", node_kind="junction")
            d.connect(f"p-{i}-from", "right" if src < dst else "left", f"p-{i}-to", "left" if src < dst else "right", stroke=EDGE, dashed=dashed)
            text(d, (xs[src] + xs[dst]) / 2, y - 19, label, NOTE)
    # Free lanes carry the distinctions that are easy to miss in a protocol diagram.
    for lane, y, label in notes:
        # Place annotations in whitespace between lifeline segments, left-aligned.
        text(d, xs[lane] - 125, y, label, NOTE, anchor="start")
    text(d, width / 2, 1460, "Auto OFF: display/store result only. Auto ON: Astra analyzes it and may submit the next candidate.", NOTE)
    text(d, width / 2, 1500, "Any build/validation failure skips Test and emits a terminal failure event instead. No polling by Astra.", NOTE)
    return d, brief


def export(name, d, brief):
    d.check_collisions()
    score, report = evaluate_svg(d)
    if any("[FAIL]" in str(line) for line in report):
        score, report, fixes = auto_refine(d, max_iter=1)
        print(name, "auto-refine:", fixes)
    qa = run_semantic_qa(d, expected_size=(d.width, d.height), brief=brief)
    lines = [f"Score: {score}", *map(str, report), *map(str, qa.report())]
    print(name + "\n" + "\n".join(lines))
    report_path = OUT / ("validation.txt" if name == "architecture" else f"{name}-validation.txt")
    report_path.write_text("\n".join(lines) + "\n")
    save_svg(d.render(), OUT / f"{name}.svg")
    brief.write(OUT / ("brief.json" if name == "architecture" else f"{name}-brief.json"))
    if shutil.which("rsvg-convert"):
        rasterize_svg(OUT / f"{name}.svg", OUT / f"{name}.png", d.width)
    elif shutil.which("convert"):
        subprocess.run(["convert", "-background", "white", "-font", "DejaVu-Sans", str(OUT / f"{name}.svg"), str(OUT / f"{name}.png")], check=True)
    try:
        from svg2pptx import svg_to_pptx
        svg_to_pptx(OUT / f"{name}.svg", OUT / f"{name}.pptx")
    except ModuleNotFoundError as error:
        print(f"Optional PPTX unavailable: {error}; no dependencies installed")
    if score < 80 or qa.has_fail or any("[FAIL]" in str(line) for line in report):
        return False
    docs = OUT.parent.parent / "docs"
    docs.mkdir(exist_ok=True)
    shutil.copyfile(OUT / f"{name}.svg", docs / f"{name}.svg")
    return True


if __name__ == "__main__":
    ok = [export(name, *factory()) for name, factory in [("architecture", architecture), ("sequence", sequence)]]
    if not all(ok):
        raise SystemExit("Diagram needs correction; see validation reports")
