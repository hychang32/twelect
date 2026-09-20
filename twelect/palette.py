"""政黨代表色。

中選會自己的 party_colors.json 顏色偏粉嫩（國民黨是紫藍、民進黨是淡綠），
對照地圖上一般人認知的政黨色會很怪，所以主要政黨用約定俗成的顏色覆寫，
其餘政黨沿用中選會設定，查不到的就歸為灰色。
"""

from __future__ import annotations

# 無黨籍及未經政黨推薦
INDEPENDENT = 999
NEUTRAL_GRAY = "#7B7F86"

# 約定俗成的政黨色，優先於中選會設定
OVERRIDES: dict[int, str] = {
    1: "#000095",    # 中國國民黨
    16: "#1B9431",   # 民主進步黨
    350: "#28C8C8",  # 台灣民眾黨
    90: "#FF6310",   # 親民黨
    74: "#FFDB00",   # 新黨
    286: "#FBBE01",  # 時代力量
    339: "#A73F24",  # 台灣基進
    79: "#66B32E",   # 綠黨
    95: "#8B4E9E",   # 台灣團結聯盟
    106: "#CC6688",  # 無黨團結聯盟
    INDEPENDENT: NEUTRAL_GRAY,
}

# 中選會把「顏色不詳」的政黨一律填 CDCDCD/BFBFBF，視為未指定
_PLACEHOLDER = {"#CDCDCD", "#BFBFBF", "#FFFFFF", "#000000"}


def resolve(party_code: int, cec_colors: dict[int, str]) -> str:
    if party_code in OVERRIDES:
        return OVERRIDES[party_code]
    color = (cec_colors.get(party_code) or "").upper()
    if color and color not in _PLACEHOLDER:
        return color
    return NEUTRAL_GRAY


def is_independent(party_code: int) -> bool:
    return party_code == INDEPENDENT
