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

### 打印“散户/机构 买入/卖出”提示信号（需要逐笔 CSV）

脚本用 **小单净量** 代理“散户”，用 **大单净量** 代理“机构”。当净量绝对值超过阈值时触发提示，并且只在状态变化时打印一次以避免刷屏。

```bash
python3 au9999_watch.py \
  --trades trades.csv \
  --signal \
  --lookback 600 \
  --signal-threshold-retail 3000 \
  --signal-threshold-institution 3000
```

### 没有逐笔 CSV（常见）：用 AU期货(AU0) 作为代理源输出信号

如果你总是没有 `trades.csv`，可以直接开启 `--signal`。脚本会自动拉取 **上期所黄金 AU 连续(AU0) 的 1分钟数据**（含成交量与持仓 `hold`），用以下代理逻辑打印散户/机构买卖提示：

- **机构代理**：用 \( \Delta OI \)（`hold`变化） + \( \Delta P \)（价格变化）推断“疑似开多/开空/平仓”
- **散户代理**：当 \( | \Delta OI | \) 不大但“签名成交量”(SV) 明显偏向一侧时，提示“散户疑似净买/净卖”

```bash
python3 au9999_watch.py --signal

# 可调阈值（不同盘面波动强弱差异很大）
python3 au9999_watch.py \
  --signal \
  --futures-lookback 600 \
  --futures-threshold-oi 200 \
  --futures-threshold-price 0.5 \
  --futures-threshold-signed-volume 8000
```

### 如果 AU9999 现货接口持续 403/超时

上金所现货接口可能会对频繁请求或特定 IP 触发限制，表现为 **403 Forbidden** 或频繁断连。

- 默认 `--spot auto`：一旦遇到 403，会自动暂停现货请求一段时间（`--spot-block-cooldown`，默认 1800 秒），避免刷屏与加重封禁。
- 你也可以直接关闭现货请求，只看 `AU0` 期货代理：

```bash
python3 au9999_watch.py --signal --spot off
```

