#!/usr/bin/env python3
"""Check verification evidence files and template (P0-F8.5, CQ-D8).

Validates, and nothing else:
1. docs/evidence/TEMPLATE.md exists and contains:
   - Mandatory field labels: Date, Image version and commit, Hardware model and revision,
     Physical access needed, Procedure, Observed result, Missed targets.
   - Visible prohibition outside HTML comments against recording secrets, keys, and private network addresses.
2. Every evidence file in docs/evidence/ (excluding TEMPLATE.md and README.md):
   - Its file name matches the story or defect identifier convention:
     P<phase>-F<n>.<m>.md or D-<nnn>.md.
   - All mandatory fields are present and non-empty (not blank, not placeholder).
   - 'Image version and commit' contains a git commit hash.
   - Table fallbacks for Procedure and Observed result are strictly scoped to the
     matching section body; an empty heading fails even if a table appears elsewhere.
   - Checks for committed PEM private keys (-----BEGIN ... PRIVATE KEY-----).
     Note: Arbitrary secrets, tokens, passwords, credentials, and private network
     addresses in evidence files are not heuristically detected by this script
     and must be reviewed manually (CQ-D8, CQ-R6).

Offline only: does not connect to GitHub or network to verify issue existence.
"""

from __future__ import annotations

import pathlib
import re
import sys
import tempfile
from typing import NamedTuple

ROOT = pathlib.Path(__file__).resolve().parent.parent
EVIDENCE_DIR = ROOT / "docs" / "evidence"
TEMPLATE_PATH = EVIDENCE_DIR / "TEMPLATE.md"

FILENAME_RE = re.compile(r"^(?:P\d+-F\d+\.\d+|D-\d+)\.md$")
PLACEHOLDER_RE = re.compile(r"^\s*<[^>]+>\s*$")
TODO_RE = re.compile(r"^\s*(?:TODO|TBD|FIXME)\b", re.IGNORECASE)
PRIVATE_KEY_RE = re.compile(r"-----BEGIN[ A-Z0-9_-]*PRIVATE KEY-----")
COMMIT_HASH_RE = re.compile(r"\b[0-9a-f]{7,40}\b", re.IGNORECASE)

REQUIRED_TEMPLATE_FIELDS = [
    ("Date", re.compile(r"(?:^|\n)\s*(?:[-*]\s+)?(?:\*\*)?Date(?::\*\*|\*\*:|:)", re.IGNORECASE)),
    ("Image version and commit", re.compile(r"(?:^|\n)\s*(?:[-*]\s+)?(?:\*\*)?Image\s+version\s+and\s+commit(?::\*\*|\*\*:|:)", re.IGNORECASE)),
    ("Hardware model and revision", re.compile(r"(?:^|\n)\s*(?:[-*]\s+)?(?:\*\*)?Hardware\s+model\s+and\s+revision(?::\*\*|\*\*:|:)", re.IGNORECASE)),
    ("Physical access needed", re.compile(r"(?:^|\n)\s*(?:[-*]\s+)?(?:\*\*)?Physical\s+access(?:\s+needed)?(?::\*\*|\*\*:|:)", re.IGNORECASE)),
    ("Procedure", re.compile(r"^#{1,6}\s+.*Procedure.*$", re.MULTILINE | re.IGNORECASE)),
    ("Observed result", re.compile(r"^#{1,6}\s+.*Observed\s+results?.*$", re.MULTILINE | re.IGNORECASE)),
    ("Missed targets", re.compile(r"^#{1,6}\s+.*Missed\s+targets?.*$", re.MULTILINE | re.IGNORECASE)),
]


class Section(NamedTuple):
    level: int
    title: str
    body: str


def is_empty_value(val: str) -> bool:
    s = val.strip()
    if not s:
        return True
    s_clean = re.sub(r"<!--.*?-->", "", s, flags=re.DOTALL).strip()
    if not s_clean:
        return True
    s_clean = re.sub(r"<[^>]+>", "", s_clean).strip()
    if not s_clean:
        return True
    if re.match(r"^\[\s*\]$", s_clean):
        return True
    if TODO_RE.match(s_clean):
        return True
    return False


def extract_metadata_field(text: str, field_pattern: re.Pattern) -> str | None:
    for line in text.splitlines():
        m = field_pattern.match(line)
        if m:
            return m.group(1).strip()
    return None


def extract_sections(text: str) -> list[Section]:
    """Return list of Section(level, title, body) for all markdown headings.

    A section's body includes all text lines up to the next heading with level <= this section's level.
    HTML comments are stripped from the body.
    """
    lines = text.splitlines()
    parsed_headings: list[tuple[int, str, int]] = []
    for idx, line in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.+)$", line)
        if m:
            parsed_headings.append((len(m.group(1)), m.group(2).strip(), idx))

    sections: list[Section] = []
    for i, (level, title, line_idx) in enumerate(parsed_headings):
        end_idx = len(lines)
        for next_level, _, next_line_idx in parsed_headings[i + 1:]:
            if next_level <= level:
                end_idx = next_line_idx
                break
        body_lines = lines[line_idx + 1:end_idx]
        raw_body = "\n".join(body_lines)
        cleaned_body = re.sub(r"<!--.*?-->", "", raw_body, flags=re.DOTALL).strip()
        sections.append(Section(level, title, cleaned_body))

    return sections


def check_table_column(section_body: str, col_name_pattern: re.Pattern) -> bool:
    """Check if the given section_body contains a markdown table with a column
    matching col_name_pattern that has at least one non-empty data row."""
    lines = section_body.splitlines()
    in_table = False
    col_idx = -1
    for line in lines:
        if "|" in line:
            parts = [p.strip() for p in line.strip().split("|")]
            if parts and parts[0] == "":
                parts.pop(0)
            if parts and parts[-1] == "":
                parts.pop()
            if not in_table:
                for idx, col in enumerate(parts):
                    if col_name_pattern.search(col):
                        in_table = True
                        col_idx = idx
                        break
            else:
                if all(re.match(r"^:?-+:?$", p) for p in parts):
                    continue
                if len(parts) > col_idx:
                    val = parts[col_idx]
                    if not is_empty_value(val):
                        return True
        else:
            in_table = False
            col_idx = -1
    return False


def section_has_content(body: str, col_name_pattern: re.Pattern | None = None) -> bool:
    if is_empty_value(body):
        return False
    if col_name_pattern and "|" in body:
        # Check if a table in this section has the expected column
        lines = body.splitlines()
        has_col = any(
            col_name_pattern.search(p)
            for line in lines
            if "|" in line
            for p in [x.strip() for x in line.strip().split("|")]
        )
        if has_col:
            return check_table_column(body, col_name_pattern)
    return True


def check_template(template_path: pathlib.Path) -> list[str]:
    errors: list[str] = []
    rel_path = template_path.relative_to(ROOT) if template_path.is_relative_to(ROOT) else template_path
    if not template_path.is_file():
        return [f"{rel_path}: evidence template file does not exist"]

    text = template_path.read_text(encoding="utf-8")
    for name, pattern in REQUIRED_TEMPLATE_FIELDS:
        if not pattern.search(text):
            errors.append(f"{rel_path}: missing template field '{name}'")

    visible_text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    lower_visible = visible_text.lower()
    if "secret" not in lower_visible:
        errors.append(f"{rel_path}: template must visibly state (outside comments) that secrets must not be recorded")
    if "key" not in lower_visible:
        errors.append(f"{rel_path}: template must visibly state (outside comments) that keys must not be recorded")
    if "private network" not in lower_visible and "addresses of private networks" not in lower_visible:
        errors.append(f"{rel_path}: template must visibly state (outside comments) that addresses of private networks must not be recorded")

    return errors


def check_evidence_file(path: pathlib.Path) -> list[str]:
    errors: list[str] = []
    rel_path = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path

    # Check filename convention
    if not FILENAME_RE.match(path.name):
        errors.append(
            f"{rel_path}: file name '{path.name}' is not a story or defect identifier "
            "in the form of the backlog conventions (P<phase>-F<n>.<m>.md or D-<nnn>.md)"
        )

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        errors.append(f"{rel_path}: could not read file: {e}")
        return errors

    # Check for committed private keys
    if PRIVATE_KEY_RE.search(text):
        errors.append(f"{rel_path}: contains private key; secrets must not be recorded in evidence")

    # 1. Date
    date_val = extract_metadata_field(text, re.compile(r"^\s*(?:[-*]\s+)?(?:\*\*)?Date(?::\*\*|\*\*:|:)\s*(.*)$", re.IGNORECASE))
    if date_val is None or is_empty_value(date_val):
        errors.append(f"{rel_path}: mandatory field 'Date' is empty or missing")

    # 2. Image version and commit
    img_val = extract_metadata_field(text, re.compile(r"^\s*(?:[-*]\s+)?(?:\*\*)?Image\s+version\s+and\s+commit(?::\*\*|\*\*:|:)\s*(.*)$", re.IGNORECASE))
    if img_val is None or is_empty_value(img_val):
        # Fallback: check separate Image version and Commit
        has_img = extract_metadata_field(text, re.compile(r"^\s*(?:[-*]\s+)?(?:\*\*)?Image(?:\s+version)?(?::\*\*|\*\*:|:)\s*(.*)$", re.IGNORECASE))
        has_commit = extract_metadata_field(text, re.compile(r"^\s*(?:[-*]\s+)?(?:\*\*)?(?:Check\s+)?Commit(?::\*\*|\*\*:|:)\s*(.*)$", re.IGNORECASE))
        if has_img is not None and not is_empty_value(has_img) and has_commit is not None and not is_empty_value(has_commit):
            img_val = f"{has_img}; {has_commit}"
        else:
            errors.append(f"{rel_path}: mandatory field 'Image version and commit' is empty or missing")
            img_val = None

    if img_val is not None:
        if not COMMIT_HASH_RE.search(img_val):
            errors.append(f"{rel_path}: mandatory field 'Image version and commit' must record a git commit hash")

    # 3. Hardware model and revision
    hw_val = extract_metadata_field(text, re.compile(r"^\s*(?:[-*]\s+)?(?:\*\*)?Hardware(?:\s+model)?(?:\s+and\s+revision)?(?::\*\*|\*\*:|:)\s*(.*)$", re.IGNORECASE))
    if hw_val is None or is_empty_value(hw_val):
        errors.append(f"{rel_path}: mandatory field 'Hardware model and revision' is empty or missing")

    # 4. Physical access needed
    pa_val = extract_metadata_field(text, re.compile(r"^\s*(?:[-*]\s+)?(?:\*\*)?Physical\s+access(?:\s+needed)?(?::\*\*|\*\*:|:)\s*(.*)$", re.IGNORECASE))
    if pa_val is None or is_empty_value(pa_val):
        errors.append(f"{rel_path}: mandatory field 'Physical access needed' is empty or missing")

    # Parse markdown sections for Procedure, Observed result, and Missed targets
    sections = extract_sections(text)

    proc_pat = re.compile(r"\bprocedure\b", re.IGNORECASE)
    res_pat = re.compile(r"\bobserved\s+results?\b", re.IGNORECASE)

    proc_sections = [s for s in sections if proc_pat.search(s.title)]
    res_sections = [s for s in sections if res_pat.search(s.title)]

    separate_proc = [s for s in proc_sections if not res_pat.search(s.title)]
    separate_res = [s for s in res_sections if not proc_pat.search(s.title)]
    combined = [s for s in sections if proc_pat.search(s.title) and res_pat.search(s.title)]

    # 5. Procedure
    if separate_proc:
        if not any(section_has_content(s.body, proc_pat) for s in separate_proc):
            errors.append(f"{rel_path}: mandatory field 'Procedure' is empty or missing")
    elif combined:
        if not any(section_has_content(s.body, proc_pat) for s in combined):
            errors.append(f"{rel_path}: mandatory field 'Procedure' is empty or missing")
    else:
        errors.append(f"{rel_path}: mandatory field 'Procedure' is empty or missing")

    # 6. Observed result
    if separate_res:
        if not any(section_has_content(s.body, res_pat) for s in separate_res):
            errors.append(f"{rel_path}: mandatory field 'Observed result' is empty or missing")
    elif combined:
        if not any(section_has_content(s.body, res_pat) for s in combined):
            errors.append(f"{rel_path}: mandatory field 'Observed result' is empty or missing")
    else:
        errors.append(f"{rel_path}: mandatory field 'Observed result' is empty or missing")

    # 7. Missed targets
    miss_sections = [s for s in sections if re.search(r"\b(?:missed\s+targets?|not\s+checked)\b", s.title, re.IGNORECASE)]
    if not miss_sections:
        errors.append(f"{rel_path}: mandatory field 'Missed targets' is empty or missing")
    elif not any(section_has_content(s.body) for s in miss_sections):
        errors.append(f"{rel_path}: mandatory field 'Missed targets' is empty or missing")

    return errors


def find_evidence_files(evidence_dir: pathlib.Path) -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for p in evidence_dir.rglob("*"):
        if not p.is_file():
            continue
        if p.name.startswith("."):
            continue
        if p.name in ("TEMPLATE.md", "README.md") and p.parent == evidence_dir:
            continue
        files.append(p)
    return sorted(files)


def run_self_tests() -> int:
    """Run automated unit tests for check_evidence rules and edge cases."""
    print("Running check_evidence self-tests...")
    failures: list[str] = []

    def assert_true(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(msg)
            print("F", end="", flush=True)
        else:
            print(".", end="", flush=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = pathlib.Path(tmpdir)

        # 1. Valid template check
        t_valid = tmp_path / "TEMPLATE.md"
        t_valid.write_text(
            "# Evidence: <ID> <Title>\n\n"
            "> [!IMPORTANT]\n"
            "> Secrets, private keys, credentials, tokens, passwords, and addresses of private networks must not be recorded in evidence.\n\n"
            "- **Date:** <YYYY-MM-DD>\n"
            "- **Image version and commit:** <image build/version and git commit, or \"none (repository only); commit <hash>\">\n"
            "- **Hardware model and revision:** <hardware model and revision, or \"none (repository only)\">\n"
            "- **Physical access needed:** <none, or describe physical intervention needed>\n\n"
            "## Procedure\n\n<steps>\n\n"
            "## Observed result\n\n<results>\n\n"
            "## Missed targets\n\n<missed>\n",
            encoding="utf-8",
        )
        errs = check_template(t_valid)
        assert_true(len(errs) == 0, f"Valid template failed: {errs}")

        # 2. Template missing required field
        t_missing_field = tmp_path / "TEMPLATE_missing_field.md"
        t_missing_field.write_text(
            "# Evidence: <ID>\n"
            "> [!IMPORTANT]\n> Secrets, keys, and addresses of private networks must not be recorded.\n"
            "- **Date:** <YYYY-MM-DD>\n"
            "## Procedure\n## Observed result\n## Missed targets\n",
            encoding="utf-8",
        )
        errs = check_template(t_missing_field)
        assert_true(any("Hardware model and revision" in e for e in errs), f"Expected missing hardware error, got: {errs}")

        # 3. Template secret notice inside HTML comments only
        t_comment_only = tmp_path / "TEMPLATE_comment_only.md"
        t_comment_only.write_text(
            "# Evidence: <ID>\n"
            "<!-- Secrets, keys, and addresses of private networks must not be recorded. -->\n"
            "- **Date:** <YYYY-MM-DD>\n"
            "- **Image version and commit:** commit cee739f\n"
            "- **Hardware model and revision:** none\n"
            "- **Physical access needed:** none\n"
            "## Procedure\n## Observed result\n## Missed targets\n",
            encoding="utf-8",
        )
        errs = check_template(t_comment_only)
        assert_true(any("outside comments" in e for e in errs), f"Expected comment-only rejection, got: {errs}")

        # 4. Valid evidence file (separate headings)
        f_valid = tmp_path / "P0-F8.5.md"
        f_valid.write_text(
            "# Evidence: P0-F8.5 Test\n"
            "- **Date:** 2026-11-14\n"
            "- **Image version and commit:** none (repository only); commit cee739f\n"
            "- **Hardware model and revision:** none\n"
            "- **Physical access needed:** none\n\n"
            "## Procedure\nSteps executed here.\n\n"
            "## Observed result\nOutput observed here.\n\n"
            "## Missed targets\nNone.\n",
            encoding="utf-8",
        )
        errs = check_evidence_file(f_valid)
        assert_true(len(errs) == 0, f"Valid evidence file failed: {errs}")

        # 5. Valid evidence file with table in Observed result
        f_table_res = tmp_path / "P0-F8.6.md"
        f_table_res.write_text(
            "# Evidence: P0-F8.6 Test\n"
            "- **Date:** 2026-11-14\n"
            "- **Image version and commit:** none (repository only); commit cee739f\n"
            "- **Hardware model and revision:** none\n"
            "- **Physical access needed:** none\n\n"
            "## Procedure\n1. Run test.\n\n"
            "## Observed result\n| AC | Procedure | Observed result |\n| --- | --- | --- |\n| AC1 | Step | Passed |\n\n"
            "## Missed targets\nNone.\n",
            encoding="utf-8",
        )
        errs = check_evidence_file(f_table_res)
        assert_true(len(errs) == 0, f"Valid table evidence file failed: {errs}")

        # 6. Valid combined section (Procedure and observed results)
        f_combined = tmp_path / "P0-F8.3.md"
        f_combined.write_text(
            "# Evidence: P0-F8.3 Test\n"
            "- **Date:** 2026-11-14\n"
            "- **Image version and commit:** none (repository only); commit bb43d5e\n"
            "- **Hardware model and revision:** none\n"
            "- **Physical access needed:** none\n\n"
            "## Procedure and observed results\n| AC | Procedure | Observed result |\n| --- | --- | --- |\n| AC1 | Step 1 | Pass |\n\n"
            "## Missed targets\nNone.\n",
            encoding="utf-8",
        )
        errs = check_evidence_file(f_combined)
        assert_true(len(errs) == 0, f"Valid combined section evidence file failed: {errs}")

        # 7. Bug from Finding 2: empty Procedure heading with table in Observed result must FAIL
        f_empty_proc = tmp_path / "P0-F8.7.md"
        f_empty_proc.write_text(
            "# Evidence: P0-F8.7 Test\n"
            "- **Date:** 2026-11-14\n"
            "- **Image version and commit:** none (repository only); commit cee739f\n"
            "- **Hardware model and revision:** none\n"
            "- **Physical access needed:** none\n\n"
            "## Procedure\n\n"
            "## Observed result\n| AC | Procedure | Observed result |\n| --- | --- | --- |\n| AC1 | Step 1 | Pass |\n\n"
            "## Missed targets\nNone.\n",
            encoding="utf-8",
        )
        errs = check_evidence_file(f_empty_proc)
        assert_true(any("mandatory field 'Procedure' is empty or missing" in e for e in errs), f"Expected empty Procedure error, got: {errs}")

        # 8. Empty Observed result heading must FAIL
        f_empty_res = tmp_path / "P0-F8.8.md"
        f_empty_res.write_text(
            "# Evidence: P0-F8.8 Test\n"
            "- **Date:** 2026-11-14\n"
            "- **Image version and commit:** none (repository only); commit cee739f\n"
            "- **Hardware model and revision:** none\n"
            "- **Physical access needed:** none\n\n"
            "## Procedure\nSome procedure.\n\n"
            "## Observed result\n\n"
            "## Missed targets\nNone.\n",
            encoding="utf-8",
        )
        errs = check_evidence_file(f_empty_res)
        assert_true(any("mandatory field 'Observed result' is empty or missing" in e for e in errs), f"Expected empty Observed result error, got: {errs}")

        # 9. Combined section with empty Observed result table cells must FAIL
        f_empty_cell = tmp_path / "P0-F8.9.md"
        f_empty_cell.write_text(
            "# Evidence: P0-F8.9 Test\n"
            "- **Date:** 2026-11-14\n"
            "- **Image version and commit:** none (repository only); commit cee739f\n"
            "- **Hardware model and revision:** none\n"
            "- **Physical access needed:** none\n\n"
            "## Procedure and observed results\n| AC | Procedure | Observed result |\n| --- | --- | --- |\n| AC1 | Step 1 | <placeholder> |\n\n"
            "## Missed targets\nNone.\n",
            encoding="utf-8",
        )
        errs = check_evidence_file(f_empty_cell)
        assert_true(any("mandatory field 'Observed result' is empty or missing" in e for e in errs), f"Expected empty result cell error, got: {errs}")

        # 10. Invalid filenames
        for bad_name in ["invalid.md", "P0-F8.md", "D-abc.md", "P0_F8.5.md"]:
            f_bad_name = tmp_path / bad_name
            f_bad_name.write_text(f_valid.read_text(encoding="utf-8"), encoding="utf-8")
            errs = check_evidence_file(f_bad_name)
            assert_true(any("not a story or defect identifier" in e for e in errs), f"Expected invalid filename error for {bad_name}, got: {errs}")

        # 11. Valid defect filename
        f_defect = tmp_path / "D-031.md"
        f_defect.write_text(f_valid.read_text(encoding="utf-8"), encoding="utf-8")
        errs = check_evidence_file(f_defect)
        assert_true(len(errs) == 0, f"Valid defect filename failed: {errs}")

        # 12. Finding 1: Image version and commit without commit hash must FAIL
        f_no_commit = tmp_path / "P0-F8.10.md"
        f_no_commit.write_text(
            "# Evidence: P0-F8.10 Test\n"
            "- **Date:** 2026-11-14\n"
            "- **Image version and commit:** none (repository only); branch `ops/P0-F8.5`\n"
            "- **Hardware model and revision:** none\n"
            "- **Physical access needed:** none\n\n"
            "## Procedure\nProcedure.\n\n"
            "## Observed result\nResults.\n\n"
            "## Missed targets\nNone.\n",
            encoding="utf-8",
        )
        errs = check_evidence_file(f_no_commit)
        assert_true(any("must record a git commit hash" in e for e in errs), f"Expected missing commit hash error, got: {errs}")

        # 13. Image version and commit with 'commit: n/a' must FAIL
        f_commit_na = tmp_path / "P0-F8.11.md"
        f_commit_na.write_text(
            "# Evidence: P0-F8.11 Test\n"
            "- **Date:** 2026-11-14\n"
            "- **Image version and commit:** none (repository only); commit: n/a\n"
            "- **Hardware model and revision:** none\n"
            "- **Physical access needed:** none\n\n"
            "## Procedure\nProcedure.\n\n"
            "## Observed result\nResults.\n\n"
            "## Missed targets\nNone.\n",
            encoding="utf-8",
        )
        errs = check_evidence_file(f_commit_na)
        assert_true(any("must record a git commit hash" in e for e in errs), f"Expected commit n/a rejection, got: {errs}")

        # 14. Missing or placeholder Date must FAIL
        f_bad_date = tmp_path / "P0-F8.12.md"
        f_bad_date.write_text(
            "# Evidence: P0-F8.12 Test\n"
            "- **Date:** <YYYY-MM-DD>\n"
            "- **Image version and commit:** commit cee739f\n"
            "- **Hardware model and revision:** none\n"
            "- **Physical access needed:** none\n\n"
            "## Procedure\nProcedure.\n\n"
            "## Observed result\nResults.\n\n"
            "## Missed targets\nNone.\n",
            encoding="utf-8",
        )
        errs = check_evidence_file(f_bad_date)
        assert_true(any("mandatory field 'Date' is empty or missing" in e for e in errs), f"Expected Date error, got: {errs}")

        # 15. Missing Missed targets must FAIL
        f_bad_missed = tmp_path / "P0-F8.13.md"
        f_bad_missed.write_text(
            "# Evidence: P0-F8.13 Test\n"
            "- **Date:** 2026-11-14\n"
            "- **Image version and commit:** commit cee739f\n"
            "- **Hardware model and revision:** none\n"
            "- **Physical access needed:** none\n\n"
            "## Procedure\nProcedure.\n\n"
            "## Observed result\nResults.\n\n"
            "## Missed targets\n<missed targets>\n",
            encoding="utf-8",
        )
        errs = check_evidence_file(f_bad_missed)
        assert_true(any("mandatory field 'Missed targets' is empty or missing" in e for e in errs), f"Expected Missed targets error, got: {errs}")

        # 16. Private key header detected
        f_privkey = tmp_path / "P0-F8.14.md"
        f_privkey.write_text(
            "# Evidence: P0-F8.14 Test\n"
            "- **Date:** 2026-11-14\n"
            "- **Image version and commit:** commit cee739f\n"
            "- **Hardware model and revision:** none\n"
            "- **Physical access needed:** none\n\n"
            "## Procedure\nProcedure.\n\n"
            "## Observed result\n-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----\n\n"
            "## Missed targets\nNone.\n",
            encoding="utf-8",
        )
        errs = check_evidence_file(f_privkey)
        assert_true(any("contains private key" in e for e in errs), f"Expected private key detection, got: {errs}")

    print()
    if failures:
        print(f"FAILED ({len(failures)} self-test failures):")
        for fail in failures:
            print(f"  - {fail}")
        return 1

    print("OK: all self-tests passed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    if "--test" in argv:
        return run_self_tests()

    errors: list[str] = []
    checked_files: list[pathlib.Path] = []

    if not argv:
        errors.extend(check_template(TEMPLATE_PATH))
        evidence_files = find_evidence_files(EVIDENCE_DIR)
        for ef in evidence_files:
            errors.extend(check_evidence_file(ef))
            checked_files.append(ef)
    else:
        for arg in argv:
            path = pathlib.Path(arg).resolve()
            if path == TEMPLATE_PATH:
                errors.extend(check_template(path))
            else:
                errors.extend(check_evidence_file(path))
                checked_files.append(path)

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    print("OK: verification evidence files and template validated.")
    print(f"Template verified: {TEMPLATE_PATH.relative_to(ROOT)}")
    print(f"Evidence files checked ({len(checked_files)}):")
    for cf in checked_files:
        rel = cf.relative_to(ROOT) if cf.is_relative_to(ROOT) else cf
        print(f"  {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
