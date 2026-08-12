# -*- coding: utf-8 -*-
"""获客引擎命令行入口：按用户条件自动生成→发现→筛选→迭代优化，输出策略文档与目标清单。

用法示例
--------
  # 用条件文件运行（需本地已配好 lxml + 搜索/AI 密钥）
  python scripts/run_acquisition.py --conditions conditions.json

  # 或直接内联条件（JSON 字符串）
  python scripts/run_acquisition.py --conditions '{"specs":["DWDM","WDM","玻璃管"],"products":"石英玻璃毛细管/光无源器件"}'

  # 仅做排序/筛选/导出（把已收集线索作为 seed，无需联网）
  python scripts/run_acquisition.py --conditions conditions.json --seed leads_raw.json

  # 查看帮助
  python scripts/run_acquisition.py --help

条件文件字段见 core/acquisition.normalize_conditions 的说明。
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core import acquisition  # noqa: E402


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description="AI 获客引擎：条件驱动自动生成与迭代优化目标客户清单")
    ap.add_argument("--conditions", required=True, help="条件 JSON 文件路径，或内联 JSON 字符串")
    ap.add_argument("--seed", default="", help="可选：已收集的原始线索 JSON（数组），跳过联网发现只做排序/筛选/导出")
    ap.add_argument("--out", default=os.path.join(ROOT, "outputs"), help="输出目录（默认 outputs/）")
    ap.add_argument("--max-rounds", type=int, default=3, help="迭代最大轮次（默认 3）")
    ap.add_argument("--base-name", default="acquisition", help="输出文件前缀")
    ap.add_argument("--no-ai", action="store_true", help="即使有 key 也不调用 AI 精筛（更快、免费）")
    args = ap.parse_args()

    # 解析 conditions：文件 or 内联
    cpath = args.conditions
    if os.path.exists(cpath):
        conditions = _load_json(cpath)
    else:
        try:
            conditions = json.loads(cpath)
        except Exception:
            sys.exit("无法解析 --conditions：既不是文件也不是合法 JSON 字符串")

    settings = {}
    if not args.no_ai:
        # 透传本地 server 的 settings（若可读取），否则留空（buyer 会用免费源）
        try:
            from core import db
            settings = db.get_settings()
        except Exception:
            settings = {}

    seed = None
    if args.seed:
        seed = _load_json(args.seed)
        if isinstance(seed, dict):
            seed = seed.get("candidates") or seed.get("leads") or []

    def progress(p):
        print(f"  · [{p.get('stage','')}] " +
              (f"目标 {p.get('targets','')} " if p.get("targets") is not None else "") +
              (f"缺口 {p.get('gaps','')}" if p.get("gaps") is not None else ""), flush=True)

    print("== 运行获客引擎 ==")
    result = acquisition.run_engine(
        conditions, settings=settings, max_rounds=args.max_rounds,
        progress=progress, seed_candidates=seed,
    )

    paths = acquisition.export_outputs(conditions, result, args.out, base_name=args.base_name)
    stats = result["stats"]
    print("\n== 完成 ==")
    print(f"目标客户总数：{stats['total']}（目标 ≥ {stats['target_count']}）")
    print(f"等级分布：{stats['by_tier']}")
    print(f"区域分布：{stats['by_region']}")
    print(f"买方类型：{stats['by_type']}")
    print(f"规格命中：{stats['by_spec']}")
    print(f"联系方式已核验：{stats['verified']} / {stats['total']}")
    if result["final_gaps"]:
        print(f"剩余缺口：{result['final_gaps']}")
    print("\n输出文件：")
    for k, v in paths.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
