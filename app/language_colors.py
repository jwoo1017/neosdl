"""GitHub linguist 색상표 일부 — 언어 구성 바를 GitHub 저장소 화면과 비슷하게 칠하기 위함.

전체 linguist colors.yml을 옮기지 않고 자주 나오는 언어만 하드코딩한다.
목록에 없는 언어는 이름을 해시해 안정적인(재요청해도 항상 같은) 색을 만든다.
"""

from __future__ import annotations

import colorsys
import hashlib

_KNOWN_COLORS: dict[str, str] = {
    "Python": "#3572A5",
    "JavaScript": "#f1e05a",
    "TypeScript": "#3178c6",
    "Java": "#b07219",
    "Go": "#00ADD8",
    "Rust": "#dea584",
    "C": "#555555",
    "C++": "#f34b7d",
    "C#": "#178600",
    "Ruby": "#701516",
    "PHP": "#4F5D95",
    "HTML": "#e34c26",
    "CSS": "#563d7c",
    "SCSS": "#c6538c",
    "Shell": "#89e051",
    "Dockerfile": "#384d54",
    "Vue": "#41b883",
    "Kotlin": "#A97BFF",
    "Swift": "#F05138",
    "Objective-C": "#438eff",
    "Scala": "#c22d40",
    "Elixir": "#6e4a7e",
    "Lua": "#000080",
    "Haskell": "#5e5086",
    "Dart": "#00B4AB",
    "SQL": "#e38c00",
    "PLpgSQL": "#336790",
    "Makefile": "#427819",
    "Jupyter Notebook": "#DA5B0B",
    "Vim Script": "#199f4b",
    "Perl": "#0298c3",
    "R": "#198CE7",
    "MDX": "#fcb32c",
    "Markdown": "#083fa1",
}


def color_for(language: str) -> str:
    known = _KNOWN_COLORS.get(language)
    if known:
        return known
    # 목록 밖 언어: 이름을 해시해 채도/명도를 고정한 파스텔 톤 색을 안정적으로 생성한다.
    digest = hashlib.sha256(language.encode()).hexdigest()
    hue = (int(digest[:8], 16) % 360) / 360
    r, g, b = colorsys.hsv_to_rgb(hue, 0.45, 0.75)
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))
