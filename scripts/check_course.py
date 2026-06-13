#!/usr/bin/env python3
"""Check stable structural requirements for the multi-chapter course."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_CHAPTERS = 24
CHAPTER_PATTERN = re.compile(r"^s(\d{2})_[a-z0-9_]+$")
REQUIRED_ROOT_FILES = [
    "AGENTS.md",
    "Plan.md",
    "Progress.md",
    "README.md",
    "docs/Decisions.md",
    "docs/SourceMap.md",
]


def main() -> int:
    errors: list[str] = []

    for relative_path in REQUIRED_ROOT_FILES:
        if not (ROOT / relative_path).is_file():
            errors.append(f"missing required file: {relative_path}")

    chapters = sorted(
        path
        for path in ROOT.iterdir()
        if path.is_dir() and CHAPTER_PATTERN.match(path.name)
    )
    if len(chapters) != EXPECTED_CHAPTERS:
        errors.append(
            f"expected {EXPECTED_CHAPTERS} chapter directories, found {len(chapters)}"
        )

    numbers = [int(CHAPTER_PATTERN.match(path.name).group(1)) for path in chapters]
    if numbers != list(range(1, EXPECTED_CHAPTERS + 1)):
        errors.append(f"chapter numbers are not contiguous: {numbers}")

    for chapter in chapters:
        readme = chapter / "README.md"
        if readme.exists():
            text = readme.read_text(encoding="utf-8")
            first_h2 = text.find("\n## ")
            diagram = text.find("```mermaid")
            if diagram == -1 or (first_h2 != -1 and diagram > first_h2):
                errors.append(f"{chapter.name}/README.md must begin with a Mermaid diagram")
            if "状态：待编写" not in text:
                source_notes = chapter / "SOURCE_NOTES.md"
                if not source_notes.is_file():
                    errors.append(
                        f"{chapter.name} is completed but missing SOURCE_NOTES.md"
                    )
                else:
                    notes = source_notes.read_text(encoding="utf-8")
                    for heading in (
                        "## 研究快照",
                        "## 实际阅读",
                        "## 从源码确认的事实",
                        "## 教学实现的简化",
                        "## 未确认与不写入正文的内容",
                    ):
                        if heading not in notes:
                            errors.append(
                                f"{chapter.name}/SOURCE_NOTES.md missing heading: {heading}"
                            )

    if errors:
        print("Course structure check failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"Course structure OK: {len(chapters)} chapters and required handoff files found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
