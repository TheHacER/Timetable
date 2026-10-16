#!/usr/bin/env python3
"""Turn a Wixams Academy SIMS-style timetable PDF into printable A4 PDFs.

Outputs (in --out):
  <Name> Timetable.pdf  A4 landscape, 2 pages (Week A / Week B), print double-sided, flip on SHORT edge
  <Name> Cards.pdf      A4 portrait, 2 pages (fronts / backs) of 10 cut-out 91x57 mm cards,
                        print double-sided, flip on LONG edge

Usage:  python timetable.py --pdf "/path/to/Timetable.pdf" [--out ./output] [--logo ./wixams-logo.png]
"""
from __future__ import annotations

import argparse
import colorsys
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# ============================================================================
# CONFIG - everything you are likely to want to change lives in this block
# ============================================================================

# Bell schedule. kind "lesson" gets a timetable cell, kind "gap" gets a grey pill.
SCHEDULE = [
    {"id": "Reg",   "label": "Reg",   "start": "08:40", "end": "09:10", "kind": "lesson"},
    {"id": "P1",    "label": "P1",    "start": "09:10", "end": "10:10", "kind": "lesson"},
    {"id": "P2",    "label": "P2",    "start": "10:10", "end": "11:10", "kind": "lesson"},
    {"id": "Break", "label": "Break", "start": "11:10", "end": "11:30", "kind": "gap"},
    {"id": "P3",    "label": "P3",    "start": "11:30", "end": "12:30", "kind": "lesson"},
    {"id": "P4",    "label": "P4",    "start": "12:30", "end": "13:30", "kind": "lesson"},
    {"id": "Lunch", "label": "Lunch", "start": "13:30", "end": "14:20", "kind": "gap"},
    {"id": "P5",    "label": "P5",    "start": "14:20", "end": "15:20", "kind": "lesson"},
]

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]
DAY_FULL = {"Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday", "Thu": "Thursday", "Fri": "Friday"}

# Kit rules ---------------------------------------------------------------
KIT_ALWAYS = ["Pencil case", "Water bottle", "Lunch"]
# Subjects that do NOT get a "<Subject> workbook" (matched as whole words, case-insensitive).
KIT_NO_WORKBOOK = ["pe", "physical education", "registration", "tutor"]
# Extra items. A rule fires if any of its "subjects" appears (as whole words) in a lesson's subject.
KIT_RULES = [
    {"subjects": ["maths", "mathematics"], "items": ["Calculator + protractor"]},
    {"subjects": ["pe", "physical education"], "items": ["PE kit"]},
    {"subjects": ["science", "design technology", "design tech", "dt",
                  "food technology", "food tech", "food"], "items": ["Hair tied back"]},
    {"subjects": ["food technology", "food tech", "food"], "items": ["Food ingredients"]},
    {"subjects": ["re", "religious education", "religious studies"], "items": ["Tie"]},
]

# Display names for subjects as they appear in the PDF (matched case-insensitively on the whole subject).
SUBJECT_ALIASES = {"mathematics": "Maths", "religious studies": "RE", "religious education": "RE",
                   "physical education": "PE", "design technology": "DT"}

# Block colours -----------------------------------------------------------
SPORTS = "SP"  # internal key for the Sports/Hall block
BLOCK_COLOURS = {
    "D":    {"fill": "#ffe1d0", "chip": "#8c491a", "border": None},
    "C":    {"fill": "#e1eecc", "chip": "#56633f", "border": None},
    "B":    {"fill": "#dcd3c4", "chip": "#474238", "border": None},
    SPORTS: {"fill": "#ebddc5", "chip": "#2e2b25", "border": "#645c50"},
}
BLOCK_LABEL = {SPORTS: "Sports/Hall"}       # legend label (default: "<X> Block")
BLOCK_SHORT = {SPORTS: "Sports"}            # used in "MOVE ->" tags
LEGEND_BLOCKS = ["C", "D", "B", SPORTS]     # always shown; extra blocks present are appended

PALETTE = {
    "ground": "#f5ead8", "surface": "#ebddc5", "text": "#201e1d", "accent": "#c67139",
    "sage": "#7a8a5e", "green": "#3d472b", "pill": "#eee7db", "grey": "#645c50", "grey2": "#474238",
}

# Card geometry (mm for the card, px for the contents) -----------------------
CARD_W_MM, CARD_H_MM = 91, 57
CARD_COLS, CARD_ROWS = 2, 5
CARD_ROW_PX, CARD_GAP_ROW_PX, CARD_ROW_GAP_PX = 20, 14, 3   # lesson row, break/lunch pill, gap between
CARD_STRIP_W_PX = 58
CARD_STRIP_PAD_PX = 6

STAFF_URL = "https://wixamsacademy.co.uk/Teaching-Staff/"
STAFF_CACHE = BASE_DIR / "staff_cache.json"
STAFF_OVERRIDES = BASE_DIR / "staff_overrides.json"
STAFF_CACHE_MAX_AGE_DAYS = 7
FONTS_DIR = BASE_DIR / "fonts"
TEMPLATES_DIR = BASE_DIR / "templates"
FONT_CSS_URL = "https://fonts.googleapis.com/css2?family=Caprasimo&family=Figtree:wght@300;400;500"
FONT_FILES = ["Caprasimo-400.ttf", "Figtree-300.ttf", "Figtree-400.ttf", "Figtree-500.ttf"]

# ============================================================================


class TimetableError(Exception):
    """A problem the user can fix; shown without a traceback."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class Lesson:
    slot: str
    subject: str
    room: str            # display form: "D114", "Sports 2", "Hall", "Sports" or ""
    block: str | None    # "D", "C", ..., SPORTS, or None if unknown
    teachers: list[str]  # raw codes
    start: str
    end: str
    move: bool = False


@dataclass
class Timetable:
    student: str
    tutor_group: str
    tutor_room: str
    tutor_code: str
    weeks: dict[str, dict[str, list[Lesson]]] = field(default_factory=dict)  # week -> day -> lessons


def lessons_slots():
    return [s for s in SCHEDULE if s["kind"] == "lesson"]


# ---------------------------------------------------------------------------
# Room / cell parsing
# ---------------------------------------------------------------------------
ROOM_RE = re.compile(r"(?i)(?<![A-Za-z0-9])(?:[A-Za-z]\d{3}[A-Za-z]?|spo(?:r|rts?)?\s*\d|assem\w*|hall)(?![A-Za-z0-9])")
TEACHER_TAIL_RE = re.compile(r"(?:^|\s)([A-Z][A-Z0-9]{2}(?:\s*,\s*[A-Z][A-Z0-9]{2})*)\s*$")
CLASS_CODE_RE = re.compile(r"^\d[A-Za-z]*/\S*$")        # 7F/Sc, 7AFH/Pe2, or 7F/ (suffix wrapped to next line)
SUBJECT_SUFFIX_RE = re.compile(r"\s*(?:KS\d+:?|\([^)]*\):?)\s*$")  # "KS3:", "(KStg3):", "(Gen):"
TIME_RE = re.compile(r"^\d{1,2}[:.]\d{2}")


def is_pe(subject: str) -> bool:
    return bool(re.search(r"(?i)\b(pe|physical education)\b", subject or ""))


def normalise_room(raw: str, subject: str = "") -> tuple[str, str | None]:
    """Return (display room, block key)."""
    r = (raw or "").strip()
    if not r:
        return ("Sports", SPORTS) if is_pe(subject) else ("", None)
    m = re.fullmatch(r"(?i)spo(?:r|rts?)?\s*(\d)", r)
    if m:
        return f"Sports {m.group(1)}", SPORTS
    if re.fullmatch(r"(?i)assem\w*|hall", r):
        return "Hall", SPORTS
    if re.fullmatch(r"(?i)spo\w*", r):
        return "Sports", SPORTS
    m = re.fullmatch(r"([A-Za-z])\s?(\d{2,3}[A-Za-z]?)", r)
    if m:
        return m.group(1).upper() + m.group(2).upper(), m.group(1).upper()
    return r, r[0].upper()


def parse_cell(text: str, slot: str) -> tuple[str, str, list[str]]:
    """Split one timetable cell into (subject, raw room, teacher codes)."""
    t = re.sub(r"\s+", " ", (text or "").replace("\n", " ")).strip()
    # SIMS cells read "<Subject> KS3: Year 7: <class code>" / room / teachers.
    # Drop "Year 7: <class code>" (the code may wrap: "7F/" + "Ma"), or "Year 7: 7 Franklin" for registration.
    ym = re.search(r"Year\s+\d+:", t)
    if ym:
        toks = t[ym.end():].split()
        n = 0
        if slot == "Reg":
            n = 2 if len(toks) >= 2 else 0
        elif toks and CLASS_CODE_RE.match(toks[0]):
            n = 2 if toks[0].endswith("/") and len(toks) > 1 else 1
        t = (t[: ym.start()] + " " + " ".join(toks[n:])).strip()
    teachers: list[str] = []
    m = TEACHER_TAIL_RE.search(t)
    if m and m.start() > 0:
        teachers = [c.strip() for c in m.group(1).split(",")]
        t = t[: m.start()].strip()
    rooms = list(ROOM_RE.finditer(t))
    room = ""
    if rooms:
        last = rooms[-1]
        room = last.group(0)
        t = (t[: last.start()] + " " + t[last.end():]).strip()
    t = SUBJECT_SUFFIX_RE.sub("", t)
    subject = re.sub(r"\s+", " ", t).strip(" -–,")
    subject = SUBJECT_ALIASES.get(subject.lower(), subject)
    if slot == "Reg":
        subject = "Tutor"
    return subject, room, teachers


def annotate_moves(lessons: list[Lesson]) -> int:
    """Flag lessons whose block differs from the previous one that day; return the count."""
    prev = None
    n = 0
    for ls in lessons:
        ls.move = False
        if ls.block is None:
            continue
        if prev is not None and ls.block != prev:
            ls.move = True
            n += 1
        prev = ls.block
    return n


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------
DAY_RE = re.compile(r"(?i)^(mon|tue|wed|thu|fri)[a-z]*\.?$")
SLOT_WORD_RE = re.compile(r"(?i)^(reg|registration|p[1-5])$")
SLOT_TEXT_RE = re.compile(r"(?is)^\s*(reg(?:istration)?|p\s?([1-5])|period\s*([1-5]))(?![a-z])")
DAY_TEXT_RE = re.compile(r"(?is)^\s*(mon|tue|wed|thu|fri)[a-z]*")
HEADER_RE = re.compile(r"Timetable\s+for\s+(.+?)\s*,\s*(.+?)\s*,\s*Week\s*([AB])\b", re.I)


def _day_of(text: str) -> str | None:
    m = DAY_TEXT_RE.match(text or "")
    return m.group(1).capitalize() if m else None


def _slot_of(text: str) -> str | None:
    m = SLOT_TEXT_RE.match(text or "")
    if not m:
        return None
    if m.group(1).lower().startswith("reg"):
        return "Reg"
    return "P" + (m.group(2) or m.group(3))


def _line_text(words, top, tol=3):
    line = sorted((w for w in words if abs(w["top"] - top) <= tol), key=lambda w: w["x0"])
    return " ".join(w["text"] for w in line)


def find_week_headers(pdf) -> list[dict]:
    """Locate each 'Timetable for <Name> , <Group> , Week X' header (falls back to a bare 'Week X')."""
    found = []
    for pi, page in enumerate(pdf.pages):
        words = page.extract_words(x_tolerance=2, y_tolerance=2)
        for w in words:
            if w["text"].lower() != "timetable":
                continue
            line = _line_text(words, w["top"])
            m = HEADER_RE.search(line)
            if m:
                found.append({"page": pi, "top": w["top"], "x0": w["x0"], "week": m.group(3).upper(),
                              "name": m.group(1).strip(), "group": m.group(2).strip()})
    if found:
        return found
    for pi, page in enumerate(pdf.pages):  # fallback: bare "Week A"
        words = page.extract_words(x_tolerance=2, y_tolerance=2)
        for i, w in enumerate(words):
            t = w["text"]
            wk = None
            if re.fullmatch(r"(?i)week[AB]", t):
                wk = t[-1].upper()
            elif t.lower() == "week" and i + 1 < len(words) and words[i + 1]["text"] in ("A", "B") \
                    and abs(words[i + 1]["top"] - w["top"]) < 3:
                wk = words[i + 1]["text"]
            if wk:
                found.append({"page": pi, "top": w["top"], "x0": w["x0"], "week": wk, "name": "", "group": ""})
    return found


def _regions(pdf, headers) -> list[tuple[dict, tuple]]:
    """Give each header a bbox: down to the next header on its page, split left/right if side by side."""
    out = []
    for h in headers:
        page = pdf.pages[h["page"]]
        same = [o for o in headers if o["page"] == h["page"] and o is not h]
        x0, x1, top, bottom = 0, page.width, max(h["top"] - 2, 0), page.height
        for o in same:
            if abs(o["top"] - h["top"]) < 10:  # side by side
                mid = (o["x0"] + h["x0"]) / 2
                if o["x0"] > h["x0"]:
                    x1 = min(x1, mid)
                else:
                    x0 = max(x0, mid)
            elif o["top"] > h["top"]:
                bottom = min(bottom, o["top"] - 2)
        out.append((h, (x0, top, x1, bottom)))
    return out


def grid_from_table(rows: list[list[str | None]]) -> dict | None:
    """Map a ruled table (list of rows of cell text) to {(day, slot): text}."""
    rows = [[(c or "").strip() for c in r] for r in rows]
    day_pos = [(ri, ci, _day_of(c)) for ri, r in enumerate(rows) for ci, c in enumerate(r) if c and _day_of(c)]
    slot_pos = [(ri, ci, _slot_of(c)) for ri, r in enumerate(rows) for ci, c in enumerate(r) if c and _slot_of(c)]
    if len({d for *_, d in day_pos}) < 5 or len({s for *_, s in slot_pos}) < 6:
        return None
    days_are_rows = len({ci for _, ci, _ in day_pos}) < len({ri for ri, _, _ in day_pos})
    grid = {}
    for dr, dc, day in day_pos:
        for sr, sc, slot in slot_pos:
            r, c = (dr, sc) if days_are_rows else (sr, dc)
            if r < len(rows) and c < len(rows[r]):
                grid[(day, slot)] = rows[r][c]
    return grid


def grid_from_words(words: list[dict]) -> dict | None:
    """Assign words to (day, slot) cells using the positions of the day / period header labels."""
    ws = []
    i = 0
    while i < len(words):  # merge "Period" + "1"
        w = words[i]
        if w["text"].lower() == "period" and i + 1 < len(words) and re.fullmatch(r"[1-5]", words[i + 1]["text"]):
            n = words[i + 1]
            ws.append({**w, "text": "P" + n["text"], "x1": n["x1"], "bottom": max(w["bottom"], n["bottom"])})
            i += 2
            continue
        ws.append(w)
        i += 1
    words = ws

    days, slots, extras, header_ids = {}, {}, {}, set()
    for w in words:
        t = w["text"].strip(".,:")
        if DAY_RE.match(t) and _day_of(t) not in days:
            days[_day_of(t)] = w
            header_ids.add(id(w))
        elif SLOT_WORD_RE.match(t) and _slot_of(t) not in slots:
            slots[_slot_of(t)] = w
            header_ids.add(id(w))
        elif t.lower() in ("break", "lunch") and t.capitalize() not in extras:
            extras[t.capitalize()] = w
            header_ids.add(id(w))
    if len(days) < 5 or len(slots) < 6:
        return None

    def cx(w): return (w["x0"] + w["x1"]) / 2
    def cy(w): return (w["top"] + w["bottom"]) / 2
    spread_y = max(cy(w) for w in days.values()) - min(cy(w) for w in days.values())
    spread_x = max(cx(w) for w in days.values()) - min(cx(w) for w in days.values())
    days_are_rows = spread_y > spread_x
    if days_are_rows:
        row_ref, col_ref = days, {**slots, **extras}
    else:
        row_ref, col_ref = {**slots, **extras}, days
    header_top = min(w["top"] for w in col_ref.values())

    cells = defaultdict(list)
    for w in words:
        if id(w) in header_ids or w["top"] < header_top - 1 or TIME_RE.match(w["text"]) \
                or w["text"] in ("-", "–", "—"):
            continue
        r = min(row_ref, key=lambda k: abs(cy(row_ref[k]) - cy(w)))
        c = min(col_ref, key=lambda k: abs(cx(col_ref[k]) - cx(w)))
        cells[(r, c)].append(w)

    grid = {}
    for (r, c), ws_ in cells.items():
        day, slot = (r, c) if days_are_rows else (c, r)
        if day not in days or slot not in slots:
            continue  # words that fell into a Break / Lunch column
        ws_.sort(key=lambda w: (round(w["top"] / 3), w["x0"]))
        grid[(day, slot)] = " ".join(w["text"] for w in ws_)
    return grid


def extract_week_grid(pdf, header, bbox) -> dict:
    page = pdf.pages[header["page"]].crop(tuple(bbox))
    grid = None
    try:
        for tbl in page.find_tables():
            grid = grid_from_table(tbl.extract())
            if grid:
                break
    except Exception:  # table detection is best-effort; fall back to word positions
        grid = None
    if not grid:
        grid = grid_from_words(page.extract_words(x_tolerance=2, y_tolerance=2))
    if not grid:
        raise TimetableError(
            f"Found the Week {header['week']} heading but could not locate the Mon-Fri x Reg/P1-P5 grid under it. "
            "Run with --inspect to dump the words and positions found in the PDF.")
    return grid


def parse_pdf(path: Path) -> Timetable:
    import pdfplumber
    if not path.exists():
        raise TimetableError(f"Timetable PDF not found: {path}")
    try:
        pdf = pdfplumber.open(str(path))
    except Exception as e:
        raise TimetableError(f"Could not open {path} as a PDF ({e}).") from e
    with pdf:
        headers = find_week_headers(pdf)
        weeks_found = {h["week"] for h in headers}
        if weeks_found != {"A", "B"}:
            raise TimetableError(
                f"{path.name} doesn't look like a timetable: expected 'Timetable for <name> , <group> , Week A' and "
                f"Week B headings, found {sorted(weeks_found) or 'none'}. Run with --inspect to see what is in the PDF.")
        first = {}
        for h in headers:
            first.setdefault(h["week"], h)
        meta = next((h for h in headers if h["name"]), first["A"])
        tt = Timetable(student=meta["name"], tutor_group=meta["group"], tutor_room="", tutor_code="")
        reg_rooms, reg_codes = Counter(), Counter()
        for header, bbox in _regions(pdf, list(first.values())):
            grid = extract_week_grid(pdf, header, bbox)
            missing = [f"{d} {s['id']}" for d in DAYS for s in lessons_slots() if (d, s["id"]) not in grid]
            if len(missing) > 6:
                raise TimetableError(
                    f"Week {header['week']}: the grid is incomplete (missing {', '.join(missing[:8])}...). "
                    "Run with --inspect to see what the parser sees.")
            week = {}
            for d in DAYS:
                day_lessons = []
                for s in lessons_slots():
                    subject, room_raw, teachers = parse_cell(grid.get((d, s["id"]), ""), s["id"])
                    if not subject:
                        subject = "Free"
                    room, block = normalise_room(room_raw, subject)
                    if s["id"] == "Reg":
                        if room:
                            reg_rooms[room] += 1
                        for t in teachers:
                            reg_codes[t] += 1
                    day_lessons.append(Lesson(s["id"], subject, room, block, teachers, s["start"], s["end"]))
                week[d] = day_lessons
            tt.weeks[header["week"]] = week
        tt.tutor_room = reg_rooms.most_common(1)[0][0] if reg_rooms else ""
        tt.tutor_code = reg_codes.most_common(1)[0][0] if reg_codes else ""
    for week in tt.weeks.values():
        for lessons in week.values():
            annotate_moves(lessons)
    return tt


def inspect_pdf(path: Path) -> None:
    """Dump every word with its coordinates, plus detected tables. For fixing the parser to a new layout."""
    import pdfplumber
    if not path.exists():
        raise TimetableError(f"Timetable PDF not found: {path}")
    with pdfplumber.open(str(path)) as pdf:
        for pi, page in enumerate(pdf.pages):
            print(f"===== page {pi + 1}  {page.width:.0f} x {page.height:.0f} pt =====")
            print(page.extract_text() or "(no text)")
            print("--- words: x0 top x1 bottom text")
            for w in page.extract_words(x_tolerance=2, y_tolerance=2):
                print(f"{w['x0']:7.1f} {w['top']:7.1f} {w['x1']:7.1f} {w['bottom']:7.1f}  {w['text']}")
            for ti, t in enumerate(page.find_tables()):
                print(f"--- table {ti}: bbox={tuple(round(v) for v in t.bbox)}")
                for row in t.extract():
                    print("   ", row)


# ---------------------------------------------------------------------------
# Staff names
# ---------------------------------------------------------------------------
STAFF_LINE_RE = re.compile(
    r"^(Mr|Mrs|Miss|Ms|Mx|Dr)\.?\s+((?:[A-Za-z'’\-]+\s+){1,2}[A-Za-z'’\-]+|[A-Za-z'’\-]+)\s+[-–—]\s+\S")


def staff_code(first: str, surname: str) -> str:
    letters = re.sub(r"[^A-Za-z]", "", surname)
    return (first[:1] + letters[:2]).upper()


def parse_staff_html(html: str) -> dict[str, list[str]]:
    from bs4 import BeautifulSoup
    text = BeautifulSoup(html, "html.parser").get_text("\n")
    out: dict[str, list[str]] = defaultdict(list)
    for line in (ln.strip() for ln in text.splitlines()):
        if not line or len(line) > 110:
            continue
        m = STAFF_LINE_RE.match(line)
        if not m:
            continue
        title, name = m.group(1), m.group(2).split()
        if len(name) < 2:
            continue
        code = staff_code(name[0], name[-1])
        disp = f"{title} {' '.join(name[1:])}"
        if disp not in out[code]:
            out[code].append(disp)
    return dict(out)


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


class Staff:
    """code -> 'Title Surname'. Overrides win; ambiguous / unknown codes show the raw code."""

    def __init__(self, by_code: dict[str, list[str]], overrides: dict[str, str]):
        self.by_code, self.overrides = by_code, overrides

    @classmethod
    def load(cls, refresh: bool = False, offline: bool = False) -> "Staff":
        cache = _read_json(STAFF_CACHE, {})
        by_code = cache.get("staff", {})
        fresh = False
        if cache.get("fetched"):
            try:
                age = datetime.now(timezone.utc) - datetime.fromisoformat(cache["fetched"])
                fresh = age.days < STAFF_CACHE_MAX_AGE_DAYS
            except ValueError:
                pass
        if not offline and (refresh or not fresh or not by_code):
            try:
                import requests
                r = requests.get(STAFF_URL, timeout=15, headers={"User-Agent": "timetable-cards/1.0"})
                r.raise_for_status()
                parsed = parse_staff_html(r.text)
                if parsed:
                    by_code = parsed
                    STAFF_CACHE.write_text(json.dumps(
                        {"fetched": datetime.now(timezone.utc).isoformat(), "staff": by_code}, indent=2),
                        encoding="utf-8")
                else:
                    print("  (staff page parsed to nothing - using cached names)")
            except Exception as e:
                print(f"  (couldn't reach the staff page: {e.__class__.__name__} - using cached names and overrides)")
        return cls(by_code, _read_json(STAFF_OVERRIDES, {}))

    def name(self, code: str) -> str:
        if code in self.overrides:
            return self.overrides[code]
        names = self.by_code.get(code, [])
        return names[0] if len(names) == 1 else code

    def display(self, codes: list[str]) -> str:
        return " & ".join(self.name(c) for c in codes)


# ---------------------------------------------------------------------------
# Kit
# ---------------------------------------------------------------------------
def _has(subject: str, needles: list[str]) -> bool:
    s = subject.lower()
    return any(re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", s) for n in needles)


WORKBOOKS_OPTION = "Workbooks"


def kit_options() -> list[str]:
    """Every item that can be left out for a student, in checklist order."""
    opts = list(KIT_ALWAYS) + [WORKBOOKS_OPTION]
    for rule in KIT_RULES:
        for it in rule["items"]:
            if it not in opts:
                opts.append(it)
    return opts


def resolve_leave_out(names: list[str]) -> set[str]:
    """Match user-typed names (case-insensitive) to kit option names; raise on anything unknown."""
    by_lower = {o.lower(): o for o in kit_options()}
    out, bad = set(), []
    for n in names:
        n = n.strip()
        if not n:
            continue
        key = by_lower.get(n.lower())
        (out.add(key) if key else bad.append(n))
    if bad:
        raise TimetableError(f"Unknown kit item(s): {', '.join(bad)}. Choose from: {', '.join(kit_options())}.")
    return out


def kit_for_day(lessons: list[Lesson], leave_out: set[str] | frozenset = frozenset()) -> dict:
    """Return {'always', 'workbooks', 'extras', 'items'} for one day, minus anything in leave_out
    (option names from kit_options(); 'Workbooks' drops every '<Subject> workbook' line)."""
    out = {x.lower() for x in leave_out}
    always = [x for x in KIT_ALWAYS if x.lower() not in out]
    subjects: list[str] = []
    if WORKBOOKS_OPTION.lower() not in out:
        for ls in lessons:
            if ls.slot == "Reg" or ls.subject == "Free" or _has(ls.subject, KIT_NO_WORKBOOK):
                continue
            if ls.subject not in subjects:
                subjects.append(ls.subject)
    extras: list[str] = []
    for ls in lessons:
        for rule in KIT_RULES:
            if _has(ls.subject, rule["subjects"]):
                for it in rule["items"]:
                    if it not in extras and it.lower() not in out:
                        extras.append(it)
    items = always + [f"{x} workbook" for x in subjects] + extras
    return {"always": always, "workbooks": subjects, "extras": extras, "items": items}


# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------
def block_style(block: str | None) -> dict:
    if block is None:
        return {"fill": PALETTE["pill"], "chip": PALETTE["grey"], "border": None}
    if block in BLOCK_COLOURS:
        return BLOCK_COLOURS[block]
    idx = (ord(block[0].upper()) - 65) % 26
    hue = (14 + (idx * 37) % 48) / 360  # warm range: terracotta -> olive
    def hexc(h, s, l):
        r, g, b = colorsys.hls_to_rgb(h, l, s)
        return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))
    style = {"fill": hexc(hue, 0.45, 0.88), "chip": hexc(hue, 0.35, 0.28), "border": None}
    BLOCK_COLOURS[block] = style
    return style


def block_label(block: str) -> str:
    return BLOCK_LABEL.get(block, f"{block} Block")


# ---------------------------------------------------------------------------
# View models + rendering
# ---------------------------------------------------------------------------
def _fmt_room_tag(ls: Lesson) -> str:
    return BLOCK_SHORT.get(ls.block, ls.block or "")


def build_view(tt: Timetable, staff: Staff, show_name_on_cards: bool, logo_path: Path,
               leave_out: set[str] | frozenset = frozenset()) -> dict:
    from PIL import Image
    try:
        with Image.open(logo_path) as im:
            logo_w, logo_h = im.size
    except Exception as e:
        raise TimetableError(f"Could not read the logo image {logo_path} ({e}).") from e

    weeks = {}
    blocks_present: list[str] = []
    for wk, days in tt.weeks.items():
        wd = {}
        for d in DAYS:
            lessons = days[d]
            by_id = {ls.slot: ls for ls in lessons}
            items = []
            for s in SCHEDULE:
                if s["kind"] == "gap":
                    items.append({"gap": True, "label": s["label"], "start": s["start"], "end": s["end"]})
                    continue
                ls = by_id[s["id"]]
                st = block_style(ls.block)
                if ls.block and ls.block not in blocks_present:
                    blocks_present.append(ls.block)
                items.append({
                    "gap": False, "slot": s["id"], "label": s["label"], "start": ls.start, "end": ls.end,
                    "subject": "Registration" if ls.slot == "Reg" else ls.subject,
                    "room": ls.room, "teacher": staff.display(ls.teachers), "move": ls.move,
                    "move_to": _fmt_room_tag(ls), "fill": st["fill"], "chip": st["chip"], "border": st["border"],
                })
            wd[d] = {"name": DAY_FULL[d], "short": d, "items": items,
                     "moves": sum(1 for ls in lessons if ls.move), "kit": kit_for_day(lessons, leave_out)}
        weeks[wk] = wd

    always = [x.lower() for x in KIT_ALWAYS if x.lower() not in {o.lower() for o in leave_out}]
    always_text = (", ".join(always[:-1]) + " + " + always[-1]) if len(always) > 1 else "".join(always)

    legend = [{"label": block_label(b), **block_style(b)} for b in LEGEND_BLOCKS]
    legend += [{"label": block_label(b), **block_style(b)} for b in blocks_present if b not in LEGEND_BLOCKS]

    strip_h = (6 * CARD_ROW_PX + 2 * CARD_GAP_ROW_PX + 7 * CARD_ROW_GAP_PX)
    # logo is rotated -90deg, so its (pre-rotation) width runs along the strip's height
    avail_len = strip_h - 2 * CARD_STRIP_PAD_PX
    avail_thick = CARD_STRIP_W_PX - 2 * CARD_STRIP_PAD_PX
    aspect = logo_w / logo_h
    logo_len = min(avail_len, avail_thick * aspect)
    return {
        "p": PALETTE, "weeks": weeks, "tt": tt, "legend": legend, "logo": logo_path.resolve().as_uri(),
        "show_name": show_name_on_cards, "always_text": always_text, "days": DAYS, "schedule": SCHEDULE,
        "card": {"w": CARD_W_MM, "h": CARD_H_MM, "row": CARD_ROW_PX, "gap": CARD_GAP_ROW_PX,
                 "row_gap": CARD_ROW_GAP_PX, "strip_w": CARD_STRIP_W_PX, "strip_h": strip_h,
                 "logo_len": round(logo_len, 2), "logo_thick": round(logo_len / aspect, 2),
                 "cols": CARD_COLS, "rows": CARD_ROWS, "margin_x": (210 - CARD_COLS * CARD_W_MM) / 2,
                 "margin_y": (297 - CARD_ROWS * CARD_H_MM) / 2},
    }


def _jinja():
    from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
    return Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=select_autoescape(["html", "j2"]),
                       undefined=StrictUndefined)


def render_html(view: dict) -> dict[str, str]:
    env = _jinja()
    return {"timetable": env.get_template("timetable.html.j2").render(**view),
            "cards": env.get_template("cards.html.j2").render(**view)}


def ensure_fonts() -> None:
    missing = [f for f in FONT_FILES if not (FONTS_DIR / f).exists()]
    if not missing:
        return
    print("Downloading fonts (Caprasimo, Figtree) from Google Fonts...")
    FONTS_DIR.mkdir(exist_ok=True)
    try:
        import requests
        css = requests.get(FONT_CSS_URL, timeout=20).text
        for block in re.findall(r"@font-face\s*\{([^}]*)\}", css):
            fam = re.search(r"font-family:\s*'?([^';]+)'?", block)
            wt = re.search(r"font-weight:\s*(\d+)", block)
            url = re.search(r"url\((https://[^)]+)\)", block)
            if not (fam and wt and url):
                continue
            dest = FONTS_DIR / f"{fam.group(1).strip()}-{wt.group(1)}.ttf"
            if dest.exists():
                continue
            r = requests.get(url.group(1), timeout=30)
            r.raise_for_status()
            dest.write_bytes(r.content)
    except Exception as e:
        print(f"  download failed: {e}")
    still = [f for f in FONT_FILES if not (FONTS_DIR / f).exists()]
    if still:
        raise TimetableError(
            f"Missing fonts in {FONTS_DIR}: {', '.join(still)}. They are downloaded automatically on first run, "
            "but that failed (offline?). Download Caprasimo and Figtree from https://fonts.google.com, rename the "
            "static TTFs to the names above and put them in the fonts/ folder.")


# ---- overflow check ---------------------------------------------------------
def check_card_fit(document, expected_cards: int | None = None) -> list[str]:
    """Walk a rendered WeasyPrint document; warn for any .card whose content spills past 57 mm."""
    limit = CARD_H_MM / 25.4 * 96
    warnings, seen = [], 0
    for pi, page in enumerate(document.pages, 1):
        stack = [page._page_box]
        while stack:
            box = stack.pop()
            el = getattr(box, "element", None)
            classes = (el.get("class") or "").split() if el is not None and hasattr(el, "get") else []
            if "card" in classes and getattr(box, "element_tag", "") == "div":
                inner = next((d for d in box.descendants() if getattr(d, "element", None) is not None
                              and "inner" in (d.element.get("class") or "").split()), None)
                if inner is None:
                    continue
                seen += 1
                used = inner.margin_height() + 2 * 0.2  # content + padding + the card's dashed border
                if used > limit + 0.5:
                    mm = used / 96 * 25.4
                    warnings.append(f"page {pi}: card '{el.get('data-label', '?')}' content is {mm:.1f} mm tall "
                                    f"(limit {CARD_H_MM} mm)")
                continue
            stack.extend(getattr(box, "children", []) or [])
    if expected_cards is not None and seen != expected_cards:
        warnings.append(f"overflow check found {seen} cards, expected {expected_cards}")
    return warnings


def render_pdfs(view: dict, out_dir: Path, name: str, debug: bool = False) -> tuple[Path, Path, list[str]]:
    import logging
    from weasyprint import HTML
    logging.getLogger("weasyprint").setLevel(logging.ERROR)
    logging.getLogger("fontTools").setLevel(logging.ERROR)
    ensure_fonts()
    html = render_html(view)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r'[\\/:*?"<>|]+', "", name).strip() or "Student"
    tt_path, cards_path = out_dir / f"{safe} Timetable.pdf", out_dir / f"{safe} Cards.pdf"
    if debug:
        dbg = out_dir / "debug"
        dbg.mkdir(exist_ok=True)
        (dbg / "timetable.html").write_text(html["timetable"], encoding="utf-8")
        (dbg / "cards.html").write_text(html["cards"], encoding="utf-8")
    base = str(BASE_DIR) + os.sep
    HTML(string=html["timetable"], base_url=base).write_pdf(str(tt_path))
    doc = HTML(string=html["cards"], base_url=base).render()
    doc.write_pdf(str(cards_path))
    warnings = check_card_fit(doc, expected_cards=2 * CARD_COLS * CARD_ROWS)
    return tt_path, cards_path, warnings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def ask(prompt: str, prefill: str = "") -> str:
    """Prompt with an editable pre-filled suggestion (needs a terminal); Enter on an empty answer accepts it."""
    if prefill and sys.stdin.isatty():
        try:
            import readline
            readline.set_startup_hook(lambda: readline.insert_text(prefill))
            try:
                return input(prompt).strip() or prefill
            finally:
                readline.set_startup_hook()
        except ImportError:
            pass
    s = input(f"{prompt}[{prefill}] " if prefill else prompt).strip()
    return s or prefill


def yes_no(prompt: str, default: bool) -> bool:
    ans = input(prompt).strip().lower()
    return default if not ans else ans.startswith("y")


def ask_leave_out() -> set[str]:
    opts = kit_options()
    print("\nKit list items (all are included by default):")
    for i, o in enumerate(opts, 1):
        print(f"  {i:>2}. {o}")
    while True:
        raw = input("Leave any out for this student? Numbers separated by commas, or Enter to include all: ").strip()
        if not raw:
            return set()
        picks = [x for x in raw.replace(" ", "").split(",") if x]
        if all(x.isdigit() and 1 <= int(x) <= len(opts) for x in picks):
            return {opts[int(x) - 1] for x in picks}
        print(f"  Please enter numbers between 1 and {len(opts)}.")


def print_summary(tt: Timetable, staff: Staff) -> None:
    print(f"\n{tt.student} - {tt.tutor_group} - tutor room {tt.tutor_room or '?'} - tutor "
          f"{staff.name(tt.tutor_code) if tt.tutor_code else '?'}")
    for wk in sorted(tt.weeks):
        print(f"\nWeek {wk}")
        for d in DAYS:
            parts = [f"{ls.slot} {ls.subject} {ls.room or '-'} {','.join(ls.teachers) or '-'}"
                     for ls in tt.weeks[wk][d]]
            moves = sum(ls.move for ls in tt.weeks[wk][d])
            print(f"  {d}  " + " | ".join(parts) + f"   [{moves} moves]")


def print_instructions() -> None:
    print("\nPrinting")
    print("  Timetable: A4, 100% / Actual size, double-sided, flip on SHORT edge.")
    print("  Cards:     A4, 100% / Actual size, double-sided, flip on LONG edge.")
    print("  Do a test print first - back-side alignment can drift 1-2 mm on some printers.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Wixams timetable PDF -> printable wall timetable + kit cards")
    ap.add_argument("--pdf", default=os.environ.get("TIMETABLE_PDF"),
                    help="Timetable PDF (or set TIMETABLE_PDF)")
    ap.add_argument("--out", default="./output", help="Output folder (default ./output)")
    ap.add_argument("--logo", default=str(BASE_DIR / "wixams-logo.png"), help="Logo PNG (white on transparent)")
    ap.add_argument("--debug", action="store_true", help="Dump parsed JSON and intermediate HTML to <out>/debug")
    ap.add_argument("--inspect", action="store_true", help="Print every word + coordinates in the PDF and exit")
    ap.add_argument("--name", help="Student name (skips the prompt)")
    ap.add_argument("--names-on-cards", choices=["yes", "no"], help="Skips the prompt about names on cards")
    ap.add_argument("--leave-out", metavar="ITEMS",
                    help="Comma-separated kit items to leave off this student's lists, e.g. \"Lunch,Hair tied back\" "
                         "(skips the prompt). Use \"none\" to include everything.")
    ap.add_argument("--yes", action="store_true", help="Skip the 'Looks right?' confirmation")
    ap.add_argument("--refresh-staff", action="store_true", help="Re-download the staff list now")
    ap.add_argument("--offline", action="store_true", help="Don't contact the staff website")
    args = ap.parse_args(argv)
    try:
        if not args.pdf:
            raise TimetableError("No PDF given. Use --pdf /path/to/Timetable.pdf (or set TIMETABLE_PDF).")
        pdf_path = Path(args.pdf).expanduser()
        if args.inspect:
            inspect_pdf(pdf_path)
            return 0
        logo = Path(args.logo).expanduser()
        if not logo.exists():
            raise TimetableError(f"Logo not found: {logo}. Pass --logo /path/to/logo.png "
                                 "(white-on-transparent PNG, it is placed on dark green).")
        tt = parse_pdf(pdf_path)
        name = args.name or ask("Student's name: ", tt.student)
        if not name:
            raise TimetableError("A student name is required.")
        tt.student = name
        if args.names_on_cards:
            show = args.names_on_cards == "yes"
        else:
            show = yes_no("Include the student's name on the kit cards? [y/N] ", False)
        if args.leave_out is not None:
            leave_out = resolve_leave_out([] if args.leave_out.lower() == "none" else args.leave_out.split(","))
        else:
            leave_out = ask_leave_out()
        staff = Staff.load(refresh=args.refresh_staff, offline=args.offline)
        print_summary(tt, staff)
        if args.debug:
            dbg = Path(args.out) / "debug"
            dbg.mkdir(parents=True, exist_ok=True)
            (dbg / "parsed.json").write_text(json.dumps(asdict(tt), indent=2), encoding="utf-8")
        if not args.yes and not yes_no("\nLooks right? [Y/n] ", True):
            print("Aborted. Run with --inspect to see what is in the PDF.")
            return 1
        view = build_view(tt, staff, show, logo, leave_out)
        tt_path, cards_path, warnings = render_pdfs(view, Path(args.out), name, debug=args.debug)
        print(f"\nWrote {tt_path}\nWrote {cards_path}")
        for w in warnings:
            print(f"WARNING: {w}")
        if not warnings:
            print("Card fit check: all 20 card sides fit within 91 x 57 mm.")
        print_instructions()
        return 0
    except TimetableError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print()
        return 130


if __name__ == "__main__":
    sys.exit(main())
