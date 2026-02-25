from typing import Annotated, List, TypedDict

from langchain_core.messages import BaseMessage


class IndicatorAgentState(TypedDict):
    """State type for the multi-agent trading graph."""

    kline_data: Annotated[dict, "OHLCV dictionary used for computing technical indicators"]
    time_frame: Annotated[str, "time period for k line data provided"]
    stock_name: Annotated[dict, "stock name for prompt"]

    rsi: Annotated[List[float], "Relative Strength Index values"]
    macd: Annotated[List[float], "MACD line values"]
    macd_signal: Annotated[List[float], "MACD signal line values"]
    macd_hist: Annotated[List[float], "MACD histogram values"]
    stoch_k: Annotated[List[float], "Stochastic Oscillator %K values"]
    stoch_d: Annotated[List[float], "Stochastic Oscillator %D values"]
    roc: Annotated[List[float], "Rate of Change values"]
    willr: Annotated[List[float], "Williams %R values"]
    indicator_report: Annotated[str, "Final indicator agent summary report"]

    pattern_image: Annotated[str, "Base64-encoded K-line chart for pattern recognition"]
    pattern_image_filename: Annotated[str, "Local file path to saved K-line chart image"]
    pattern_image_description: Annotated[str, "Brief description of the generated K-line image"]
    pattern_report: Annotated[str, "Final pattern agent summary report"]

    trend_image: Annotated[str, "Base64-encoded trend-annotated chart"]
    trend_image_filename: Annotated[str, "Local file path to saved trendline chart image"]
    trend_image_description: Annotated[str, "Brief description of trendline chart"]
    trend_report: Annotated[str, "Final trend analysis summary"]

    analysis_results: Annotated[str, "Computed result of the analysis or decision"]
    messages: Annotated[List[BaseMessage], "List of chat messages used in LLM prompt construction"]
    decision_prompt: Annotated[str, "decision prompt for reflection"]
    final_trade_decision: Annotated[str, "Final BUY or SELL decision"]
