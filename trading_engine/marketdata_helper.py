
import logging
logger = logging.getLogger(__name__)
from trading_utils import ib_marketdata_async
from trading_utils import df_utils

from trading_core.file_manager import FileManager

async def get_historical_data(ib, symbol, app_config, application_state, time_frame ='1 day', historical_days=''):
    if historical_days == '':
        historical_days = '4 D'

    logger.info(f"[get_historical_data] symbol: {symbol}, time_frame: {time_frame}, historical_days: {historical_days}")
    df = await ib_marketdata_async.get_stock_historical_data(
        ib,
        symbol,
        time_frame=time_frame,
        duration=historical_days,
        contract_month= app_config['symbols_meta'].get(symbol, {}).get('contract_month'),
        use_RTH=False
    )

    if not application_state['is_save_time']:
        logger.info(f"[get_historical_data] {symbol} start: \n{df[:2].to_markdown()}")
        logger.info(f"[get_historical_data] {symbol} end: \n{df[-2:].to_markdown()}")
    return df


def save_ohlc_for_chart(application_state, market_data, save_tabular=False):
    mode = application_state.get('mode', 'live')
    time_frame = "1 min"
    for symbol, df in market_data.dfs_map.items():
        logger.info(f"in save_ohlc_for_chart, symbol: {symbol}, len(df): {len(df)}")
        if mode == 'live':
            df = df[['date','open', 'high', 'low', 'close', 'volume', 'atr_14']]
            file_name = f"{symbol}-{time_frame.replace(' ', '')}.csv"
            FileManager.save_my_df(df, dir="charts", file_name=file_name, save_tabular=save_tabular, mode='w')
    return


def save_extra_features_df(application_state, symbol, df, relative_strength_df, intraday_rs_df, time_frame='1 min', save_tabular=False):
    extra_features_df = df.copy()
    extra_features_df = extra_features_df.merge(relative_strength_df, on='date', how='left')
    extra_features_df = extra_features_df.merge(intraday_rs_df, on='date', how='left')

    file_name = f"{symbol}-{time_frame.replace(' ', '')}-extra_features_df.csv"
    FileManager.save_my_df(extra_features_df, dir="charts", file_name=file_name, save_tabular=save_tabular, mode='w')
