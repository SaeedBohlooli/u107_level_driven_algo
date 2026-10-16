import logging
import math
import operator
import functools
from itertools import *
import numpy as np
import pandas as pd

from trading_core.trading_ledger import TradingLedger
from trading_engine import chart_helper
from trading_utils import number_utils
from trading_utils import indicators_util
from trading_core.runtime_manager import RuntimeManager

logger = logging.getLogger(__name__)

def calculate_PDL_PDH(application_state, symbol, df, day_of_week):

    df = df[['date','open','close', 'high', 'low', 'volume']]
    # filter to that day between 9:30–16:00

    unique_days = sorted(df['date'].dt.normalize().unique())

    if len(unique_days) < 2:
        logger.warning(f"unique_days: {unique_days}")
        raise ValueError("Not enough days in DataFrame to get previous day's data")

    # --- Select the previous day ---
    prev_day = unique_days[-2]
    if symbol == 'MNQ' and day_of_week == 'Monday':  # the -1 goes to Sunday, so go to Friday
        # TODO how about if we have
        prev_day = unique_days[-3]



    # --- Filter to that day and RTH time window (09:30–16:00) ---
    mask_day = df['date'].dt.normalize() == prev_day
    mask_time = df['date'].dt.time.between(pd.to_datetime("09:30").time(), pd.to_datetime("16:00").time())

    df_rth = df[mask_day & mask_time]

    # --- Compute high and low ---
    day_high = df_rth["high"].max()
    day_low = df_rth["low"].min()

    logger.debug(f"Previous Day RTH High: {day_high}")
    logger.debug(f"Previous Day RTH Low: {day_low}" )

    application_state['levels'].setdefault(symbol, {})['PDH'] = day_high
    application_state['levels'].setdefault(symbol, {})['PDL'] = day_low

    time_frame = '1 min'
    chart_helper.add_to_drawing_objects_df(symbol=symbol, time_frame=time_frame,  object='dash', color='Blue', price_1=day_high, memo=f'PDH {day_high}', unique_id=f'{symbol}-{time_frame}-PDH' )
    chart_helper.add_to_drawing_objects_df(symbol=symbol, time_frame=time_frame,  object='dash', color='Blue', price_1=day_low, memo=f'PDL {day_low}' , unique_id=f'{symbol}-{time_frame}-LDH' )
    add_to_key_levels_df(symbol=symbol, time_frame=time_frame, key_level_name='PDH', price=day_high, memo=f'PDH {day_high}')
    add_to_key_levels_df(symbol=symbol, time_frame=time_frame, key_level_name='PDL', price=day_low, memo=f'PDH {day_high}')

    return


def all_levels_in(application_state, symbol, levels=['PDL', 'PDH', 'PMH', 'PML','5MH', '5ML']):
    # if a level is not there ,will return False

    for level in levels:
        if application_state.get('levels', {}).get(symbol,{}).get(level) is None:
            return False
    return True

def are_levels_in(application_state, symbol, levels=['PDL', 'PDH', 'PMH', 'PML','5MH', '5ML']):
    # if a level is not there ,will return False

    for level in levels:
        if application_state.get('levels',{}).get(symbol,{}).get(level) is None:
            return False
    return True

def find_add_PMH_PML(application_state, df, symbol):
    time_frame = '1 min'
    wait_until_end_of_period = True

    pml, pmh = find_session_high_and_low(df, start="04:00", end="09:29", wait_until_end_of_period= wait_until_end_of_period)
    if number_utils.is_valid_price(pmh) and number_utils.is_valid_price(pml) and pmh != -1 and pml != -1:
        application_state['levels'].setdefault(symbol, {})['PMH'] = pmh
        application_state['levels'].setdefault(symbol, {})['PML'] = pml

        chart_helper.add_to_drawing_objects_df(symbol=symbol, time_frame=time_frame, object='dash', color='Red', price_1=pml, memo=f'PML {pml}', unique_id=f'{symbol}-{time_frame}-PML')
        chart_helper.add_to_drawing_objects_df(symbol=symbol, time_frame=time_frame, object='dash', color ='Red', price_1=pmh, memo=f'PMH {pmh}', unique_id=f'{symbol}-{time_frame}-PMH')

        add_to_key_levels_df(symbol, time_frame, 'PML', pml, f'PML {pml}')
        add_to_key_levels_df(symbol, time_frame, 'PMH', pmh, f'PMH {pmh}')


    return

def find_session_high_and_low(df, start="09:30", end="09:35", wait_until_end_of_period=True):
    """
    Return the high between start and end time for the current day.
    Only calculates if the latest candle is past end time.
    """
    df = df.copy()
    # ensure datetime
    df['date'] = pd.to_datetime(df['date'])

    # get current day from latest row
    current_day = df['date'].dt.date.max()

    # check if we passed the end time
    latest_time = df['date'].max().time()
    end_time = pd.to_datetime(end).time()
    start_time = pd.to_datetime(start).time()
#  (df['date'].dt.date == current_day) &
    if latest_time > end_time or not wait_until_end_of_period:
        mask = (
                (df['date'].dt.date == current_day) &
                (df['date'].dt.time >= start_time) &
                (df['date'].dt.time <= end_time)
        )
        return df.loc[mask, 'low'].min(), df.loc[mask, 'high'].max()
        #return df.loc[mask, 'high'][-1], df.loc[mask, 'low'][-1]
    else:
        return -1, -1  # not yet past end time


def find_add_5MH_5ML(application_state, df, symbol):

    wait_until_end_of_period = True
    time_frame = '1 min'

    x5ml, x5mh = find_session_high_and_low(df, start="09:30", end="09:34", wait_until_end_of_period= wait_until_end_of_period)
    if number_utils.is_valid_price(x5mh) and number_utils.is_valid_price(x5ml) and x5mh != -1 and x5ml != -1:
        application_state['levels'].setdefault(symbol, {})['5MH'] = x5mh
        application_state['levels'].setdefault(symbol, {})['5ML'] = x5ml

        chart_helper.add_to_drawing_objects_df(symbol=symbol, time_frame=time_frame, object='dot', color='Black', price_1=x5ml, memo=f'5ML {x5ml}', unique_id=f'{symbol}-{time_frame}-5ML')
        chart_helper.add_to_drawing_objects_df(symbol=symbol, time_frame=time_frame, object='dot', color ='Black', price_1=x5mh, memo=f'5MH {x5mh}', unique_id=f'{symbol}-{time_frame}-5MH')

        add_to_key_levels_df(symbol, time_frame, '5ML', x5ml , f'5ML {x5ml}')
        add_to_key_levels_df(symbol, time_frame, '5MH', x5mh, f'5MH {x5mh}')
    return


def  add_to_key_levels_df(symbol, time_frame, key_level_name, price, memo='', unique_id=''):
    if price> 0:
        if unique_id == '':
            unique_id = f'{symbol}--{time_frame}--{key_level_name}'
        data = {'symbol': symbol , 'time_frame': time_frame, 'key_level': key_level_name, 'price': price, 'memo' : memo, 'unique_id': unique_id}
        TradingLedger.add_to_dataframe("key_levels_df",data)



def compute_indicators(app_config, application_state, symbol, df):
    if app_config.get("indicators", {}).get("enabled", False) == False:
        return df

    if RuntimeManager.is_due(f"compute_low_high_of_day-{symbol}", interval_sec=15, min_time_hhmm=930):
        compute_low_high_of_day(app_config,application_state,symbol,df)

    if RuntimeManager.should_run_once(f"compute_previous_day_close-{symbol}", min_time_hhmm=931):
        compute_previous_day_close(app_config, application_state, symbol, df)

    if RuntimeManager.should_run_once(f"compute_today_open-{symbol}", min_time_hhmm=931):
        compute_today_open(app_config, application_state, symbol, df)

    if RuntimeManager.is_due(f"compute_technical_indicators-{symbol}",
                             interval_sec=app_config.get('indicators', {}).get('calculation_interval_seconds', 60),
                             min_time_hhmm=130):
        df = indicators_util.compute_technical_indicators(app_config, application_state, symbol, df)
        if app_config.get("indicators", {}).get("print_last_few_rows", False):
            logger.info(f"[compute_technical_indicators] print_last_few_rows df: \n {df[-4:].to_markdown()}")
    return df

def compute_today_open(app_config, application_state, symbol, df):
    current_day = df['date'].dt.date.max()
    mask = (df['date'].dt.date == current_day) & (df['date'].dt.time == pd.to_datetime("09:30").time())
    df_open = df[mask]

    if df_open.empty:
        logger.warning(f"[compute_today_open] No 09:30 candle found for {symbol}")
        return

    today_open = df_open.iloc[0]['open']
    logger.debug(f"Today Open for {symbol}: {today_open}")
    application_state['levels'].setdefault(symbol, {})['TDO'] = today_open


def compute_previous_day_close(app_config, application_state, symbol, df):
    unique_days = sorted(df['date'].dt.normalize().unique())

    if len(unique_days) < 2:
        logger.warning(f"[compute_previous_day_close] Not enough days for {symbol}")
        return

    prev_day = unique_days[-2]

    mask = (df['date'].dt.normalize() == prev_day) & (df['date'].dt.time == pd.to_datetime("16:30").time())
    df_prev = df[mask]

    if df_prev.empty:
        logger.warning(f"[compute_previous_day_close] No 16:30 candle found for previous day for {symbol}")
        return

    prev_close = df_prev.iloc[-1]['close']

    logger.debug(f"Previous Day Close for {symbol}: {prev_close}")
    application_state['levels'].setdefault(symbol, {})['PDC'] = prev_close


def compute_low_high_of_day(app_config, application_state, symbol, df):

   current_day = df['date'].dt.date.max()
   start_time = pd.to_datetime("09:30").time()

   mask = (
       (df['date'].dt.date == current_day) &
       (df['date'].dt.time >= start_time)
   )

   df_today = df[mask]

   if df_today.empty:
       logger.warning(f"[compute_low_high_of_day] No data found for {symbol} today from 09:30")
       return

   day_high = df_today['high'].max()
   day_low = df_today['low'].min()

   application_state['levels'].setdefault(symbol, {})['TDH'] = day_high
   application_state['levels'].setdefault(symbol, {})['TDL'] = day_low

   return