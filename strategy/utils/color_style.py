import mplfinance as mpf

font = {
    "font.family": "sans-serif",
    "font.sans-serif": ["Helvetica Neue", "Arial", "DejaVu Sans"],
    "font.weight": "normal",
    "font.size": 15,
}

my_color_style = mpf.make_mpf_style(
    marketcolors=mpf.make_marketcolors(
        down="#A02128",
        up="#006340",
        edge="none",
        wick="black",
        volume="in",
    ),
    gridstyle="-",
    facecolor="white",
    rc=font,
)
