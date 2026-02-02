## AU9999 实时盯盘（Au99.99）说明

### 你能得到什么

- **实时价格**（轮询上金所公开接口，通过 `akshare` 封装）  
- 基于价格行为的**代理判断**：近 \(N\) 秒走势是否“更像趋势资金/机构风格”或“更像散户来回博弈”
- 如果你有逐笔成交（tick prints）CSV，可额外输出按“小/中/大单”划分的**净量**（同样是代理估计）

### 重要限制（必须读）

- 仅靠“最新价/分钟价”**无法严格判断**散户/机构的真实流入流出。要更可靠需要：逐笔成交量、盘口深度（Level2）、席位/会员单位、甚至衍生品持仓变化等。
- 本脚本输出的“散户/机构”是**基于统计特征的提示**，不是事实断言。

### 安装

```bash
python3 -m pip install -r requirements.txt
```

### 使用

```bash
# 默认盯 Au99.99（即 AU9999），每 2 秒拉一次，滚动窗口 180 秒
python3 au9999_watch.py

# 指定窗口、轮询间隔
python3 au9999_watch.py --interval 1 --window 300

# 只取一次并退出（方便脚本化）
python3 au9999_watch.py --once
```

### 如果你有逐笔成交 CSV

CSV 至少需要列：`time`/`ts`、`price`、`volume`；可选列：`side`（B/S 或 buy/sell 或 1/-1）。

```bash
python3 au9999_watch.py --trades trades.csv --lookback 600 --size-small 1000 --size-medium 5000
```

