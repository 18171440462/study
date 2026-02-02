# AU9999 盯盘与“散户/机构”净流入流出（估算）

这个仓库原本只有 `IBN-Net/` 相关代码。本工具新增了一个**命令行盯盘脚本**，用于轮询 AU9999 行情，并在具备“逐笔成交/盘口”数据时，按成交额分层估算“散户/机构”净流入流出。

> 重要限制：如果你的数据源**只有实时价格**、没有逐笔成交（含每笔成交价/量/时间），则**无法可靠区分**散户与机构；脚本会明确提示，并仅展示价格/涨跌与成交量等基础信息。

## 安装

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 使用（推荐先跑 mock，确认终端显示正常）

```bash
python au9999_watch.py --provider mock --interval 1
```

你也可以用样例逐笔数据（CSV）来验证“分层净流入/流出”的计算：

```bash
python au9999_watch.py --provider csv --ticks-csv sample_ticks.csv --interval 0
```

## 接入真实数据源

脚本目前内置：

- `mock`: 随机/模拟数据（可运行、用于验收）
- `csv`: 从本地逐笔 CSV 回放（用于验证分层净流入/流出）

要做到“实时盯盘 + 散户/机构流入流出”，你需要把 `au9999/providers.py` 里的 `HttpTicksProvider` 按你的数据源实现（交易所、券商、数据供应商的逐笔/盘口接口）。只要能产出统一的 `Tick` 列表即可复用现有计算逻辑。

## 免责声明

该工具只做数据展示与**近似估算**，不构成投资建议。不同数据源口径、延迟、以及阈值设定都会显著影响“散户/机构”判定结果。

