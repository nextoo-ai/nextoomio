#!/usr/bin/env python3
"""Check document versions and cross-document section references (P0-F8.3, CQ-M9).

Checks, and nothing else:

1. The header of SPECIFICATION.md, ROADMAP.md, and ARCHITECTURE.md (the lines
   before the first "## " heading): a version stated for another one of the
   three documents must equal that document's own "Document version".
2. README.md: a version stated for one of the three documents must equal that
   document's own "Document version".
3. Every explicit reference "<document> section <n>" in the maintained Markdown
   files, where <document> is SPECIFICATION, ROADMAP, or ARCHITECTURE (with or
   without ".md", or as the lower-case word), must point to a numbered heading
   that exists in that document.

Version numbers in version notes, decision records, and other body text are
history and are not compared. Prose is not checked for factual correctness.
The frozen SK/ archive is not read.
"""

from __future__ import annotations

import pathlib
import re
import sys
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parent.parent

DOCS = {
    "specification": "SPECIFICATION.md",
    "roadmap": "ROADMAP.md",
    "architecture": "ARCHITECTURE.md",
}

NUM = r"\d+(?:\.\d+)*"

OWN_VERSION_RE = re.compile(rf"^Document version:\s*\**({NUM})\**\s*$")
HEADING_RE = re.compile(rf"^#{{1,6}}\s+({NUM})\.?\s")
FENCE_RE = re.compile(r"^\s*(```|~~~)")

# A mention of one of the three documents: a link such as
# "[ROADMAP.md](../ROADMAP.md)", or the bare name or word.
MENTION_RE = re.compile(
    r"\[(?P<link>SPECIFICATION|ROADMAP|ARCHITECTURE)\.md\]\([^)]*\)"
    r"|(?<![\w/.-])(?P<word>specification|roadmap|architecture)(?:\.md)?(?!\w)",
    re.IGNORECASE,
)

# A version stated after a mention: "version 1.15", "`v1.15`". A release
# range such as "`v0.0.1–v0.0.5`" is not a document version.
VERSION_RE = re.compile(rf"(?:`v|\bversion\s+\**)({NUM})(?![\w.]*\s*[–-]\s*v?\d)")

# "<document> section 3.5", "<document>.md (section 5.4)", "sections 10–11".
SECTION_REF_RE = re.compile(
    r"(?<![\w/.-])(?P<doc>specification|roadmap|architecture)(?:\.md)?`?(?!\w)"
    r"(?:\]\([^)]*\))?[\s,:(]{0,3}(?:sections?|§)\s*"
    rf"(?P<nums>{NUM}(?:\s*(?:–|-|/|and)\s*{NUM})*)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Stated:
    file: str
    line: int
    doc: str  # file name of the document the version is stated for
    version: str


def rel(path: pathlib.Path) -> str:
    return str(path.relative_to(ROOT))


def read_lines(path: pathlib.Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def own_version(path: pathlib.Path) -> tuple[str, int] | None:
    for number, line in enumerate(read_lines(path), start=1):
        match = OWN_VERSION_RE.match(line)
        if match:
            return match.group(1), number
    return None


def header_lines(path: pathlib.Path) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    for number, line in enumerate(read_lines(path), start=1):
        if line.startswith("## "):
            break
        lines.append((number, line))
    return lines


def stated_versions(file: str, numbered: list[tuple[int, str]], links_only: bool) -> list[Stated]:
    """Versions stated for the three documents, one segment per mention."""
    found: list[Stated] = []
    for number, line in numbered:
        mentions = [
            m for m in MENTION_RE.finditer(line) if m.group("link") or not links_only
        ]
        for index, mention in enumerate(mentions):
            end = mentions[index + 1].start() if index + 1 < len(mentions) else len(line)
            version = VERSION_RE.search(line, mention.end(), end)
            if not version:
                continue
            name = (mention.group("link") or mention.group("word")).lower()
            found.append(Stated(file, number, DOCS[name], version.group(1)))
    return found


def headings(path: pathlib.Path) -> set[str]:
    numbers: set[str] = set()
    in_fence = False
    for line in read_lines(path):
        if FENCE_RE.match(line):
            in_fence = not in_fence
        elif not in_fence:
            match = HEADING_RE.match(line)
            if match:
                numbers.add(match.group(1))
    return numbers


def maintained_markdown() -> list[pathlib.Path]:
    # Same set as scripts/check_traceability.py; SK/ is never read.
    names = [
        "SPECIFICATION.md",
        "ROADMAP.md",
        "ARCHITECTURE.md",
        "ARCHITECTURE_REVIEW.md",
        "AGENTS.md",
        "README.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
    ]
    files = [ROOT / name for name in names]
    files += sorted((ROOT / "docs").rglob("*.md"))
    files += sorted((ROOT / "meta-nextoomio").rglob("*.md"))
    return [f for f in files if f.exists()]


def main() -> int:
    errors: list[str] = []
    verified: list[str] = []

    own: dict[str, tuple[str, int]] = {}
    for file in DOCS.values():
        found = own_version(ROOT / file)
        if found is None:
            errors.append(f"{file}: no 'Document version:' line found")
        else:
            own[file] = found

    def compare(item: Stated) -> None:
        if item.doc not in own:
            return
        actual, actual_line = own[item.doc]
        where = f"{item.file}:{item.line}"
        if item.version != actual:
            errors.append(
                f"{where} states {item.doc} version {item.version}, "
                f"but {item.doc}:{actual_line} has Document version {actual}"
            )
        else:
            verified.append(f"{where} states {item.doc} {item.version}")

    for file in DOCS.values():
        for item in stated_versions(file, header_lines(ROOT / file), links_only=True):
            if item.doc != file:
                compare(item)

    readme = ROOT / "README.md"
    if readme.exists():
        numbered = list(enumerate(read_lines(readme), start=1))
        for item in stated_versions("README.md", numbered, links_only=False):
            compare(item)

    section_sets = {file: headings(ROOT / file) for file in DOCS.values()}
    refs_checked: list[str] = []
    for md_file in maintained_markdown():
        in_fence = False
        for number, line in enumerate(read_lines(md_file), start=1):
            if FENCE_RE.match(line):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            for match in SECTION_REF_RE.finditer(line):
                target = DOCS[match.group("doc").lower()]
                for section in re.findall(NUM, match.group("nums")):
                    where = f"{rel(md_file)}:{number}"
                    if section in section_sets[target]:
                        refs_checked.append(f"{where} -> {target} section {section}")
                    else:
                        errors.append(
                            f"{where} refers to {target} section {section}, "
                            f"which has no such numbered heading"
                        )

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    print("OK: document versions and cross-references")
    print("Document versions: " + ", ".join(f"{f} {v}" for f, (v, _) in own.items()))
    print(f"Stated versions equal to the document's own version ({len(verified)}):")
    for entry in verified:
        print(f"  {entry}")
    print(f"Section references that resolve to a numbered heading ({len(refs_checked)}):")
    for entry in refs_checked:
        print(f"  {entry}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
