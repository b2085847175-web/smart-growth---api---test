"""Generate answer-test YAML cases from the official quality-point workbook."""

from pathlib import Path
import re

import openpyxl
import json


WORKBOOK = Path(r"D:\Download\官方质检点最终版.xlsx")
OUTPUT = Path(__file__).resolve().parents[1] / "data" / "answer" / "official_quality_points_cases.yaml"


def slug(value: str) -> str:
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", value).strip("_")
    return text[:48] or "quality_point"


def load_points() -> list[dict[str, str]]:
    sheet = openpyxl.load_workbook(WORKBOOK, data_only=True).active
    points: list[dict[str, str]] = []
    level_one = ""
    for row in sheet.iter_rows(min_row=3, values_only=True):
        values = list(row) + [None] * 5
        if values[0]:
            level_one = str(values[0]).strip()
        point = str(values[1] or "").strip()
        if not point:
            continue
        points.append(
            {
                "level_one": level_one,
                "quality_point": point,
                "quality_type": str(values[2] or "规则").strip(),
                "source_standard": str(values[3] or "").strip(),
                "optimized_standard": str(values[4] or "").strip(),
            }
        )
    return points


def build_document(points: list[dict[str, str]]) -> dict:
    cases = []
    for point in points:
        quality_point = point["quality_point"]
        cases.append(
            {
                "name": quality_point,
                "turns": [
                    {
                        "question": f"客户咨询“{quality_point}”，请问这种情况应该怎么处理？",
                    },
                    {
                        "question": f"如果现在仍然遇到“{quality_point}”的问题，能再具体说明一下吗？",
                    },
                    {
                        "question": f"关于“{quality_point}”，请给我一个明确的处理结果。",
                        "expect": {"scene": quality_point},
                    }
                ],
            }
        )
    return {
        "suite": {
            "name": "official_quality_points",
            "mode": "sequential",
            "quality": True,
            "match_score": False,
            "final_reply_equals_chat": False,
            "turn_interval_seconds": 1,
            "quality_retries": 10,
            "quality_retry_interval_seconds": 2,
            "assertions": True,
        },
        "target_env": "dev",
        "cases": cases,
    }


def main() -> None:
    document = build_document(load_points())
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 根据 D:/Download/官方质检点最终版.xlsx 生成的官方质检点测试数据",
        "# comments 字段为维护注释；expect.scene 为二级质检点名称。",
        "suite:",
        "  name: official_quality_points",
        "  mode: sequential",
        "  quality: true",
        "  match_score: false",
        "  final_reply_equals_chat: false",
        "  turn_interval_seconds: 1",
        "  quality_retries: 10",
        "  quality_retry_interval_seconds: 2",
        "  assertions: true",
        "target_env: dev",
        "cases:",
    ]
    for case in document["cases"]:
        lines.extend(
            [
                f"  - name: {json.dumps(case['name'], ensure_ascii=False)}",
                "    turns:",
                *[
                    f"      - question: {json.dumps(turn['question'], ensure_ascii=False)}"
                    + (
                        f"\n        expect:\n          scene: {json.dumps(turn['expect']['scene'], ensure_ascii=False)}"
                        if turn.get("expect")
                        else ""
                    )
                    for turn in case["turns"]
                ],
            ]
        )
    OUTPUT.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    print(f"generated {len(document['cases'])} cases -> {OUTPUT}")


if __name__ == "__main__":
    main()
