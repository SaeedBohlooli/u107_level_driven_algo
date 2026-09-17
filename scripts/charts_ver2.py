import sys
import time

from bokeh.colors.named import chartreuse

sys.path.insert(0, f'../')
import plotly.io as pio
import plotly.graph_objects as go

import logging
import os.path
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import configparser
from plotly.subplots import make_subplots

import datetime
from utils import miscutils
from trading_utils import df_utils
from trading_utils import config_utils

# logger = logging.getLogger(__name__)
logger = miscutils.setup_logger(__name__, logging.INFO)
logger.info('g')

from flask import Flask, render_template, request
import plotly.graph_objs as go
import plotly
import json
import plotly.io as pio

app = Flask(__name__)

portfolio_id = 'p107'
configs_folder = f'../configs'
config_file = f'{configs_folder}/app-config.yaml'
portfolio_dir = f'../../portfolios/results/{portfolio_id}'
reports_dir = f'../../portfolios/reports/{portfolio_id}'
log_dir = f'../../portfolios/logs/{portfolio_id}'
detailed_log_dir = f'../../portfolios/detailed-logs/{portfolio_id}'
run_counter = 0


def load_app_config(portfolio_id):
    global app_config
    logger.warning(f"loading app_config ....")
    # app_config = config_utils.load_app_config(f'{configs_folder}/config-{portfolio_id}.yaml')
    app_config = config_utils.load_app_config(portfolio_id=portfolio_id)
    chart_config = config_utils.load_config(f'{configs_folder}/config-charts.yaml')
    app_config.update(chart_config)
    logger.info(f"loaded.")
    return app_config


def load_date_config(chart_date):
    global app_config
    logger.warning(f"loading app_config ....")
    # app_config = config_utils.load_app_config(f'{configs_folder}/config-{portfolio_id}.yaml')

    live_base_dir = f'../../portfolios/{portfolio_id}/charts/'
    date_config_path = f'{live_base_dir}/{chart_date}/config.yaml'
    app_config = config_utils.load_config(date_config_path)

    chart_config = config_utils.load_config(f'{configs_folder}/config-charts.yaml')
    app_config.update(chart_config)
    logger.info(f"loaded.")
    return app_config


def get_portfoilo_dir(portfolio_id):
    base_dir = f'../../portfolios'
    portfolio_dir = os.path.join(base_dir, 'results', portfolio_id)
    return portfolio_dir

def get_charts_dir(portfolio_id):
    # base_dir = f'../portfolios'
    # portfolio_dir = os.path.join(base_dir, 'charts', portfolio_id)
    # return portfolio_dir
    return charts_dir
def convert_time_zone(df, from_tz, to_tz):
    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    df['date'] = df['date'].dt.tz_localize(from_tz)
    df['date'] = df['date'].dt.tz_convert(to_tz)
    df['date'] = df['date'].dt.tz_localize(None)
    return df

def load_support_resistance_map_from_file():
    file_path = os.path.join(charts_dir, 'support_resistance_1min_previous_day.json')
    if os.path.exists(file_path):
        with open(file_path, 'r') as f:
            logger.info(f"loading from file_path: {file_path} ")
            support_resistance_map = json.load(f)
        logger.info(f"loaded, support_resistance_map: {support_resistance_map}")
    else:
        logger.warning(f"file isn o there ,{file_path}")
    return  support_resistance_map



def cut_df_until_hour_x_on_last_day(df, cutoff_time="13:00"):
    """
    Cut the DataFrame up to (and including) a specific time on the last day in df['date'].

    Parameters:
        df : pd.DataFrame
            Must contain a 'date' column of datetime type.
        cutoff_time : str
            Time in HH:MM (24-hour) format. Default is '13:00'.

    Returns:
        pd.DataFrame : sliced DataFrame up to cutoff_time of the last day.
    """
    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])

    # Find the last trading day in the DataFrame
    last_day = df['date'].dt.normalize().max()

    # Create masks
    mask_day = df['date'].dt.normalize() == last_day
    mask_time = df['date'].dt.time <= pd.to_datetime(cutoff_time).time()

    # Keep everything before that cutoff on the last day, and all prior days
    cut_df = df[(df['date'].dt.normalize() < last_day) | (mask_day & mask_time)]
    return cut_df


def draw_w_plotly_w_subplot_1(symbol, chart_title='title'):
    global df
    global extra_features_df
    logger.info(f"in draw_w_plotly_w_subplot:\n {df[-5:].to_markdown()}")
    df['date'] = pd.to_datetime(df['date'])


    end_time = df['date'].max() + pd.Timedelta(minutes=10)
    if len(extra_features_df)>0:
        extra_features_df['date'] = pd.to_datetime(extra_features_df['date'])


    hours_in_focus = int(app_config['chart']['hours_in_focus'])
    start_time = end_time - pd.Timedelta(hours=hours_in_focus)



    # Set 'date' as the index
    # df.set_index('date', inplace=True)

    # Create a subplot: (2 rows, shared x-axis)
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                        vertical_spacing=0.04,
                        # row_heights=[0.65, 0.05, 0.05, 0.10, 0.04, 0.04, 0.04, 0.04],
                        row_heights=[0.90, 0.05, 0.05],
                        # row_heights=[0.90, 0.05, 0.05],
                        subplot_titles=(f'{symbol}',
                                        # f'Volume Ratio {symbol}',
                                        # f'Relative Strength Relative {symbol}',
                                        # f'Relative Strength Delta {symbol}',
                                        # f'Check ... {symbol}',
                                        f'ATR-{symbol}',
                                        f'Volume-{symbol}',

                                        )
                        )

    row_in_chart = 0
    row_in_chart += 1
    # Candlestick chart
    fig.add_trace(go.Candlestick(
        x=df['date'],
        open=df['open'],
        high=df['high'],
        low=df['low'],
        close=df['close'],
        name='Candles'
    ), row=row_in_chart, col=1)

    df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
    fig.add_trace(go.Scatter( # add email for candle
        x=df['date'],
        y=df['ema_9'],
        line=dict(color='blue', dash='dot', width=1),
        name='ema_9 '
    ), row=row_in_chart, col=1)

    fig.update_xaxes(showticklabels=True, row=1, col=1)

    df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
    fig.add_trace(go.Scatter( # add email for candle
        x=df['date'],
        y=df['ema_21'],
        line=dict(color='orange', dash='dot', width=1),
        name='ema_21'
    ), row=row_in_chart, col=1)

    fig.update_xaxes(showticklabels=True, row=1, col=1)

    if False:
        # vol ratio
        row_in_chart += 1
        df['volume_sma10'] = df['volume'].rolling(window=10).mean()
        df['VR'] = df['volume'] / df['volume_sma10']
        cap = df['VR'].quantile(0.95)  # 95th percentile
        df['VR'] = df['VR'].clip(upper=cap)
        df['VR_sma3'] = df['VR'].rolling(window=3).mean()

        fig.add_trace(go.Scatter(
            x=df['date'],
            y=df['VR'],
            line=dict(color='blue', width=2),
            name='volume ratio '
        ), row=row_in_chart, col=1)

        fig.add_trace(go.Scatter( # line on 1
            x=df['date'],
            y=[1] * len(df),
            mode='lines',
            name='1 Line',
            line=dict(color='red', dash='dot', width=1),
            showlegend=False
        ), row=row_in_chart, col=1)

        fig.add_trace(go.Scatter(
            x=df['date'],
            y=df['VR_sma3'],
            line=dict(color='blue', dash='dot', width=2),
            name='VR_sma3'
        ), row=row_in_chart, col=1)

        #rs_rel
        row_in_chart += 1
        fig.add_trace(go.Scatter(
            x=extra_features_df['date'],
            y=extra_features_df['rs_rel'],
            line=dict(color='blue', width=2),
            name='rs_rel'
        ), row=row_in_chart, col=1)

        fig.add_trace(go.Scatter( # line on 0
            x=extra_features_df['date'],
            y=[0] * len(extra_features_df),
            mode='lines',
            name='Zero Line',
            line=dict(color='black', dash='dot', width=1),
            showlegend=False
        ), row=row_in_chart, col=1)

        fig.add_trace(go.Scatter( # ema
            x=extra_features_df['date'],
            y=extra_features_df['rs_rel_ema'],
            mode='lines',
            name='Zero Line',
            line=dict(color='blue', dash='dot', width=3),
            showlegend=False
        ), row=row_in_chart, col=1)


        # rs_delta
        row_in_chart += 1
        fig.add_trace(go.Scatter(
            x=extra_features_df['date'],
            y=extra_features_df['rs_delta'],
            line=dict(color='red', width=1),
            name='rs_delta'
        ), row=row_in_chart, col=1)

        # rs_delta_ema
        fig.add_trace(go.Scatter(
            x=extra_features_df['date'],
            y=extra_features_df['rs_delta_ema'],
            line=dict(color='blue', width=3, dash='dot'),
            name='rs_delta_ema'
        ), row=row_in_chart, col=1)

        fig.add_trace(go.Scatter( # line on 0
            x=extra_features_df['date'],
            y=[0] * len(extra_features_df),
            mode='lines',
            name='Zero Line',
            line=dict(color='black', dash='dot', width=1),
            showlegend=False
        ), row=row_in_chart, col=1)

        # rs_roc ---? stock_pct
        row_in_chart += 1
        cap = extra_features_df['stock_pct'].quantile(0.95)  # 95th percentile
        extra_features_df['stock_pct'] = extra_features_df['stock_pct'].clip(upper=cap)
        fig.add_trace(go.Scatter(
            x=extra_features_df['date'],
            y=extra_features_df['stock_pct'],
            line=dict(color='blue', width=2),
            name='stock_pct'
        ), row=row_in_chart, col=1)

        cap = extra_features_df['qqq_pct'].quantile(0.95)  # 95th percentile
        extra_features_df['qqq_pct'] = extra_features_df['qqq_pct'].clip(upper=cap)
        fig.add_trace(go.Scatter(
            x=extra_features_df['date'],
            y=extra_features_df['qqq_pct'],
            line=dict(color='red', width=1),
            name='qqq_pct'
        ), row=row_in_chart, col=1)

        fig.add_trace(go.Scatter( # line on 0
            x=extra_features_df['date'],
            y=[0] * len(extra_features_df),
            mode='lines',
            name='Zero Line',
            line=dict(color='black', dash='dot', width=1),
            showlegend=False
        ), row=row_in_chart, col=1)

    # ATR line chart
    row_in_chart += 1
    fig.add_trace(go.Scatter(
        x=df['date'],
        y=df['atr_14'],
        line=dict(color='orange', width=2),
        name='ATR'
    ), row=row_in_chart, col=1)

    # volume
    # Clip outlier volumes above a certain percentile
    cap = df['volume'].quantile(0.95)  # 95th percentile
    df['volume_clipped'] = df['volume'].clip(upper=cap)

    row_in_chart += 1
    fig.add_trace(go.Scatter(
        x=df['date'],
        y=df['volume_clipped'],
        line=dict(color='orange', width=2),
        name='Volume'
    ), row=row_in_chart, col=1)

    # --- Compute SMA(20) ---
    df['volume_sma20'] = df['volume'].rolling(window=20).mean()

    # --- Add SMA(20) for volume ---
    fig.add_trace(go.Scatter(
        x=df['date'],
        y=df['volume_sma20'],
        line=dict(color='blue', width=2, dash='dot'),  # dashed blue line
        name='Vol SMA 20'
    ), row=row_in_chart, col=1)

    fig.update_layout(
        title=f'{chart_title}',
        width=1900,
        height=1400,
        xaxis=dict(
            range=[start_time, end_time],  # limit slider to last 4 hours
            rangeslider=dict(
                visible=False,
            ),

        ),
        xaxis2=dict(
            range=[start_time, end_time],  # 👈 sets visible window
            rangeslider=dict(visible=False)  # ATR row
        ),
        xaxis3=dict(
            range=[start_time, end_time],  # 👈 sets visible window
            rangeslider=dict(visible=True, thickness=0.05)  # Volume row
        ),
        xaxis4=dict(
            range=[start_time, end_time],  # 👈 sets visible window
            rangeslider=dict(visible=False)  # Volume row
        ),
        xaxis5=dict(
            range=[start_time, end_time],  # 👈 sets visible window
            rangeslider=dict(visible=False)
        ),
        xaxis6=dict(
            range=[start_time, end_time],  # 👈 sets visible window
            rangeslider=dict(visible=False)
        ),
        xaxis7=dict(
            range=[start_time, end_time],  # 👈 sets visible window
            rangeslider=dict(visible=True,
                    thickness=0.07  # makes it smaller so it doesn’t overlap ATR
                    )
        )
    )

    x = 0

    fig.update_yaxes(title_text="Price", row=1, col=1, title_standoff=20, automargin=True)
    fig.update_yaxes(title_text="ATR", row=2, col=1, title_standoff=20, automargin=True)
    fig.update_yaxes(title_text="Volume", row=3, col=1, title_standoff=20, automargin=True)
    # fig.update_yaxes(title_text="RS", row=4, col=1, title_standoff=20, automargin=True)
    # fig.update_yaxes(title_text="RS", row=5, col=1, title_standoff=20, automargin=True)
    # fig.update_yaxes(title_text="RS Rel", row=6, col=1, title_standoff=20, automargin=True)
    # fig.update_yaxes(title_text="Volume Ratio", row=7, col=1, title_standoff=20, automargin=True)
    #
    # Optional: rotate x-axis labels

    return fig

# def draw_w_plotly_w_subplot(df, chart_title='title'):
#
#     logger.info(f"in draw_w_plotly_w_subplot:\n {df[-20:].to_markdown()}")
#
#     df['date'] = pd.to_datetime(df['date'])
#     end_time = df['date'].max() + pd.Timedelta(minutes=20)  # leave some space in the right ....
#     hours_in_focus = int(app_config['chart']['hours_in_focus'])
#     start_time = end_time - pd.Timedelta(hours=hours_in_focus)
#
#     # Set 'date' as the index
#     df.set_index('date', inplace=True)
#
#     # Create a subplot: (2 rows, shared x-axis)
#     fig = make_subplots(rows=1, cols=1, shared_xaxes=True,
#                         vertical_spacing=0.05,
#                         #row_heights=[0.8, 0.2],
#                         subplot_titles=(f'OHLC Chart {chart_title}', f'Volume {chart_title}'))
#
#     # Candlestick chart
#     fig.add_trace(go.Candlestick(
#         x=df['date'],
#         open=df['open'],
#         high=df['high'],
#         low=df['low'],
#         close=df['close'],
#         name='Candles'
#     ), row=1, col=1)
#
#
#     #
#     # fig.add_trace(go.Bar(
#     #     x=df['date'],
#     #     y=df["volume"],
#     #     name="Volume",
#     #     marker_color="orange"
#     # ), row=2, col=1)
#     #
#
#
#     # # add volume ...
#     # fig.add_trace(go.Scatter(
#     #     x=df['date'],
#     #     y=df['volume'],
#     #     line=dict(color='orange', width=2),
#     #     name='Volume'
#     # ), row=2, col=1)
#
#
#     #
#     # fig.update_layout(
#     #     title=f'{chart_title}',
#     #     width=1700,
#     #     height=1200)
#     #
#
#     fig.update_layout(
#         title=f'{chart_title}',
#         width=1800,
#         height=1300,
#         xaxis=dict(
#             range=[start_time, end_time],  # 👈 focus last 4 hours
#             rangeslider=dict(visible=True, thickness=0.05),
#             type="date",
#             rangebreaks=[
#                 dict(bounds=["sat", "mon"]),  # skip weekends
#                 dict(bounds=[0, 3.5], pattern="hour"),  # skip 00:00–09:30
#                 dict(bounds=[20, 24], pattern="hour"),  # skip 16:00–24:00
#             ]
#         ),
#
#     )
#     # sometimes slidebar overlaps ... this is the fix.
#     # fig.update_layout(
#     #     xaxis=dict(rangeslider=dict(visible=True)),
#     #     xaxis2=dict(rangeslider=dict(visible=False)),  # Prevent overlapping in ATR subplot
#     # )
#
#     # # fix for slide bar range ..
#     # fig.update_layout(
#     #     xaxis=dict(
#     #         rangeslider=dict(
#     #             visible=True,
#     #             range=[start_time_slider, end_time],  # limit slider to last 4 hours
#     #             thickness=0.05  # Smaller value = thinner slider (default is ~0.1)
#     #         )
#     #     )
#     # )
#     # chart is based on UTC, so we cut the chart ...
#     # fig.update_xaxes(range=[start_time, end_time], row=1, col=1)
#     # fig.update_xaxes(range=[start_time, end_time], row=2, col=1)
#     # fig.update_xaxes(showticklabels=True, row=1, col=1)  # showing X lables in the chart ...
#
#     return fig

def draw_w_plotly_w_subplot_test(df, chart_title='title'):
    import pandas as pd
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    fig = go.Figure()
    end_time = df["date"].max()
    start_time = end_time - pd.Timedelta(hours=4)
    fig.add_trace(go.Scatter(
        x=df["date"],
        y=df["close"],
        mode="lines",
        name="Price"
    ))

    # ------------------------
    # Layout: last 4 hours focused + rangeslider
    # ------------------------
    fig.update_layout(
        xaxis=dict(
            range=[start_time, end_time],  # ⬅ initial view
            rangeslider=dict(visible=True),  # scroll bar
            type="date"
        ),
        yaxis=dict(title="Price"),
        title="Example: Last 4 Hours Focused"
    )

    return fig

def draw_objects(fig, df, drawing_objects_df, symbol, time_frame):

    for i in range(len(drawing_objects_df)):

        if drawing_objects_df['symbol'].iloc[i] == symbol:# and drawing_objects_df['time_frame'].iloc[i] == time_frame:

            obj = drawing_objects_df['object'].iloc[i]
            color = drawing_objects_df['color'].iloc[i]
            price = drawing_objects_df['price_1'].iloc[i]
            price_2 = drawing_objects_df['price_2'].iloc[i]
            date = drawing_objects_df['date_1'].iloc[i]
            date_2 = drawing_objects_df['date_2'].iloc[i]

            memo = drawing_objects_df['memo'].iloc[i]


            if obj in [ "solid", "dot", "dash", "longdash", "dashdot", "longdashdot"]:
                x_vals = df['date']
                y_vals = [price] * len(x_vals)

                # Create a text list: only first point has text
                text_vals = [''] * (len(x_vals) - 1) + [memo]

                fig.add_trace(go.Scatter(
                    x=x_vals,
                    y=y_vals,
                    mode='lines+text',
                    line=dict(color=color, dash=obj),
                    text=text_vals,
                    textposition='top right',  # always on the left
                    showlegend=True,
                    name=memo
                ))
            elif obj in ['rect']:

                fig.add_shape(
                    type="rect",
                    x0=date,
                    x1=date_2,
                    y0=min(price, price_2),
                    y1=max(price, price_2),
                    fillcolor=color,  # green transparent
                    line=dict(color="orange", width=1, dash="dot"),
                )

    return fig


def add_start_finish_day(fig, df):
    logger.info(f"in add_start_finish_day")
    # Extract unique trading dates (calendar days)
    unique_days = df['date'].dt.normalize().unique()

    for day in unique_days:
        # Build timestamps for that day
        open_time = pd.Timestamp(day) + pd.Timedelta(hours=9, minutes=30)
        close_time = pd.Timestamp(day) + pd.Timedelta(hours=16)

        # Add vertical line at 9:30
        fig.add_vline(
            x=open_time.to_pydatetime(),
            line_color="green",
            line_dash="dot",
        )

        # Add vertical line at 16:00
        fig.add_vline(
            x=close_time.to_pydatetime(),
            line_color="red",
            line_dash="dot",
        )

    return fig


# def load_relative_strength_df_from_file(portfolio_id='p700', symbol='TSLA', time_frame='1min'):
#     file = f'{charts_dir}/{symbol}-{time_frame}-relative_strength.csv'
#     logger.info(f"load_relative_strength_df_from_file, reading file: {file}")
#
#     df = pd.read_csv(file)
#     df['date'] = pd.to_datetime(df['date'])
#
#     df = df[-app_config['chart']['1m_candles']:]
#
#     logger.info(f"in load_relative_strength_df_from_file, df: \n{df[-5:].to_markdown()}")
#     return df

def load_extra_features_df(portfolio_id='p700', symbol='TSLA', time_frame='1min'):
    file = f'{charts_dir}/{symbol}-{time_frame}-extra_features_df.csv'
    logger.info(f"load_extra_features_df_from_file, reading file: {file}")
    if os.path.exists(file):
        df = pd.read_csv(file)
        df  = df [-1200:]
        df['date'] = pd.to_datetime(df['date'])


        logger.info(f"[load_extra_features_df] df: \n{df[-5:].to_markdown()}")
        return df
    else:
        logger.info(f"[load_extra_features_df] file doesnt exist ...")
        return pd.DataFrame()

def load_df_from_ohlc_file(portfolio_id='p700', symbol='TSLA', time_frame='1min'):
    file = f'{charts_dir}/{symbol}-{time_frame}.csv'
    logger.info(f"load_ohlc_file_to_df, reading file: {file}")
    if not os.path.exists(file):
        return pd.DataFrame()
    df = pd.read_csv(file)
    df = df [-1200:]
    df['date'] = pd.to_datetime(df['date'])

    # if app_config['chart']['cutoff_in_hours'] !=0 :  # cut off hours ...
    #     # find the cutoff timestamp
    #     cut_off_hours = app_config['chart']['cutoff_in_hours']
    #     cutoff = df['date'].max() - pd.Timedelta(hours=cut_off_hours)
    #     logger.info(f"cutoff: {cutoff}")
    #     # keep only rows older than cutoff
    #     df = df[df['date'] < cutoff]

    logger.info(f"in load_ohlc_file_to_df, df: \n{df[-5:].to_markdown()}")
    return df

def load_file_to_drawing_objects_df():
    file = f'{get_charts_dir(portfolio_id)}/10-drawing_objects_df.csv'
    logger.info(f"reading file: {file}")
    df = pd.read_csv(file)
    logger.info(f"drawing_objects_df:\n{df[-3:].to_markdown()}")
    return df

def load_file_to_hover_df():
    file = f'{get_charts_dir(portfolio_id)}/12-hover_df.csv'
    if os.path.exists(file):
        logger.info(f"reading file: {file}")
        df = pd.read_csv(file)
        logger.info(f"[load_file_to_hover_df]:\n{df[-3:].to_markdown()}")
        return df
    else:
        return pd.DataFrame()

def load_file_to_close_levels_df():
    file = f'{get_charts_dir(portfolio_id)}/13-close_levels_df.csv'
    if os.path.exists(file):
        logger.info(f"reading file: {file}")
        df = pd.read_csv(file)
        logger.info(f"load_file_to_close_levels_df:\n{df[-3:].to_markdown()}")
        return df
    else:
        return pd.DataFrame()


# def chart_orch(df, portfolio_id='p700', symbol='TSLA', time_frame='1min'):
#     logger.info(df[-12:].to_markdown())
#
#     first_order_date = df.iloc[0]['date']
#     last_order_date = df.iloc[-1]['date']
#     logger.info(f"first_order_date: {first_order_date}, last_order_date: {last_order_date} , len(df): {len(df)} chart_1m_candles: {app_config['chart']['1m_candles']}")
#
#     # draw plots
#     # fig = draw_w_plotly_w_subplot(df, chart_title=f'{symbol}-{time_frame}')
#     fig = draw_w_plotly_w_subplot_1(chart_title=f'{symbol}-{time_frame}')
#     logger.info(f"\n{df[-10:].to_markdown()}")
#
#     return fig

def mark_market_time_only_last_one(fig,df):
    logger.info(f"in mark_market_time_only_last_one")

    last_day = df['date'].dt.normalize().max()
    market_start = last_day + pd.Timedelta(hours=9, minutes=30)
    market_end = last_day + pd.Timedelta(hours=16, minutes=0)
    fig.add_vrect(
        x0=market_start, x1=market_end,
        fillcolor="lightgreen",
        opacity=0.2,
        layer="below",
        line_width=0,
    )

    return fig

def mark_before_after_hours(fig, df):  # IS VERY SLOOW ... so we marke only last
    logger.info(f"in mark_before_after_hours")

    market_start = df["date"].dt.normalize() + pd.Timedelta(hours=9, minutes=30)
    market_end = df["date"].dt.normalize() + pd.Timedelta(hours=16, minutes=0)


    # Add shaded rectangles for pre-market / after-market
    for start, end in zip(market_start, market_end):
        fig.add_vrect(
            x0=start, x1=end,
            fillcolor="lightgreen",
            opacity=0.2,
            layer="below",
            line_width=0,
        )
    logger.info(f"in mark_before_after_hours, Done!")

    return fig

def add_hover_to_chart(fig1, hover_df):
    if len(hover_df) > 0:
        signal_x = hover_df['date'].tolist()
        signal_y = hover_df['price'].tolist()
        signals = hover_df['signals'].tolist()
        colors = hover_df['color'].tolist()
        hovertexts = hover_df['text'].tolist()
        fig1.add_trace(go.Scatter(
            x=signal_x,
            y=signal_y,
            mode='text',
            text=signals,
            hovertext=hovertexts,
            hoverinfo='text',
            textposition='top center',
            textfont=dict(size=20, color=colors),
            showlegend=False
        ))

    return fig1


def cut_df_for_live(df):
    if len(df) ==0:
        return df
    # return df

    logger.info(f"[cut_df_for_live] cutting df for mode: {mode}, original len: {len(df)} \n{df[:10].to_markdown()} \n{df[-5:].to_markdown()}")
    if mode == 'live':
        logger.info(f"[cut_df_for_live] mode: {mode}, {app_config['chart']['live']['start_time']}, {app_config['chart']['live']['end_time']}, cutting for live mode ...")
        df = df_utils.cut_df_strating_hour_x_on_last_day(df, cutoff_time=app_config['chart']['live']['start_time'] )
        df = df_utils.cut_df_until_hour_x_on_last_day(df, cutoff_time=app_config['chart']['live']['end_time'] )
        logger.info(f"[cut_df_for_live] after cutting for live mode, len: {len(df)}")
    else:
        logger.info(f"[cut_df_for_live] mode: {mode}, no cutting applied.")
    return df
def create_chart_hovered_df(hover_df, symbol):
    if len(hover_df) == 0:
        logger.info(f"[create_chart_hovered_df], hover_df is empty, returning empty df_1")
        return pd.DataFrame()
    else:
        logger.info(f"[create_chart_hovered_df], symbol: {symbol}, hover_df: {hover_df[-5:].to_markdown()}")

    df_1 = hover_df.copy()
    logger.info(f"[create_chart_hovered_df], created hover df_1:\n{df_1[-5:].to_markdown()}")

    df_1 = df_1[df_1['symbol'] == symbol]
    logger.info(f"[create_chart_hovered_df]:\n{df[-3:].to_markdown()}")

    df_1['date'] = df_1['date_1']
    df_1['price'] = df_1['price_1']
    df_1['text'] = df_1['memo']
    df_1['date'] = pd.to_datetime(df_1['date'])
    df_1 = cut_df_for_live(df_1)

    logger.info(f"[create_chart_hovered_df], after filter df_1:\n{df_1[-5:].to_markdown()}")

#  ⇗ ↛ ⇧
    # http://xahlee.info/comp/unicode_geometric_shapes.html
    # https: // en.wikipedia.org / wiki / List_of_Unicode_characters
    # colors https://stackoverflow.com/questions/72496150/user-friendly-names-for-plotly-css-colors
# ◒
    mapping = {

        'LEVEL_REPLACED': '○',

        # 'BREAKOUT': '●',
        'BREAKOUT': 'B',

        # Up / Positive
        # 'FLASH_UP': '▲',
        # 'UP': '▲',
        # 'STRONG_UP': '⏫',
        # 'BREAKOUT_UP': '🔥',

        # Down / Negative
        # 'FLASH_DOWN': '▼',
        # 'DOWN': '▼',
        # 'STRONG_DOWN': '⏬',
        # 'BREAKOUT_DOWN': '💥',

        # Neutral / Flat
        # 'NEUTRAL': '●',
        # 'FLAT': '▬',
        # 'SIDEWAYS': '○',

        # Hold / Pause
        # 'HOLD': '■',
        # 'WAIT': '⏸',

        # Exit / Stop
        # 'EXIT': '✖',
        # 'STOP': '⛔',
        # 'CANCEL': '❌',

        # Highlight / Special
        # 'ALERT': '★',
        # 'NEWS': '⚡',
        # 'VOLUME_SPIKE': '◆',

        # Highlight / Special
        # 'RETEST': '●',
        'RETEST': 'R',
        # 'RETEST_UP': '★',
        # 'RETEST_DOWN': '★',

        # 'bullish_reversal' : '◆',
        # 'bearish_reversal' : '◆',

        # ------------we use form here
        'CANDLE_TYPE': '○',

        # BUY and sell Entry
        # 'BUY_ENTRY_case_1': '■',
        # 'SELL_ENTRY_case_1': '■',

        # BUY and sell Entry

        'BUY_ENTRY': '●',
        'SELL_ENTRY': '●',

        # 'BUY_ENTRY_case_2': '●',
        # 'SELL_ENTRY_case_2': '●',
        #
        # 'BUY_ENTRY_case_3': '●',
        # 'SELL_ENTRY_case_3': '●',
        #
        # 'BUY_ENTRY_case_4': '●',
        # 'SELL_ENTRY_case_4': '●',
        #
        # 'BUY_ENTRY_case_5': '●',
        # 'SELL_ENTRY_case_5': '●',
        #
        # 'BUY_ENTRY_case_6': '●',
        # 'SELL_ENTRY_case_6': '●',
        #
        # 'BUY_ENTRY_case_7': '●',
        # 'SELL_ENTRY_case_7': '●',




        # 'SCREENING_case_1': '○',
        # 'SCREENING_case_2': '○',
        # 'SCREENING_case_3': '○',

        'ORDER_SENT': '◆',
        'STOP_LOSS_SENT': '◆',
        'TAKE_PROFIT_SENT': '◆',

        # 'BACKTEST_STOP_LOSS': '❉',
        # 'BACKTEST_TAKE_PROFIT': '◍',
        # 'BACKTEST_CLOSE_POSITION': '🞉',

        'CANDLE_INFO': '○',

        '5MH_SMALL_DOT': '.',
        # '5MH_SMALL_DOT_1': '.',
        # '5MH_SMALL_DOT_2': '.',

        '5ML_SMALL_DOT': '.',
        # '5ML_SMALL_DOT_1': '.',
        # '5ML_SMALL_DOT_2': '.',


        'ORDER_SENT': '◆',
        'TAKE_PROFIT_SENT': '✖',
        'STOP_LOSS_SENT': '✖',

        'SCORE': 'S',
        'PRICE_CLODE_TO_LEVEL': '●',
        'PRICE_CLOSE_TO_LEVEL': '●',

        # ------
        # Test 
        # 'x': '↑',
        # 'b': '↓',
        # 'b': '->',
    }

    mask = df_1["object"].str.contains("TEXT", case=False, na=False)

    # When object has 'TEXT' - > take first part of memo before '#'
    df_1.loc[mask, "signals"] = df_1["memo"].str.split("#").str[0].str.strip()

    # Otherwise - > use mapping fallback
    # Exact match first, then prefix match, then default '●'
    def resolve_signal(obj):
        s = str(obj)
        if s in mapping:
            return mapping[s]
        for key, symbol in mapping.items():
            if s.startswith(key):
                return symbol
        return "●"

    df_1.loc[~mask, "signals"] = df_1["object"].apply(resolve_signal)

    df_1 = df_1[['date', 'price', 'signals', 'color', 'text']]
    logger.info(f"[create_chart_hovered_df], created hover df_1:\n{df_1[-5:].to_markdown()}")

    return df_1


def add_close_levels_anoteation(fig1, df, close_levels_df, symbol):
    if len(close_levels_df) == 0:
        return fig1

    close_levels_df = close_levels_df
    close_levels_df = close_levels_df[close_levels_df["symbol"] == symbol]

    for idx, row in close_levels_df.iterrows():
        level1 = row["level1"]
        level2 = row["level2"]
        memo = row["memo"]
        clr = "red" if "X" in memo else "blue"
        fig1.add_annotation(
            x=df['date'].iloc[0],
            y=(level1 + level2) / 2,  # midpoint between the two levels
            text=f"{memo}", # Δ
            showarrow=False,
            font=dict(color=clr, size=10, family="Arial"),
            bgcolor="white",
            bordercolor="red",
            borderwidth=1,
        )
        # logger.info(f"add_close_levels_anoteation(), {symbol}, {df['date'].iloc[-1]} , {level1}, {level2}")

    return fig1

def add_cases_info_label(fig1, app_config):
    """
    Add individual info labels for each case to the chart.
    Each case gets its own separate annotation positioned vertically.
    Only displays on hover (does not clutter the chart).
    Preserves proper YAML structure with indentation and line breaks.
    """
    try:
        cases = app_config.get('cases', {})
        
        if not cases:
            logger.warning("[add_cases_info_label] No cases data found in app_config")
            return fig1
        
        def format_value(value, indent=0):
            """Recursively format values with proper indentation"""
            indent_str = " " * indent
            
            if isinstance(value, dict):
                lines = []
                for k, v in value.items():
                    formatted_v = format_value(v, indent + 2)
                    # If value is multi-line, keep it multi-line
                    if '\n' in str(formatted_v):
                        lines.append(f"{indent_str}{k}:")
                        lines.append(formatted_v)
                    else:
                        lines.append(f"{indent_str}{k}: {formatted_v}")
                return '\n'.join(lines)
            elif isinstance(value, list):
                lines = []
                for item in value:
                    formatted_item = format_value(item, indent)
                    # Each list item on its own line with proper indentation
                    for line in str(formatted_item).split('\n'):
                        lines.append(f"{indent_str}- {line}")
                return '\n'.join(lines)
            else:
                return str(value)
        
        # Create a separate annotation for each case
        if isinstance(cases, dict):
            case_index = 0
            for case_name, case_config in cases.items():
                # Format individual case
                case_lines = [f"CASE: {case_name}", "=" * 80]
                case_lines.append("")
                case_lines.append(f"cases:")
                case_lines.append(f"  {case_name}:")
                formatted_config = format_value(case_config, indent=4)
                case_lines.extend(formatted_config.split('\n'))
                
                # Convert to HTML with <br> for line breaks
                hover_text_lines = []
                for line in case_lines:
                    # Escape special HTML characters
                    line = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    hover_text_lines.append(line)
                
                # Join with <br> for proper line breaks in HTML
                hover_text = "<br>".join(hover_text_lines)
                # Wrap in pre tag for proper monospace formatting and left-alignment
                hover_text = f'<pre style="text-align: left; font-family: monospace; font-size: 28px; margin: 0; padding: 5px; white-space: pre-wrap; word-wrap: break-word;">{hover_text}</pre>'

                # Position each case label vertically stacked in top-left corner
                y_position = 1.0 - (case_index * 0.08)  # Offset each case vertically
                
                fig1.add_annotation(
                    x=0.0,  # top-left corner (normalized coordinates)
                    y=y_position,
                    xref="paper",
                    yref="paper",
                    text=f"[{case_index + 1}]",  # Numbered indicator
                    showarrow=False,
                    hovertext=hover_text,
                    font=dict(size=14, color="darkblue", family="Arial"),
                    xanchor="left",
                    yanchor="top",
                    xshift=15,
                    yshift=-15,
                )
                
                logger.info(f"[add_cases_info_label] Added case '{case_name}' with label [{case_index + 1}]")
                case_index += 1
        
        logger.info(f"[add_cases_info_label] All {case_index} cases added successfully (hover only)")
        return fig1
        
    except Exception as e:
        logger.error(f"[add_cases_info_label] Error adding cases info: {e}")
        import traceback
        logger.error(f"[add_cases_info_label] Traceback: {traceback.format_exc()}")
        return fig1


app_config = load_app_config(portfolio_id)  # to be accisible form every where ...
backtest_date = '20250810'
charts_dir = ''
chart_rows = 2
df = pd.DataFrame()
extra_features_df = pd.DataFrame()
mode = 'live'  # live or back_test
@app.route('/')
def index():
    global charts_dir
    global df
    global extra_features_df
    global close_levels_df
    global mode

    start_time = time.time()

    portfolio_id = 'p107'
    app_config = load_app_config(portfolio_id)
    mode = app_config['chart']['source']

    backtest_base_dir = '../../portfolios/charts-backtest'
    if not os.path.exists(backtest_base_dir):
        os.makedirs(backtest_base_dir, exist_ok=True)

    live_base_dir = f'../../portfolios/{portfolio_id}/charts/'

    available_backtest_dates = sorted([
        d for d in os.listdir(backtest_base_dir)
        if os.path.isdir(os.path.join(backtest_base_dir, d))
    ], reverse=True)  # sort newest first
    # available_backtest_dates.insert(0 , 'live') # adding live to bigiinig ...
    live_dates = sorted([
        d for d in os.listdir(live_base_dir)
        if os.path.isdir(os.path.join(live_base_dir, d))
    ], reverse=True)  # sort newest first

    available_dates = live_dates + available_backtest_dates

    chart_date = request.args.get('chart_date') # read from URL
    logger.info(f"available_dates {available_dates}")
    i = 0
    while chart_date is None or len (chart_date) != 10:
        chart_date = available_dates[i]
        i += 1


    logger.info(f"chart_date: {chart_date}")
    if len (chart_date) == 10:  # backtest date format 'YYYYMMDD' or 'YYYY-MM-DD'
        charts_dir = f'../../portfolios/{portfolio_id}/charts/{chart_date}'
        mode = 'live'
    else:
        charts_dir = f'../../portfolios/charts-backtest/{chart_date}/{portfolio_id}'
        mode = 'back_test'
    app_config = load_date_config(chart_date)
    user_name = app_config.get("user_name") or "Unknown"
    drawing_objects_df = load_file_to_drawing_objects_df()
    hover_df = load_file_to_hover_df()
    close_levels_df = load_file_to_close_levels_df()
    plots = []
    plots_with_symbols = []  # Store both symbol and plot
    logger.info(f"================== call from client run_counter: {run_counter}")

    for symbol in app_config['symbols']:
        logger.info(f"================== {symbol}")
        time_frame = '1min'

        df = load_df_from_ohlc_file(portfolio_id='p107', time_frame=time_frame, symbol=symbol)
        if len(df) == 0:
            continue

        df = cut_df_for_live(df)

        extra_features_df = load_extra_features_df(portfolio_id='p107', time_frame=time_frame, symbol=symbol)
        extra_features_df = cut_df_for_live(extra_features_df)

        fig1 = draw_w_plotly_w_subplot_1(symbol, chart_title=f'{symbol}-{time_frame}')
        fig1 = draw_objects(fig1,df, drawing_objects_df, symbol=symbol, time_frame=time_frame )
        if mode == 'back_test':
            fig1 = add_start_finish_day(fig1, df)
            fig1 = mark_market_time_only_last_one(fig1, df)
        chart_hovered_df = create_chart_hovered_df(hover_df, symbol)
        fig1 = add_hover_to_chart(fig1, chart_hovered_df)
        fig1 = add_close_levels_anoteation(fig1, df, close_levels_df, symbol)
        fig1 = add_cases_info_label(fig1, app_config)
        plot_html = pio.to_html(fig1, full_html=False)

        plots.append(plot_html)
        plots_with_symbols.append((symbol, plot_html))  # Store with symbol

    end_time = time.time()
    run_spend_time = round(end_time - start_time, 2)
    logger.warning(f'run_spend_time: {run_spend_time} seconds')

    logger.info(f"Done! {run_counter}")
    if chart_date != '':
        return render_template(
            "index.html",
            plots=plots,
            plots_with_symbols=plots_with_symbols,  # ✅ pass paired data
            symbols=app_config['symbols'],  # ✅ pass symbols for navigation
            backtest_date=chart_date,
            available_dates=available_dates,
            user_name=user_name
        )
    else:
        return render_template(
            "index.html",
            plots=plots,
            plots_with_symbols=plots_with_symbols,  # ✅ pass paired data
            symbols=app_config['symbols'],  # ✅ pass symbols for navigation
            backtest_date='',
            available_dates=[],
            user_name=user_name)  # ✅ must pass this


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(app_config['chart']['port']))
