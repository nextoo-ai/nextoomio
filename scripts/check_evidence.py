#!/usr/bin/env python3
"""Check verification evidence files and template (P0-F8.5, CQ-D8).

Validates, and nothing else:
1. docs/evidence/TEMPLATE.md exists and contains:
   - Mandatory field labels: Date, Image version and commit, Hardware model and revision,
     Physical access needed, Procedure, Observed result, Missed targets.
   - Explicit prohibition against recording secrets, keys, and private network addresses.
2. Every evidence file in docs/evidence/ (excluding TEMPLATE.md and README.md):
   - Its file name matches the story or defect identifier convention:
     P<phase>-F<n>.<m>.md or D-<nnn>.md.
   - All mandatory fields are present and non-empty (not blank, not placeholder).
   - No private keys or secrets are recorded.

Offline only: does not connect to GitHub or network to verify issue existence.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
EVIDENCE_DIR = ROOT / "docs" / "evidence"
TEMPLATE_PATH = EVIDENCE_DIR / "TEMPLATE.md"

FILENAME_RE = re.compile(r"^(?:P\d+-F\d+\.\d+|D-\d+)\.md$")
PLACEHOLDER_RE = re.compile(r"^\s*<[^>]+>\s*$")
TODO_RE = re.compile(r"^\s*(?:TODO|TBD|FIXME)\b", re.IGNORECASE)
PRIVATE_KEY_RE = re.compile(r"-----BEGIN[ A-Z0-9_-]*PRIVATE KEY-----")

REQUIRED_TEMPLATE_FIELDS = [
    ("Date", re.compile(r"(?:^|\n)\s*(?:[-*]\s+)?(?:\*\*)?Date(?::\*\*|\*\*:|:)", re.IGNORECASE)),
    ("Image version and commit", re.compile(r"(?:^|\n)\s*(?:[-*]\s+)?(?:\*\*)?Image\s+version\s+and\s+commit(?::\*\*|\*\*:|:)", re.IGNORECASE)),
    ("Hardware model and revision", re.compile(r"(?:^|\n)\s*(?:[-*]\s+)?(?:\*\*)?Hardware\s+model\s+and\s+revision(?::\*\*|\*\*:|:)", re.IGNORECASE)),
    ("Physical access needed", re.compile(r"(?:^|\n)\s*(?:[-*]\s+)?(?:\*\*)?Physical\s+access(?:\s+needed)?(?::\*\*|\*\*:|:)", re.IGNORECASE)),
    ("Procedure", re.compile(r"^#{1,6}\s+.*Procedure.*$", re.MULTILINE | re.IGNORECASE)),
    ("Observed result", re.compile(r"^#{1,6}\s+.*Observed\s+results?.*$", re.MULTILINE | re.IGNORECASE)),
    ("Missed targets", re.compile(r"^#{1,6}\s+.*Missed\s+targets?.*$", re.MULTILINE | re.IGNORECASE)),
]


def is_empty_value(val: str) -> bool:
    s = val.strip()
    if not s:
        return True
    s_clean = re.sub(r"<[^>]+>", "", s).strip()
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


def extract_section_body(text: str, heading_pattern: re.Pattern) -> str | None:
    lines = text.splitlines()
    in_section = False
    section_level = 0
    body_lines: list[str] = []

    for line in lines:
        heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading_match:
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            if in_section:
                if level <= section_level:
                    break
                body_lines.append(line)
            elif heading_pattern.search(title):
                in_section = True
                section_level = level
        elif in_section:
            body_lines.append(line)

    if not in_section:
        return None

    raw = "\n".join(body_lines)
    cleaned = re.sub(r"<!--.*?-->", "", raw, flags=re.DOTALL)
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    return cleaned.strip()


def check_table_column(text: str, col_name_pattern: re.Pattern) -> bool:
    lines = text.splitlines()
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
                    cleaned = re.sub(r"<[^>]+>", "", val).strip()
                    if cleaned and not is_empty_value(cleaned):
                        return True
        else:
            in_table = False
            col_idx = -1
    return False


def check_template(template_path: pathlib.Path) -> list[str]:
    errors: list[str] = []
    rel_path = template_path.relative_to(ROOT) if template_path.is_relative_to(ROOT) else template_path
    if not template_path.is_file():
        return [f"{rel_path}: evidence template file does not exist"]

    text = template_path.read_text(encoding="utf-8")
    for name, pattern in REQUIRED_TEMPLATE_FIELDS:
        if not pattern.search(text):
            errors.append(f"{rel_path}: missing template field '{name}'")

    lower_text = text.lower()
    if "secret" not in lower_text:
        errors.append(f"{rel_path}: template must state that secrets must not be recorded")
    if "key" not in lower_text:
        errors.append(f"{rel_path}: template must state that keys must not be recorded")
    if "private network" not in lower_text and "addresses of private networks" not in lower_text:
        errors.append(f"{rel_path}: template must state that addresses of private networks must not be recorded")

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
        if not (has_img is not None and not is_empty_value(has_img) and has_commit is not None and not is_empty_value(has_commit)):
            errors.append(f"{rel_path}: mandatory field 'Image version and commit' is empty or missing")

    # 3. Hardware model and revision
    hw_val = extract_metadata_field(text, re.compile(r"^\s*(?:[-*]\s+)?(?:\*\*)?Hardware(?:\s+model)?(?:\s+and\s+revision)?(?::\*\*|\*\*:|:)\s*(.*)$", re.IGNORECASE))
    if hw_val is None or is_empty_value(hw_val):
        errors.append(f"{rel_path}: mandatory field 'Hardware model and revision' is empty or missing")

    # 4. Physical access needed
    pa_val = extract_metadata_field(text, re.compile(r"^\s*(?:[-*]\s+)?(?:\*\*)?Physical\s+access(?:\s+needed)?(?::\*\*|\*\*:|:)\s*(.*)$", re.IGNORECASE))
    if pa_val is None or is_empty_value(pa_val):
        errors.append(f"{rel_path}: mandatory field 'Physical access needed' is empty or missing")

    # 5. Procedure
    proc_body = extract_section_body(text, re.compile(r"\bprocedure\b", re.IGNORECASE))
    proc_in_table = check_table_column(text, re.compile(r"\bprocedure\b", re.IGNORECASE))
    proc_field = extract_metadata_field(text, re.compile(r"^\s*(?:[-*]\s+)?(?:\*\*)?Procedure(?::\*\*|\*\*:|:)\s*(.*)$", re.IGNORECASE))
    if not ((proc_body and not is_empty_value(proc_body)) or proc_in_table or (proc_field and not is_empty_value(proc_field))):
        errors.append(f"{rel_path}: mandatory field 'Procedure' is empty or missing")

    # 6. Observed result
    res_body = extract_section_body(text, re.compile(r"\bobserved\s+results?\b", re.IGNORECASE))
    res_in_table = check_table_column(text, re.compile(r"\bobserved\s+results?\b", re.IGNORECASE))
    res_field = extract_metadata_field(text, re.compile(r"^\s*(?:[-*]\s+)?(?:\*\*)?Observed\s+results?(?::\*\*|\*\*:|:)\s*(.*)$", re.IGNORECASE))
    if not ((res_body and not is_empty_value(res_body)) or res_in_table or (res_field and not is_empty_value(res_field))):
        errors.append(f"{rel_path}: mandatory field 'Observed result' is empty or missing")

    # 7. Missed targets
    miss_body = extract_section_body(text, re.compile(r"\b(?:missed\s+targets?|not\s+checked)\b", re.IGNORECASE))
    miss_field = extract_metadata_field(text, re.compile(r"^\s*(?:[-*]\s+)?(?:\*\*)?Missed\s+targets?(?::\*\*|\*\*:|:)\s*(.*)$", re.IGNORECASE))
    if not ((miss_body and not is_empty_value(miss_body)) or (miss_field and not is_empty_value(miss_field))):
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


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

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
