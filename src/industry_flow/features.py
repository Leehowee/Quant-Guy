from __future__ import annotations

import pandas as pd


def _positive_streak(values: pd.Series) -> pd.Series:
    out = []
    streak = 0
    for value in values:
        if pd.notna(value) and value > 0:
            streak += 1
        else:
            streak = 0
        out.append(streak)
    return pd.Series(out, index=values.index, dtype="int64")


def build_features(
    history: pd.DataFrame,
    flow_column: str = "main_net_inflow",
    rank_prefix: str = "main_flow",
) -> pd.DataFrame:
    if history.empty:
        return history.copy()

    df = history.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    has_period = "period" in df.columns
    sort_columns = ["trade_date"] + (["period"] if has_period else []) + ["industry_name"]
    cross_section = ["trade_date"] + (["period"] if has_period else [])
    series_groups = (["period"] if has_period else []) + ["industry_name"]
    df = df.sort_values(sort_columns).reset_index(drop=True)

    # 横截面：1 = 当日主力净流入最高
    rank_column = f"{rank_prefix}_rank"
    df[rank_column] = (
        df.groupby(cross_section)[flow_column]
        .rank(method="min", ascending=False)
    )

    # 越接近1表示横截面越强
    df[f"{rank_prefix}_rank_pct"] = (
        df.groupby(cross_section)[flow_column]
        .rank(method="average", pct=True, ascending=True)
    )

    ratio_column = "main_net_inflow_ratio"
    if ratio_column in df.columns:
        df[f"{rank_prefix}_ratio_rank"] = (
            df.groupby(cross_section)[ratio_column]
            .rank(method="min", ascending=False)
        )

    parts = []

    for _, g in df.groupby(series_groups, sort=False):
        g = g.sort_values("trade_date").copy()
        is_daily_flow = (
            not has_period or g["period"].iloc[0] == "即时"
        )

        g["rank_change_1d"] = g[rank_column].shift(1) - g[rank_column]
        g["rank_change_3d"] = g[rank_column].shift(3) - g[rank_column]

        if is_daily_flow:
            for n in (3, 5, 10):
                sum_column = (
                    f"main_flow_sum_{n}d"
                    if flow_column == "main_net_inflow"
                    else f"{flow_column}_sum_{n}d"
                )
                g[sum_column] = g[flow_column].rolling(n, min_periods=1).sum()

            # Missing flow observations are unknown, not outflow observations.
            pos = g[flow_column].gt(0).astype(float).where(
                g[flow_column].notna()
            )
            g["positive_days_3d"] = pos.rolling(3, min_periods=1).sum()
            g["positive_days_5d"] = pos.rolling(5, min_periods=1).sum()
            g["positive_streak"] = _positive_streak(g[flow_column])
        else:
            # These rows already represent rolling 3/5/10/20-day aggregates;
            # summing overlapping windows would create a misleading feature.
            g["positive_streak"] = pd.NA

        parts.append(g)

    out = pd.concat(parts, ignore_index=True)

    return (
        out.sort_values(sort_columns)
        .reset_index(drop=True)
    )
