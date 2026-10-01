#!/usr/bin/env python3
"""Build the Japanese OSTEP translation as a navigable EPUB 3.

Requires Pandoc 3.x (https://pandoc.org/installing.html). Run from any directory:
    python3 build_epub.py                       # horizontal (default)
    python3 build_epub.py --vertical            # traditional Japanese layout
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TITLE = "Operating Systems: Three Easy Pieces"
AUTHORS = ("Remzi H. Arpaci-Dusseau", "Andrea C. Arpaci-Dusseau")
PART = re.compile(r"^# (第\d+部 .+)$")
ENTRY = re.compile(r"^## \d+\. \[[^]]+\]\(\./(\d{2})/(\d{2})\.md\)$")
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
LINK = re.compile(r"(!?)\[([^]]*)\]\(([^)]+)\)")
OLD_NAV = re.compile(r"^\s*(?:\[(?:prev|next)\]\([^)]*\)\s*\|?\s*)+$", re.I)
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")


@dataclass(frozen=True)
class Chapter:
    source: Path
    number: str
    part: str
    title: str

    @property
    def anchor(self) -> str:
        return f"chapter-{self.number}"


def chapters_from_readme() -> list[Chapter]:
    """Use the published contents list, not a directory glob, for reading order."""
    part = ""
    chapters: list[Chapter] = []
    for line in (ROOT / "README.md").read_text(encoding="utf-8").splitlines():
        if match := PART.fullmatch(line):
            part = match.group(1)
        elif match := ENTRY.fullmatch(line):
            number, filename = match.groups()
            if number != filename or not part:
                raise ValueError(f"Invalid contents entry: {line}")
            source = ROOT / number / f"{number}.md"
            if not source.is_file():
                raise FileNotFoundError(f"Missing chapter: {source}")
            title = next(
                (m.group(2) for text in source.read_text(encoding="utf-8").splitlines()
                 if (m := HEADING.match(text))),
                None,
            )
            if title is None:
                raise ValueError(f"Chapter has no heading: {source}")
            chapters.append(Chapter(source, number, part, title))
    if not chapters or len({c.number for c in chapters}) != len(chapters):
        raise ValueError("README.md has no chapters or contains duplicate chapter numbers")
    return chapters


def convert_chapter(chapter: Chapter, chapters: list[Chapter], index: int,
                    vertical: bool = False) -> str:
    """Normalize heading levels and package local images and chapter links."""
    known = {c.source.resolve(): c.anchor for c in chapters}
    lines = chapter.source.read_text(encoding="utf-8").splitlines()
    output = [f"## {chapter.title} {{#{chapter.anchor}}}", ""]
    first_heading = True
    fence_char = ""
    fence_width = 0

    def replace_link(match: re.Match[str]) -> str:
        image, label, target = match.groups()
        if target.startswith(("http:", "https:", "mailto:", "#")):
            return match.group(0)
        if not image and "/" not in target and "." not in target:
            # The source has citations like [SR05](第7章): parenthetical prose,
            # not a URL. Keep the text rather than shipping broken EPUB links.
            return f"\\[{label}\\]({target})"
        path = (chapter.source.parent / target.split("#", 1)[0]).resolve()
        if image or path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".svg"}:
            if not path.is_file():
                raise FileNotFoundError(f"Missing image in {chapter.source}: {target}")
            # Absolute paths let Pandoc find figures in each chapter's own img/ folder.
            return f"![{label or '図 ' + path.stem.removeprefix('fig')}](<{path.as_posix()}>)"
        if path.suffix.lower() == ".md":
            if path not in known:
                raise ValueError(f"Unlisted chapter link in {chapter.source}: {target}")
            fragment = target.partition("#")[2]
            if fragment:
                raise ValueError(f"Unsupported chapter fragment in {chapter.source}: {target}")
            return f"[{label}](#{known[path]})"
        raise ValueError(f"Unsupported local link in {chapter.source}: {target}")

    for line in lines:
        fence = FENCE.match(line)
        if fence:
            marker = fence.group(1)
            if not fence_char:
                fence_char, fence_width = marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_width:
                fence_char = ""
            output.append(line)
            continue
        if fence_char:
            output.append(line)
            continue
        if OLD_NAV.fullmatch(line):
            continue  # Old source navigation includes links to untranslated chapters.
        if heading := HEADING.match(line):
            if first_heading:
                first_heading = False
                continue
            # Some source subsections use # instead of ##; always keep them under the chapter.
            level = min(6, max(3, len(heading.group(1)) + 1))
            output.append("#" * level + " " + heading.group(2))
            continue
        output.append(LINK.sub(replace_link, line))

    if first_heading:
        raise ValueError(f"Chapter heading was not found: {chapter.source}")
    neighbors = []
    if index:
        label = "前章 →" if vertical else "← 前章"
        neighbors.append(f"[{label}](#{chapters[index - 1].anchor})")
    if index + 1 < len(chapters):
        label = "← 次章" if vertical else "次章 →"
        neighbors.append(f"[{label}](#{chapters[index + 1].anchor})")
    output.extend(["", "---", "", "　|　".join(neighbors), ""])
    return "\n".join(output)


def make_book(chapters: list[Chapter], vertical: bool = False) -> str:
    preface = (ROOT / "README.md").read_text(encoding="utf-8").split("## EPUB を作成する", 1)[0]
    # The README's first heading is a repository label; Pandoc supplies the book title page.
    preface = preface.replace('# "Operating Systems: Three Easy Pieces"の日本語翻訳',
                              '# はじめに', 1)
    result = [preface.strip(), ""]
    previous_part = ""
    for index, chapter in enumerate(chapters):
        if chapter.part != previous_part:
            result.extend([f"# {chapter.part}", ""])
            previous_part = chapter.part
        result.extend([convert_chapter(chapter, chapters, index, vertical), ""])
    return "\n".join(result)


def build(output: Path, pandoc: str, vertical: bool = False) -> None:
    binary = shutil.which(pandoc)
    if binary is None:
        raise RuntimeError("Pandoc 3.x is required: https://pandoc.org/installing.html")
    version = subprocess.run([binary, "--version"], capture_output=True, text=True, check=True)
    match = re.search(r"^pandoc (\d+)\.", version.stdout)
    if not match or int(match.group(1)) < 3:
        raise RuntimeError("Pandoc 3.x or newer is required (for --split-level)")

    chapters = chapters_from_readme()
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        binary, "--from=markdown-implicit_figures", "--to=epub3", "--standalone", "--mathml",
        "--table-of-contents", "--toc-depth=3", "--split-level=2",
        "--epub-title-page=true", "--metadata=lang:ja",
        f"--metadata=title:{TITLE}",
        *(f"--metadata=author:{author}" for author in AUTHORS),
        f"--css={ROOT / 'epub.css'}",
    ]
    if vertical:
        # The second stylesheet overrides only layout, leaving typography shared.
        command.extend([f"--css={ROOT / 'epub-vertical.css'}",
                        "--metadata=page-progression-direction:rtl"])
    command.extend([f"--output={output}", "-"])
    # Input is piped: no generated Markdown or copied figures are left in the repo.
    subprocess.run(command, input=make_book(chapters, vertical), text=True, cwd=ROOT, check=True)
    print(f"Created {output} ({len(chapters)} chapters)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        help="EPUB path (default: ostep-ja.epub or ostep-ja-vertical.epub)")
    parser.add_argument("--vertical", action="store_true",
                        help="Japanese vertical right-to-left layout and page progression")
    parser.add_argument("--pandoc", default="pandoc", help="Pandoc executable (default: pandoc)")
    args = parser.parse_args()
    output = args.output or ROOT / ("ostep-ja-vertical.epub" if args.vertical else "ostep-ja.epub")
    try:
        build(output, args.pandoc, args.vertical)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"EPUB build failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
