# AU9999 盯盘（价格行为代理）

本仓库新增了一个简易脚本用于**盯盘 AU9999（上金所 Au99.99）**并输出：

- **偏流入/偏流出**（仅基于价格上行/下行的代理判断）
- **更像散户/更像机构主导**（仅基于“趋势是否平滑、是否具方向性”的代理判断）

> 重要限制：公开分钟行情通常不包含 Level-2 盘口/逐笔成交规模分布，因此无法从“实时价格”直接、准确地区分散户与机构真实资金流向。脚本输出的是“价格行为代理指标”，只能作为辅助参考。

## 安装

```bash
pip3 install -r requirements.txt
```

## 使用

- **持续盯盘（默认每 5 秒轮询一次）**

```bash
python3 au9999_watch.py
```

- **调整轮询间隔与观察窗口**

```bash
python3 au9999_watch.py --interval 10 --window 120
```

- **只拉一次快照**

```bash
python3 au9999_watch.py --once
```

- **可选：保存到 CSV**

```bash
python3 au9999_watch.py --csv-out au9999_ticks.csv
```

