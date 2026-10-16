"""Tests. A synthetic PDF in the same layout as the real SIMS export is generated on the fly (the real PDF
contains a student's name, so it is not committed). Set TIMETABLE_PDF to also run the Monday check on a real file."""
import os
from pathlib import Path

import pytest

import timetable as T

HEADER = "Timetable for Alex Smith , 7 Franklin , Week {wk}, Monday,12October2026 - Friday,16October2026"
REG = "Year 7: 7 Franklin<br>C104<br>JLA"


def lesson(subj, room, teacher, kind="KS3:", code="7F/Xx"):
    r = f"{room}<br>" if room else ""
    return f"{subj} {kind} Year 7: {code}<br>{r}{teacher}"


MON_A = [REG,
         "Mathematics KS3: Year 7: 7F/<br>Ma<br>D114<br>CKA",
         "PE (Gen): Year 7: 7AFH/Pe2<br>CHI",
         "Art (KStg3): Year 7: 7F/Ar<br>D121<br>LHU , NFR",
         "Music KS3: Year 7: 7F/Mu<br>B103<br>CBU",
         "Science KS3: Year 7: 7F/Sc<br>D011<br>DDU"]


def filler(wk, day):
    return [REG, lesson("English", "D208", "ALE"), lesson("Religious Studies", "D113", "CWN"),
            lesson("Food", "D013", "FLA , CT1"), lesson("PE (Gen)", "Spor1", "TDA"),
            lesson("Drama (KStg3)", "Assem", "RHA")]


def week_html(wk):
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    head = "".join(f"<th>{h}</th>" for h in ["", "Reg (08:40-09:10)", "P1 (09:10-10:10)", "P2 (10:10-11:10)",
                                                "P3 (11:30-12:30)", "P4 (12:30-13:30)", "P5 (14:20-15:20)"])
    rows = ""
    for d in days:
        cells = MON_A if (wk == "A" and d == "Monday") else filler(wk, d)
        rows += f"<tr><td>{d} {wk}</td>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
    return f"<div class=pg><p>{HEADER.format(wk=wk)}</p><table><tr>{head}</tr>{rows}</table></div>"


@pytest.fixture(scope="session")
def sample_pdf(tmp_path_factory):
    from weasyprint import HTML
    css = """@page{size:A4 landscape;margin:12mm} body{font:8pt sans-serif} .pg{page-break-after:always}
             table{border-collapse:collapse;width:100%} td,th{border:1px solid #000;padding:4px;width:14%;vertical-align:middle}
             td:first-child{width:6%}"""
    html = f"<style>{css}</style>" + week_html("A") + week_html("B")
    p = tmp_path_factory.mktemp("pdf") / "Timetable.pdf"
    HTML(string=html).write_pdf(str(p))
    return p


def monday_check(tt):
    got = [(l.slot, l.subject, l.room, ",".join(l.teachers)) for l in tt.weeks["A"]["Mon"]]
    assert got == [("Reg", "Tutor", "C104", "JLA"), ("P1", "Maths", "D114", "CKA"), ("P2", "PE", "Sports", "CHI"),
                   ("P3", "Art", "D121", "LHU,NFR"), ("P4", "Music", "B103", "CBU"), ("P5", "Science", "D011", "DDU")]


def test_week_a_monday_synthetic(sample_pdf):
    tt = T.parse_pdf(sample_pdf)
    monday_check(tt)
    assert (tt.student, tt.tutor_group, tt.tutor_room, tt.tutor_code) == ("Alex Smith", "7 Franklin", "C104", "JLA")
    assert set(tt.weeks) == {"A", "B"}


@pytest.mark.skipif(not os.environ.get("TIMETABLE_PDF"), reason="TIMETABLE_PDF not set")
def test_week_a_monday_real_pdf():
    monday_check(T.parse_pdf(Path(os.environ["TIMETABLE_PDF"])))


def test_blocks_and_moves(sample_pdf):
    tt = T.parse_pdf(sample_pdf)
    mon = tt.weeks["A"]["Mon"]
    assert [l.block for l in mon] == ["C", "D", T.SPORTS, "D", "B", "D"]
    assert [l.move for l in mon] == [False, True, True, True, True, True]  # Reg -> P1 counts
    fri = tt.weeks["B"]["Fri"]
    assert [l.room for l in fri][4:] == ["Sports 1", "Hall"]


@pytest.mark.parametrize("raw,subj,expect", [
    ("Spo 2", "PE", ("Sports 2", T.SPORTS)), ("Spor1", "PE", ("Sports 1", T.SPORTS)),
    ("Assem", "Drama", ("Hall", T.SPORTS)), ("", "PE", ("Sports", T.SPORTS)),
    ("", "Maths", ("", None)), ("C104", "", ("C104", "C")), ("d114", "", ("D114", "D"))])
def test_normalise_room(raw, subj, expect):
    assert T.normalise_room(raw, subj) == expect


def test_parse_cell_variants():
    assert T.parse_cell("Religious Studies KS3: Year 7:\n7F/Re\nD113\nCWN", "P2") == ("RE", "D113", ["CWN"])
    assert T.parse_cell("Food KS3: Year 7: 7AF/Fd1\nD013\nFLA , CT1", "P3") == ("Food", "D013", ["FLA", "CT1"])
    assert T.parse_cell("PE (Gen): Year 7: 7AFH/Pe2\nSpo 2\nTDA", "P2") == ("PE", "Spo 2", ["TDA"])
    assert T.parse_cell("Year 7: 7 Franklin\nC104\nJLA", "Reg") == ("Tutor", "C104", ["JLA"])


def test_kit_rules():
    def mk(*subjects):
        return [T.Lesson("P1", s, "D1", "D", [], "", "") for s in subjects]
    k = T.kit_for_day(mk("Maths", "PE", "Science", "Food", "RE", "Maths"))
    assert k["workbooks"] == ["Maths", "Science", "Food", "RE"]
    assert k["items"][:3] == T.KIT_ALWAYS
    for x in ["Calculator + protractor", "PE kit", "Hair tied back", "Food ingredients", "Tie"]:
        assert x in k["extras"]
    assert "PE workbook" not in k["items"]
    assert T.kit_for_day(mk("English"))["extras"] == []


def test_staff_codes_and_html():
    assert T.staff_code("Chloe", "Kaler") == "CKA"
    html = "<p>Mrs Charlotte Hindmarch – Teacher of PE</p><p>Mrs Kaler graduated from X - and then some long text " \
           "that goes on and on and on and on and on and on and on and on and on and on and on and on and on.</p>"
    assert T.parse_staff_html(html) == {"CHI": ["Mrs Hindmarch"]}


def test_staff_overrides_and_ambiguity():
    s = T.Staff({"CKA": ["Mrs Kaler", "Mr Kay"], "AAA": ["Ms A"]}, {"CKA": "Mrs Kaler (override)"})
    assert s.name("CKA") == "Mrs Kaler (override)" and s.name("AAA") == "Ms A" and s.name("ZZZ") == "ZZZ"
    assert s.display(["AAA", "ZZZ"]) == "Ms A & ZZZ"
    amb = T.Staff({"KAL": ["Mrs A", "Mr B"]}, {})
    assert amb.name("KAL") == "KAL"


def test_unknown_block_colours_are_generated():
    st = T.block_style("A")
    assert st["fill"].startswith("#") and st["chip"].startswith("#") and st["fill"] != st["chip"]
    assert T.block_style("A") == st


def test_errors(tmp_path, capsys):
    assert T.main(["--pdf", str(tmp_path / "nope.pdf")]) == 1
    assert "not found" in capsys.readouterr().err
    from weasyprint import HTML
    p = tmp_path / "other.pdf"
    HTML(string="<p>Shopping list: eggs</p>").write_pdf(str(p))
    assert T.main(["--pdf", str(p)]) == 1
    assert "doesn't look like a timetable" in capsys.readouterr().err
    assert T.main(["--pdf", str(p), "--logo", str(tmp_path / "x.png")]) == 1
    assert "Logo not found" in capsys.readouterr().err


@pytest.fixture(scope="session")
def fonts():
    try:
        T.ensure_fonts()
    except T.TimetableError:
        pytest.skip("fonts unavailable (offline)")


def test_end_to_end_render_and_fit(sample_pdf, tmp_path, fonts):
    import pdfplumber
    tt = T.parse_pdf(sample_pdf)
    view = T.build_view(tt, T.Staff({}, {"CKA": "Mrs Kaler"}), False, T.BASE_DIR / "wixams-logo.png")
    tp, cp, warnings = T.render_pdfs(view, tmp_path, tt.student)
    assert warnings == []
    with pdfplumber.open(tp) as p:
        assert len(p.pages) == 2 and round(p.pages[0].width) == 842  # A4 landscape
    with pdfplumber.open(cp) as p:
        assert len(p.pages) == 2 and round(p.pages[0].width) == 595  # A4 portrait
        assert "Alex" not in (p.pages[1].extract_text() or "")  # names off the cards


def test_overflow_check_flags_tall_card(fonts):
    from weasyprint import HTML
    html = ('<style>@page{size:A4;margin:0}.card{width:91mm;height:57mm;overflow:hidden}</style>'
            '<div class="card" data-label="tall"><div class="inner"><div style="height:70mm"></div></div></div>')
    warnings = T.check_card_fit(HTML(string=html).render(), expected_cards=1)
    assert len(warnings) == 1 and "tall" in warnings[0]


def test_kit_leave_out():
    ls = [T.Lesson("P1", s, "D1", "D", [], "", "") for s in ("Maths", "PE", "Science", "Food", "RE")]
    out = T.resolve_leave_out(["lunch", "Hair tied back", "workbooks"])
    k = T.kit_for_day(ls, out)
    assert k["always"] == ["Pencil case", "Water bottle"]
    assert k["workbooks"] == [] and not any("workbook" in i for i in k["items"])
    assert "Hair tied back" not in k["items"] and "Lunch" not in k["items"]
    assert "PE kit" in k["items"] and "Tie" in k["items"]
    assert T.kit_for_day(ls)["always"] == T.KIT_ALWAYS  # default unchanged


def test_kit_leave_out_unknown_item():
    with pytest.raises(T.TimetableError, match="Unknown kit item"):
        T.resolve_leave_out(["Sandwiches"])


def test_leave_out_reaches_pdf(sample_pdf, tmp_path, fonts):
    import pdfplumber
    tt = T.parse_pdf(sample_pdf)
    view = T.build_view(tt, T.Staff({}, {}), False, T.BASE_DIR / "wixams-logo.png",
                        T.resolve_leave_out(["Lunch", "Hair tied back"]))
    tp, cp, warnings = T.render_pdfs(view, tmp_path, "X")
    assert warnings == []
    with pdfplumber.open(cp) as p:
        back = p.pages[1].extract_text()
    assert "Lunch" not in back and "Hair tied back" not in back and "Pencil case" in back
    with pdfplumber.open(tp) as p:
        assert "Every day: pencil case + water bottle" in p.pages[0].extract_text()
        assert "+ lunch" not in p.pages[0].extract_text()
