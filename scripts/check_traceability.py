#!/usr/bin/env python3
"""Verify requirement ID coverage and relative Markdown links across maintained docs.

Follows AGENTS.md rules:
- Checks SPECIFICATION.md, ROADMAP.md, ARCHITECTURE.md, README.md, CONTRIBUTING.md, SECURITY.md, docs/**
- Explicitly excludes the frozen SK/ archive and scratch directories.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

REQ_ID_RE = re.compile(
    r"\b(?:SYS|IN|SEL|MR|VOL|TRN|REC|MA|VOICE|CTL|I18N|ACT|UPD)-\d{2}\b"
)
MD_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def extract_ids(path: pathlib.Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    return set(REQ_ID_RE.findall(text))


def check_links(md_path: pathlib.Path) -> list[str]:
    errors: list[str] = []
    text = md_path.read_text(encoding="utf-8")
    for match in MD_LINK_RE.finditer(text):
        target = match.group(1).strip()
        if (
            target.startswith(("http://", "https://", "mailto:", "#"))
            or "://" in target
        ):
            continue
        rel_path = target.split("#", 1)[0]
        if not rel_path:
            continue
        resolved = (md_path.parent / rel_path).resolve()
        if not resolved.exists():
            errors.append(
                f"{md_path.relative_to(ROOT)}: broken relative link -> {target}"
            )
    return errors


def main() -> int:
    spec_ids = extract_ids(ROOT / "SPECIFICATION.md")
    roadmap_ids = extract_ids(ROOT / "ROADMAP.md")
    arch_ids = extract_ids(ROOT / "ARCHITECTURE.md")

    errors: list[str] = []

    if len(spec_ids) != 78:
        errors.append(f"Expected 78 requirement IDs in SPECIFICATION.md, found {len(spec_ids)}")

    missing_in_roadmap = sorted(spec_ids - roadmap_ids)
    extra_in_roadmap = sorted(roadmap_ids - spec_ids)
    missing_in_arch = sorted(spec_ids - arch_ids)
    extra_in_arch = sorted(arch_ids - spec_ids)

    if missing_in_roadmap:
        errors.append(f"Missing in ROADMAP.md: {missing_in_roadmap}")
    if extra_in_roadmap:
        errors.append(f"Extra in ROADMAP.md: {extra_in_roadmap}")
    if missing_in_arch:
        errors.append(f"Missing in ARCHITECTURE.md: {missing_in_arch}")
    if extra_in_arch:
        errors.append(f"Extra in ARCHITECTURE.md: {extra_in_arch}")

    md_files = [
        ROOT / "SPECIFICATION.md",
        ROOT / "ROADMAP.md",
        ROOT / "ARCHITECTURE.md",
        ROOT / "ARCHITECTURE_REVIEW.md",
        ROOT / "AGENTS.md",
        ROOT / "README.md",
        ROOT / "CONTRIBUTING.md",
        ROOT / "SECURITY.md",
        *sorted((ROOT / "docs").rglob("*.md")),
        *sorted((ROOT / "meta-nextoomio").rglob("*.md")),
    ]

    for md_file in md_files:
        if md_file.exists():
            errors.extend(check_links(md_file))

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    print(
        f"OK: All {len(spec_ids)} requirement IDs verified across SPECIFICATION.md, "
        f"ROADMAP.md, and ARCHITECTURE.md; {len(md_files)} Markdown files link-checked."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
