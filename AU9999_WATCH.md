# AU9999 实时盯盘（启发式流入/流出 & 散户/机构提示）

这个小工具会轮询公开行情源（默认：Sina `SGE_AU9999`），输出 AU9999 的最新价、成交额/成交量增量、盘口深度不平衡，并给出**启发式**的“流入/流出”和“更像机构/更像散户”的提示。

> 重要限制：公开行情通常不提供“参与者级别资金流”（散户/机构真实流入流出）。本工具的 `participant_hint` 只是基于**成交额/盘口深度的异常波动**做的推断，不能当作真实资金流或交易建议。

## 运行

一次拉取：

```bash
python3 au9999_watch.py --once
```

持续盯盘（每 2 秒一条）：

```bash
python3 -m au9999_watch --interval 2
```

JSON Lines 输出（方便你接入数据库/可视化）：

```bash
python3 -m au9999_watch --json
```

## 输出字段（人类可读模式）

- `last`: 最新价
- `dLast`: 与上一条相比的价格变化
- `dAmt`: 与上一条相比的成交额增量（来自行情源累计额的差分）
- `dVol`: 与上一条相比的成交量增量（来自行情源累计量的差分）
- `imb`: 一档盘口深度不平衡 \((bid\_vol-ask\_vol)/(bid\_vol+ask\_vol)\)，越接近 1 表示买盘堆得更厚
- `flow`: `inflow/outflow/neutral/unknown`（启发式）
- `hint`: `institution_like/retail_like/unknown`（启发式）

## 启发式规则（简述）

- **流入/流出**：若 `dAmt>0` 且价格上涨 → `inflow`；若 `dAmt>0` 且价格下跌 → `outflow`；否则 `neutral/unknown`
- **机构/散户提示**：在滚动窗口内，若 `dAmt` 或 `bid_vol+ask_vol` 出现显著“尖峰”（基于 median + k*MAD 的鲁棒阈值），输出 `institution_like`，否则 `retail_like`；历史不足输出 `unknown`

