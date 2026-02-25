import base64
import io
from typing import Annotated

import matplotlib
import matplotlib.pyplot as plt
import mplfinance as mpf
import numpy as np
import pandas as pd
import talib
from langchain_core.tools import tool

from . import color_style as color

matplotlib.use("Agg")


def check_trend_line(support: bool, pivot: int, slope: float, y: np.array):
    intercept = -slope * pivot + y.iloc[pivot]
    line_vals = slope * np.arange(len(y)) + intercept
    diffs = line_vals - y

    if support and diffs.max() > 1e-5:
        return -1.0
    elif not support and diffs.min() < -1e-5:
        return -1.0

    err = (diffs**2.0).sum()
    return err


def optimize_slope(support: bool, pivot: int, init_slope: float, y: np.array):
    slope_unit = (y.max() - y.min()) / len(y)
    opt_step = 1.0
    min_step = 0.0001
    curr_step = opt_step

    best_slope = init_slope
    best_err = check_trend_line(support, pivot, init_slope, y)
    assert best_err >= 0.0

    get_derivative = True
    derivative = None
    while curr_step > min_step:
        if get_derivative:
            slope_change = best_slope + slope_unit * min_step
            test_err = check_trend_line(support, pivot, slope_change, y)
            derivative = test_err - best_err

            if test_err < 0.0:
                slope_change = best_slope - slope_unit * min_step
                test_err = check_trend_line(support, pivot, slope_change, y)
                derivative = best_err - test_err

            if test_err < 0.0:
                raise Exception("Derivative failed. Check your data. ")

            get_derivative = False

        if derivative > 0.0:
            test_slope = best_slope - slope_unit * curr_step
        else:
            test_slope = best_slope + slope_unit * curr_step

        test_err = check_trend_line(support, pivot, test_slope, y)
        if test_err < 0 or test_err >= best_err:
            curr_step *= 0.5
        else:
            best_err = test_err
            best_slope = test_slope
            get_derivative = True

    return (best_slope, -best_slope * pivot + y.iloc[pivot])


def fit_trendlines_single(data: np.array):
    x = np.arange(len(data))
    coefs = np.polyfit(x, data, 1)
    line_points = coefs[0] * x + coefs[1]

    upper_pivot = (data - line_points).argmax()
    lower_pivot = (data - line_points).argmin()

    support_coefs = optimize_slope(True, lower_pivot, coefs[0], data)
    resist_coefs = optimize_slope(False, upper_pivot, coefs[0], data)

    return (support_coefs, resist_coefs)


def fit_trendlines_high_low(high: np.array, low: np.array, close: np.array):
    x = np.arange(len(close))
    coefs = np.polyfit(x, close, 1)
    line_points = coefs[0] * x + coefs[1]
    upper_pivot = (high - line_points).argmax()
    lower_pivot = (low - line_points).argmin()

    support_coefs = optimize_slope(True, lower_pivot, coefs[0], low)
    resist_coefs = optimize_slope(False, upper_pivot, coefs[0], high)

    return (support_coefs, resist_coefs)


def get_line_points(candles, line_points):
    idx = candles.index
    line_i = len(candles) - len(line_points)
    assert line_i >= 0
    points = []
    for i in range(line_i, len(candles)):
        points.append((idx[i], line_points[i - line_i]))
    return points


def split_line_into_segments(line_points):
    return [[line_points[i], line_points[i + 1]] for i in range(len(line_points) - 1)]


class TechnicalTools:

    @staticmethod
    @tool
    def generate_trend_image(
        kline_data: Annotated[
            dict,
            "Dictionary containing OHLCV data with keys 'Datetime', 'Open', 'High', 'Low', 'Close'.",
        ]
    ) -> dict:
        """
        Generate a candlestick chart with trendlines from OHLCV data,
        save it locally as 'trend_graph.png', and return a base64-encoded image.
        """
        data = pd.DataFrame(kline_data)
        candles = data.iloc[-50:].copy()

        candles["Datetime"] = pd.to_datetime(candles["Datetime"])
        candles.set_index("Datetime", inplace=True)

        support_coefs_c, resist_coefs_c = fit_trendlines_single(candles["Close"])
        support_coefs, resist_coefs = fit_trendlines_high_low(
            candles["High"], candles["Low"], candles["Close"]
        )

        support_line_c = support_coefs_c[0] * np.arange(len(candles)) + support_coefs_c[1]
        resist_line_c = resist_coefs_c[0] * np.arange(len(candles)) + resist_coefs_c[1]
        support_line = support_coefs[0] * np.arange(len(candles)) + support_coefs[1]
        resist_line = resist_coefs[0] * np.arange(len(candles)) + resist_coefs[1]

        s_seq = get_line_points(candles, support_line)
        r_seq = get_line_points(candles, resist_line)
        s_seq2 = get_line_points(candles, support_line_c)
        r_seq2 = get_line_points(candles, resist_line_c)

        s_segments = split_line_into_segments(s_seq)
        r_segments = split_line_into_segments(r_seq)
        s2_segments = split_line_into_segments(s_seq2)
        r2_segments = split_line_into_segments(r_seq2)

        all_segments = s_segments + r_segments + s2_segments + r2_segments
        colors = (
            ["white"] * len(s_segments)
            + ["white"] * len(r_segments)
            + ["blue"] * len(s2_segments)
            + ["red"] * len(r2_segments)
        )

        apds = [
            mpf.make_addplot(support_line_c, color="blue", width=1, label="Close Support"),
            mpf.make_addplot(resist_line_c, color="red", width=1, label="Close Resistance"),
        ]

        fig, axlist = mpf.plot(
            candles,
            type="candle",
            style=color.my_color_style,
            addplot=apds,
            alines=dict(alines=all_segments, colors=colors, linewidths=1),
            returnfig=True,
            figsize=(12, 6),
            block=False,
        )

        axlist[0].set_ylabel("Price", fontweight="normal")
        axlist[0].set_xlabel("Datetime", fontweight="normal")

        fig.savefig("trend_graph.png", format="png", dpi=600, bbox_inches="tight", pad_inches=0.1)
        plt.close(fig)

        axlist[0].legend(loc="upper left")

        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        buf.seek(0)
        img_b64 = base64.b64encode(buf.read()).decode("utf-8")
        plt.close(fig)

        return {
            "trend_image": img_b64,
            "trend_image_description": "Trend-enhanced candlestick chart with support/resistance lines.",
        }

    @staticmethod
    @tool
    def generate_kline_image(
        kline_data: Annotated[
            dict,
            "Dictionary containing OHLCV data with keys 'Datetime', 'Open', 'High', 'Low', 'Close'.",
        ],
    ) -> dict:
        """
        Generate a candlestick (K-line) chart from OHLCV data, save it locally, and return a base64-encoded image.
        """
        df = pd.DataFrame(kline_data)
        df = df.tail(40)

        df.to_csv("record.csv", index=False, date_format="%Y-%m-%d %H:%M:%S")
        try:
            df.index = pd.to_datetime(df["Datetime"], format="%Y-%m-%d %H:%M:%S")
        except ValueError:
            print("ValueError at graph_util.py\n")

        fig, axlist = mpf.plot(
            df[["Open", "High", "Low", "Close"]],
            type="candle",
            style=color.my_color_style,
            figsize=(12, 6),
            returnfig=True,
            block=False,
        )
        axlist[0].set_ylabel("Price", fontweight="normal")
        axlist[0].set_xlabel("Datetime", fontweight="normal")

        fig.savefig(fname="kline_chart.png", dpi=600, bbox_inches="tight", pad_inches=0.1)
        plt.close(fig)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=600, bbox_inches="tight", pad_inches=0.1)
        plt.close(fig)

        buf.seek(0)
        img_b64 = base64.b64encode(buf.read()).decode("utf-8")

        return {
            "pattern_image": img_b64,
            "pattern_image_description": "Candlestick chart saved locally and returned as base64 string.",
        }

    @staticmethod
    @tool
    def compute_rsi(
        kline_data: Annotated[dict, "Dictionary with a 'Close' key containing a list of float closing prices."],
        period: Annotated[int, "Lookback period for RSI calculation (default is 14)"] = 14,
    ) -> dict:
        """Compute the Relative Strength Index (RSI) using TA-Lib."""
        df = pd.DataFrame(kline_data)
        rsi = talib.RSI(df["Close"], timeperiod=period)
        return {"rsi": rsi.fillna(0).round(2).tolist()[-28:]}

    @staticmethod
    @tool
    def compute_macd(
        kline_data: Annotated[dict, "Dictionary with a 'Close' key containing a list of float closing prices."],
        fastperiod: Annotated[int, "Fast EMA period"] = 12,
        slowperiod: Annotated[int, "Slow EMA period"] = 26,
        signalperiod: Annotated[int, "Signal line EMA period"] = 9,
    ) -> dict:
        """Compute the Moving Average Convergence Divergence (MACD) using TA-Lib."""
        df = pd.DataFrame(kline_data)
        macd, macd_signal, macd_hist = talib.MACD(
            df["Close"], fastperiod=fastperiod, slowperiod=slowperiod, signalperiod=signalperiod,
        )
        return {
            "macd": macd.fillna(0).round(2).tolist(),
            "macd_signal": macd_signal.fillna(0).round(2).tolist()[-28:],
            "macd_hist": macd_hist.fillna(0).round(2).tolist()[-28:],
        }

    @staticmethod
    @tool
    def compute_stoch(
        kline_data: Annotated[dict, "Dictionary with 'High', 'Low', and 'Close' keys."]
    ) -> dict:
        """Compute the Stochastic Oscillator %K and %D using TA-Lib."""
        df = pd.DataFrame(kline_data)
        stoch_k, stoch_d = talib.STOCH(
            df["High"], df["Low"], df["Close"],
            fastk_period=14, slowk_period=3, slowd_period=3,
        )
        return {
            "stoch_k": stoch_k.fillna(0).round(2).tolist()[-28:],
            "stoch_d": stoch_d.fillna(0).round(2).tolist()[-28:],
        }

    @staticmethod
    @tool
    def compute_roc(
        kline_data: Annotated[dict, "Dictionary with a 'Close' key containing a list of float closing prices."],
        period: Annotated[int, "Number of periods over which to calculate ROC (default is 10)"] = 10,
    ) -> dict:
        """Compute the Rate of Change (ROC) indicator using TA-Lib."""
        df = pd.DataFrame(kline_data)
        roc = talib.ROC(df["Close"], timeperiod=period)
        return {"roc": roc.fillna(0).round(2).tolist()[-28:]}

    @staticmethod
    @tool
    def compute_willr(
        kline_data: Annotated[dict, "Dictionary with 'High', 'Low', and 'Close' keys."],
        period: Annotated[int, "Lookback period for Williams %R"] = 14,
    ) -> dict:
        """Compute the Williams %R indicator using TA-Lib."""
        df = pd.DataFrame(kline_data)
        willr = talib.WILLR(df["High"], df["Low"], df["Close"], timeperiod=period)
        return {"willr": willr.fillna(0).round(2).tolist()[-28:]}
