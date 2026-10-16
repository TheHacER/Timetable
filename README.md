# Wixams timetable → printable PDFs

Turns a Wixams Academy SIMS-style timetable PDF into:

- **`<Name> Timetable.pdf`** – A4 landscape, 2 pages (Week A, Week B), a reversible wall timetable.
- **`<Name> Cards.pdf`** – A4 portrait, 2 pages: fronts and backs of 10 cut-out daily kit cards (91 × 57 mm).

## Setup

```
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

WeasyPrint needs Pango (`apt install libpango-1.0-0 libpangoft2-1.0-0` on Debian/Ubuntu; `brew install pango` on macOS).
Caprasimo and Figtree are downloaded into `fonts/` on first run.

## Use

```
python timetable.py --pdf "/path/to/Timetable.pdf" [--out ./output] [--logo ./wixams-logo.png]
```

`--pdf` can also come from `TIMETABLE_PDF`. You are asked for the student's name (pre-filled from the PDF header;
Ctrl-U clears it), whether to put the name on the kit cards, and to confirm the parsed summary before anything is
rendered. Other flags: `--debug` (dump `parsed.json` + intermediate HTML to `<out>/debug`), `--inspect` (print every
word and coordinate in the PDF and exit – use this if a different layout won't parse), `--name`,
`--names-on-cards yes|no`, `--leave-out "Lunch,Hair tied back"`, `--yes` (non-interactive), `--refresh-staff`, `--offline`.

## Leaving kit items out

Not every item applies to every student. After the name questions you are shown the kit items (pencil case, water
bottle, lunch, workbooks, calculator + protractor, PE kit, hair tied back, food ingredients, tie) and can type the
numbers to leave off this student's lists, or press Enter to keep everything. Skip the prompt with
`--leave-out "Lunch,Hair tied back"` (or `--leave-out none`). Omitted items disappear from the card backs, the wall
timetable's kit list and its "Every day:" line.

## Printing

- **Timetable:** A4, 100% / Actual size, double-sided, flip on **short** edge.
- **Cards:** A4, 100% / Actual size, double-sided, flip on **long** edge. Do a test print first – back-side alignment
  can drift 1–2 mm on some printers.

## Customising

Everything editable is in the `CONFIG` block at the top of `timetable.py`: bell times (`SCHEDULE`), kit rules
(`KIT_ALWAYS`, `KIT_RULES`, `KIT_NO_WORKBOOK`), subject display names (`SUBJECT_ALIASES`), block colours
(`BLOCK_COLOURS`; unknown block letters get a generated warm colour) and card geometry. Layout/CSS is in `templates/`.

**Teacher names:** codes (first initial + first two letters of surname) are matched against
<https://wixamsacademy.co.uk/Teaching-Staff/>, cached in `staff_cache.json` (refreshed weekly). Add or correct names in
`staff_overrides.json` (`"CKA": "Mrs Kaler"`) – overrides always win. Ambiguous or unknown codes are shown raw, and an
unreachable website falls back to the cache and overrides.

**Fit check:** after rendering, every card is measured and a warning printed if its content exceeds 57 mm.

## Tests

```
pip install -r requirements-dev.txt && python -m pytest
```

The tests build a synthetic PDF in the same layout as the real export. Set `TIMETABLE_PDF` to also check Week A
Monday against a real file.
