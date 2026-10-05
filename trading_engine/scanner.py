import logging
import datetime
import traceback

from trading_core.trading_ledger import TradingLedger


logger = logging.getLogger(__name__)
from trading_utils import ib_contract, ib_pricing_async
from trading_utils import date_utils
from trading_engine import chart_helper
from trading_core.runtime_manager import RuntimeManager
def check_buy_sell_condition(ib, app_config, application_state, case, symbol, market_data):

    can_buy = False
    can_sell = False
    can_buy_cores = False
    can_sell_cores = False
    res_str = ''
    long_level = -1
    short_level = -1
    long_breakout_idx = 0
    long_retest_idx = 0
    short_breakout_idx = 0
    short_retest_idx = 0
    entry_breakout_idx = 0
    entry_retest_idx = 0
    failed_conditions_map = {}
    c_i = -1
    result_only_false= {}

    try:
        df = market_data.dfs_map.get(symbol)
        if df is None:
            logger.warning(f"[check_buy_sell_condition], no market data for symbol: {symbol}")
            return None

        qqq_df = market_data.dfs_map.get('QQQ') # used in config
        if qqq_df is None:
            logger.warning(f"[check_buy_sell_condition], no market data for QQQ")
            return None
        logger.info(f"[check_buy_sell_condition] {symbol}, qqq_open: {qqq_df['open'].iloc[-1]}, qqq_close: {qqq_df['close'].iloc[-1]}, qqq_date: {qqq_df['date'].iloc[-1]}, df_date: {df['date'].iloc[-1]}  unique_run_number: {application_state.get('unique_run_number')}")

        precondition = app_config['cases'][case]['precondition']
        precondition_eval = eval(precondition)
        if not precondition_eval:
            logger.warning(f"[check_buy_sell_condition], precondition not met for case: {case}, symbol: {symbol}, precondition: {precondition}")
            return None
        levels = application_state['levels'][symbol] # used in config

        can_replace_level = app_config['cases'][case]['can_replace_level']
        long_level = eval(app_config['cases'][case]['long']['level'])
        short_level = eval(app_config['cases'][case]['short']['level'])

        long_level = replace_level_if_needed(application_state, app_config, df, symbol, 'up', can_replace_level, long_level)
        short_level = replace_level_if_needed(application_state, app_config, df, symbol, 'down', can_replace_level, short_level)

        min_required_move_from_level = app_config['symbols_meta'].get(symbol,{}).get('min_required_move_from_level',0.15)  # used in config
        price = df['close'].iloc[-1]  # used in config
        atr_14 = df['atr_14'].iloc[-2]  # used in config
        skip_level_closeness_enabled = app_config.get("xui_symbol_controls", {}).get("skip_level_closeness_enabled", False)  # used in config
        skip_pdhl_and_qqq_check_enabled = app_config.get("xui_symbol_controls", {}).get("skip_pdhl_and_qqq_check_enabled", False)  # used in config

        logger.debug(f"[check_buy_sell_condition], levels: {levels}")
        evaluated_conditions_map = {}
        for side in ['long', 'short']:
            level_alias = app_config['cases'][case][side]['level_alias'] # used in config
            c_i = 0
            failed_conditions = f"<BR>{side}"
            result_only_false_str = ""
            for condition in app_config['cases'][case][side]['cores']:
                c_i = c_i + 1
                logger.debug(f"[check_buy_sell_condition] {c_i}), {symbol}, case: {case}, side: {side}, condition: {condition} ")
                evaluated = eval(condition)
                logger.debug(f"[check_buy_sell_condition] {c_i}), {symbol}, case: {case}, side: {side}, evaluated: {evaluated},  condition: {condition} ")
                evaluated_conditions_map.setdefault(side, {}).setdefault('valuated_conditions',[]).append(evaluated)
                evaluated_conditions_map.setdefault(side, {}).setdefault('valuated_conditions_cores',[]).append(evaluated)
                if not evaluated:
                    failed_conditions = f"{failed_conditions}<BR> {c_i} {condition}"
                    result_only_false_str = f"{result_only_false_str}|{c_i}: F"

            for condition in app_config['cases'][case][side].get('extras', []):
                c_i = c_i + 1
                logger.debug(f"[check_buy_sell_condition] {c_i}), {symbol}, case: {case}, side: {side}, condition: {condition} ")
                evaluated = eval(condition)
                logger.debug(f"[check_buy_sell_condition] {c_i}), {symbol}, case: {case}, side: {side}, evaluated: {evaluated},  condition: {condition} ")
                evaluated_conditions_map.setdefault(side, {}).setdefault('valuated_conditions',[]).append(evaluated)
                evaluated_conditions_map.setdefault(side, {}).setdefault('valuated_conditions_extras',[]).append(evaluated)
                if not evaluated:
                    failed_conditions = f"{failed_conditions}<BR> {c_i} {condition}"
                    result_only_false_str = f"{result_only_false_str}|{c_i}: F"

            failed_conditions_map[side] = failed_conditions
            result_only_false[side] = result_only_false_str

        if all(evaluated_conditions_map.get('long', {}).get('valuated_conditions', [])):
            can_buy = True
        if all(evaluated_conditions_map.get('short', {}).get('valuated_conditions', [])):
            can_sell = True

        if all(evaluated_conditions_map.get('long', {}).get('valuated_conditions_cores', [])):
            can_buy_cores = True
        if all(evaluated_conditions_map.get('short', {}).get('valuated_conditions_cores', [])):
            can_sell_cores = True

        logger.info(f"[check_buy_sell_condition] {case}, {symbol}, {can_buy}, {can_sell}")
        logger.info(f"[check_buy_sell_condition] {case}, {symbol}, can_buy: {can_buy}, ")
        logger.info(f"[check_buy_sell_condition] {case}, {symbol}, can_sell: {can_sell}")

        long_breakout_idxs = get_break_out_indices_by_level_set(application_state, symbol, long_level)
        long_retest_idxs = get_retest_indices_by_level_set(application_state, symbol, long_level)


        short_breakout_idxs = get_break_out_indices_by_level_set(application_state, symbol, short_level)
        short_retest_idxs = get_retest_indices_by_level_set(application_state, symbol, short_level)

        result_long =  ",".join(f"{i + 1}:{val}" for i, val in enumerate(evaluated_conditions_map.get('long', {}).get('valuated_conditions', [])))
        result_short = ",".join(f"{i + 1}:{val}" for i, val in enumerate(evaluated_conditions_map.get('short', {}).get('valuated_conditions', [])))

        long_breakout_idx = get_breakout_idx(application_state, case, symbol, long_level)
        short_breakout_idx = get_breakout_idx(application_state, case, symbol, short_level)

        long_retest_idx = get_retest_idx(application_state, case, symbol, long_level)
        short_retest_idx = get_retest_idx(application_state, case, symbol, short_level)

        logger.info(f"[check_buy_sell_condition] breakout_idxs, {case}, {symbol}, can_buy: {long_breakout_idxs}, {long_retest_idxs}")
        logger.info(f"[check_buy_sell_condition] breakout_idxs, {case}, {symbol}, can_sell: {short_breakout_idxs}, {short_retest_idxs}")

        logger.info(f"[check_buy_sell_condition] idx, {case}, {symbol}, can_buy: {long_breakout_idx}, {long_retest_idx}")
        logger.info(f"[check_buy_sell_condition] idx, {case}, {symbol}, can_sell: {short_breakout_idx}, {short_retest_idx}")

        # This is shown in the chart ...

        res_str = (f"res_{case}:<br>"
                   f"long: {result_only_false.get('long', '')} .. {long_breakout_idxs}.{long_retest_idxs} <br>"
                   f"short: {result_only_false.get('short', '')} .. {short_breakout_idxs}.{short_retest_idxs} <br>"
                   f"long_breakout: {long_breakout_idx}, long_retest: {long_retest_idx} <br>"
                   f"short_breakout: {short_breakout_idx}, short_retest: {short_retest_idx} <br>"
                   # f"price: {df['close'].iloc[-1]} at {date_utils.time_now_yyyy_mm_dd_hh_mm_ss()} end.<br>"
                   f"{df['date'].iloc[-1].strftime('%H:%M')}")
        res_str = res_str.replace('True', 'T')
        res_str = res_str.replace('False', 'F')

        res_str_log = res_str.replace('<br>', '\n')
        logger.info(f"[buy_sell_case_results_details] res_str:\n{res_str_log}")

        application_state.setdefault('buy_sell_case_results_details', {}).setdefault(symbol, {})[case] = {
            'can_buy': can_buy,
            'can_sell': can_sell,
            'res_str': res_str,
        }
        if can_buy:
            entry_breakout_idx = long_breakout_idx
            entry_retest_idx = long_retest_idx
        elif can_sell:
            entry_breakout_idx = short_breakout_idx
            entry_retest_idx = short_retest_idx

    except Exception as e:
        logger.error(f"[check_buy_sell_condition] @@ {symbol} {case} c_i: {c_i} error {e}")
        logger.error(traceback.format_exc())
        res_str = f'res_{case}'
    details_map = {
        'symbol': symbol,
        'can_buy': can_buy,
        'can_sell': can_sell,
        'can_buy_cores': can_buy_cores,
        'can_sell_cores': can_sell_cores,
        'res_str': res_str,
        'long_level': long_level,
        'short_level': short_level,
        'long_breakout_idx': long_breakout_idx,
        'long_retest_idx': long_retest_idx,
        'short_breakout_idx': short_breakout_idx,
        'short_retest_idx': short_retest_idx,
        'entry_breakout_idx': entry_breakout_idx,
        'entry_retest_idx': entry_retest_idx,
        'failed_conditions': failed_conditions_map
    }
    return case, can_buy, can_sell, details_map


def check_buy_and_sell_cases(ib, app_config, application_state, symbol, market_data):
    buy_sell_case_results = []
    for case in app_config['cases']:
        if case in app_config['live_cases']:
            res = check_buy_sell_condition(ib, app_config, application_state, case, symbol, market_data)
            if res is not None: # if precondition not met, we get None
                buy_sell_case_results.append(res)

    return buy_sell_case_results



def replace_level_if_needed(application_state, app_config, df, symbol, side, can_replace_level, level):
    # If two levels are close, we replace with next one ...

    if not can_replace_level:
        return level
    closeness_distance = eval(app_config['closeness_distance'])
    levels = application_state.get('levels', {}).get(symbol, {})
    next_level = get_next_level(side, level, levels)

    if side == 'up':
        if next_level > level and abs(next_level - level) < closeness_distance:
            logger.info(f"[replace_level_if_needed] level is replaced,{symbol}, {side}, level: {level}, next_level: {next_level}, {df['date'].iloc[-1]}")
            price = chart_helper.get_stacked_mark_price(app_config, application_state, symbol, side='up', price=df['high'].iloc[-1], date=df['date'].iloc[-1], caller_key="replace_level_if_needed")
            TradingLedger.add_to_list("signals", (symbol, 'LEVEL_REPLACED', price, df['date'].iloc[-1], f'level is replaced. from: {level}, to: {next_level}') )
            return next_level
    else:
        if next_level < level and abs(next_level - level) < closeness_distance:
            logger.info(f"[replace_level_if_needed] level is replaced, {symbol}, {side}, level: {level}, next_level: {next_level}, {df['date'].iloc[-1]}")
            price = chart_helper.get_stacked_mark_price(app_config, application_state, symbol, 'up', df['high'].iloc[-1], df['date'].iloc[-1], caller_key="replace_level_if_needed")
            TradingLedger.add_to_list("signals", (symbol, 'LEVEL_REPLACED', price, df['date'].iloc[-1], f'level is replaced. from: {level}, to: {next_level}') )
            return next_level
    return level


def get_next_level(side, level, levels):

    if side == 'up':
        next_level = levels.get('PDH', -1)
    else:
        next_level = levels.get('PDL', -1)

    return next_level



def breakout_in_last_x_candles_ver_2(app_config, application_state, case, symbol, df, side='up', idx_list=[-2], level=0, level_alias=''):

    logger.debug(f"[in breakout_in_last_x_candles], symbol: {symbol}, idx_list: {idx_list}, level:{level}")

    if level == 0:
        return False
    gap = app_config['symbols_meta'].get(symbol,{}).get('breakout_confirmation_distance', 0.15)
    breakout_happened = False

    for idx in idx_list:
        row = df.iloc[idx]
        previous = df.iloc[idx-1]
        # --- Breakout detection ---
        if date_utils.get_hhmm_int(row['date']) < 930: # we dont want breaks before 930
            continue

        # --- breakout condition ---
        if side == 'up':
            cond_1 = (row["low"] <= level and row["close"] > level + gap)    # The price above level + gap
            cond_2 = (previous["open"] < level and row["close"] > level + gap)  # The prev open is below level and current above the level.
            cond_3 = (previous["open"] < level and row["open"] > level and row["close"] > level)  # The prev open is below level and current open and close are above the level.

        else:
            cond_1 = (row["high"] >= level and row["close"] < level - gap)
            cond_2 = (previous["open"] > level and row["close"] < level - gap)
            cond_3 = (previous["open"] > level and row["open"] < level and row["close"] < level)

        breakout = (cond_1 or cond_2 or cond_3)
        if not breakout:
            continue


        # --- candle body confirmation ---
        body = abs(row["close"] - row["open"])
        candle_range = row["high"] - row["low"]
        candle_is_not_week = (candle_range > 0 and body / candle_range > 0.5) # do not remove candle_rage > 0 will raise devided by zero exception

        if (cond_1 and candle_is_not_week) or cond_2 or cond_3: # for cond_1 we need body_confirmation, for cond_2 and cond_3 we do not need it

            logger.info(f"[in breakout_in_last_x_candles], idx: {idx}, level: {level}, retest happened!! ")

            application_state['breakouts'].setdefault(symbol, []).append({
                'side': side,
                'level': level,
                'level_alias': level_alias,
                'idx': idx,
                'time': str(row['date']),
            })
            breakout_happened = True
            offseted_price = chart_helper.get_stacked_mark_price(app_config, application_state, symbol, side='up', price=df['high'].iloc[idx], date=df['date'].iloc[idx], caller_key="breakout_in_last_x_candles_ver_2")
            case_color = get_case_color(app_config, case)
            TradingLedger.add_to_list("signals", (symbol, f'BREAKOUT_{level}', offseted_price, df['date'].iloc[idx], f"BREAKOUT {level_alias} ... {df['date'].iloc[idx].strftime('%H:%M')}... ", case_color) )
    return breakout_happened

def breakout_in_last_x_candles_ver_4(app_config, application_state, case, symbol, df, side='up', idx_list=[-2], level=0, level_alias=''):
    ###
    # The difference with ver_2 is that if -i is breakout candle, we do not need to check the body confirmation for -i-1 candle.
    # because -i-1 candle is before breakout and it can be weak as we do not care about it. but in ver_2 we check body
    # confirmation for -i candle which is breakout candle and it can be weak as well.
    # so in ver_3 we check body confirmation for -i candle only if cond_1 is true. if cond_2 or cond_3 is true, we do not check body confirmation for -i candle.

    logger.debug(f"[in breakout_in_last_x_candles], symbol: {symbol}, idx_list: {idx_list}, level:{level}")

    if level == 0:
        return False
    gap = app_config['symbols_meta'].get(symbol,{}).get('breakout_confirmation_distance', 0.15)
    breakout_happened = False
    breakout_idxs = []

    for idx in idx_list:
        row = df.iloc[idx]
        previous = df.iloc[idx-1]
        next = df.iloc[idx+1]   # as we always we check -2, so this is safe. and -1 with be the forming candle
        # --- Breakout detection ---
        if date_utils.get_hhmm_int(row['date']) < 930: # we dont want breaks before 930
            continue

        # --- breakout condition ---
        if side == 'up':
            cond_1 = (row["open"] <= level and row["close"] > level + gap)  and (previous["open"] <= level)  # The price above level + gap
            cond_2 = (previous["open"] < level and row["close"] > level + gap and df.iloc[idx-2]["low"] < level and df.iloc[idx-3]["low"] < level)  # The prev open is below level and current above the level.
            cond_3 = (previous["open"] < level and row["open"] > level and row["close"] > level and df.iloc[idx-2]["low"] < level and df.iloc[idx-3]["low"] < level)  # The prev open is below level and current open and close are above the level.
            cond_4 = (row["low"] <= level and row["close"] > level and next["open"] > level and next["close"] > level and next["close"]  > next["open"]
                      and previous["low"] < level and df.iloc[idx-2]["low"] < level and df.iloc[idx-3]["low"] < level)  # The current open is below level and next open and close both above the level

        else:
            cond_1 = (row["open"] >= level and row["close"] < level - gap) and (previous["open"] >= level)
            cond_2 = (previous["open"] > level and row["close"] < level - gap and df.iloc[idx-2]["high"] > level and df.iloc[idx-3]["high"] > level)
            cond_3 = (previous["open"] > level and row["open"] < level and row["close"] < level and df.iloc[idx-2]["high"] > level and df.iloc[idx-3]["high"] > level)
            cond_4 = (row["high"]  >= level and row["close"] < level and next["open"] < level and next["close"] < level and next["close"] < next["open"]
                      and previous["high"] > level and df.iloc[idx-2]["high"] > level and df.iloc[idx-3]["high"] > level)

        breakout = (cond_1 or cond_2 or cond_3 or cond_4)
        if not breakout:
            continue


        # --- candle body confirmation ---
        body = abs(row["close"] - row["open"])
        candle_range = row["high"] - row["low"]
        candle_is_not_week = (candle_range > 0 and body / candle_range > 0.5) # do not remove candle_rage > 0 will raise devided by zero exception

        if (cond_1 and candle_is_not_week) or cond_2 or cond_3 or cond_4: # for cond_1 we need body_confirmation, for others we do not need it

            logger.info(f"[breakout_in_last_x_candles_ver_4], idx: {idx}, level: {level}")

            application_state['breakouts'].setdefault(symbol, []).append({
                'case': case,
                'side': side,
                'level': level,
                'level_alias': level_alias,
                'idx': idx,
                'time': str(row['date']),
            })
            breakout_idxs.append(idx)
            breakout_happened = True
            offseted_price = chart_helper.get_stacked_mark_price(app_config, application_state, symbol, side='up', price=df['high'].iloc[idx], date=df['date'].iloc[idx], caller_key=f"breakout_in_last_x_candles_ver_4-{case}")
            case_color = get_case_color(app_config, case)
            triggered = [f"cond_{i}" for i, c in enumerate([cond_1 and candle_is_not_week, cond_2, cond_3, cond_4], 1) if c]
            TradingLedger.add_to_list("signals", (symbol, f'BREAKOUT_{level}', offseted_price, df['date'].iloc[idx], f"BREAKOUT-v4 {level_alias} ... {df['date'].iloc[idx].strftime('%H:%M')}... [{','.join(triggered)}]", "blue"))
    for breakout_idx in breakout_idxs:
        if not breakout_idx - 1 in breakout_idxs:
            # if the previous candle is not breakout candle, we check to markk it as breakout as well
                row = df.iloc[breakout_idx-1]
                if side == 'up':
                    body = abs(row["close"] - row["open"])
                    candle_range = row["high"] - row["low"]
                    candle_is_not_week = (candle_range > 0 and body / candle_range > 0.5)
                    if candle_is_not_week and row["open"] <= level and row["close"] > level:
                        application_state['breakouts'].setdefault(symbol, []).append({
                            'side': side,
                            'level': level,
                            'level_alias': level_alias,
                            'idx': breakout_idx-1,
                            'time': str(row['date']),
                        })
                        offseted_price = chart_helper.get_stacked_mark_price(app_config, application_state, symbol, side='up', price=df['high'].iloc[breakout_idx - 1], date=df['date'].iloc[breakout_idx - 1], caller_key=f"breakout_in_last_x_candles_ver_4-{case}")
                        case_color = get_case_color(app_config, case)
                        TradingLedger.add_to_list("signals", (symbol, f'BREAKOUT_{level}', offseted_price, row['date'], f"BREAKOUT-v4 {level_alias} ... {row['date'].strftime('%H:%M')}...[x]", case_color) )
                else:
                    body = abs(row["close"] - row["open"])
                    candle_range = row["high"] - row["low"]
                    candle_is_not_week = (candle_range > 0 and body / candle_range > 0.5)
                    if candle_is_not_week and row["open"] >= level and row["close"] < level:
                        application_state['breakouts'].setdefault(symbol, []).append({
                            'side': side,
                            'level': level,
                            'level_alias': level_alias,
                            'idx': breakout_idx-1,
                            'time': str(row['date']),
                        })
                        offseted_price = chart_helper.get_stacked_mark_price(app_config, application_state, symbol, side='up', price=df['high'].iloc[breakout_idx - 1], date=df['date'].iloc[breakout_idx - 1], caller_key=f"breakout_in_last_x_candles_ver_4-{case}")
                        case_color = get_case_color(app_config, case)
                        TradingLedger.add_to_list("signals", (symbol, f'BREAKOUT_{level}', offseted_price, row['date'], f"BREAKOUT-v4 {level_alias} ... {row['date'].strftime('%H:%M')}...[x]", case_color) )



    return breakout_happened


def breakout_in_last_x_candles_ver_3(app_config, application_state, case, symbol, df, side='up', idx_list=[-2], level=0, level_alias=''):
    ###
    # The difference with ver_2 is that if -i is breakout candle, we do not need to check the body confirmation for -i-1 candle.
    # because -i-1 candle is before breakout and it can be weak as we do not care about it. but in ver_2 we check body
    # confirmation for -i candle which is breakout candle and it can be weak as well.
    # so in ver_3 we check body confirmation for -i candle only if cond_1 is true. if cond_2 or cond_3 is true, we do not check body confirmation for -i candle.

    logger.debug(f"[breakout_in_last_x_candles], symbol: {symbol}, idx_list: {idx_list}, level:{level}")

    if level == 0:
        return False
    gap = app_config['symbols_meta'].get(symbol,{}).get('breakout_confirmation_distance', 0.15)
    breakout_happened = False
    breakout_idxs = []

    for idx in idx_list:
        row = df.iloc[idx]
        previous = df.iloc[idx-1]
        # --- Breakout detection ---
        if date_utils.get_hhmm_int(row['date']) < 930: # we dont want breaks before 930
            continue

        # --- breakout condition ---
        if side == 'up':
            cond_1 = (row["low"] <= level and row["close"] > level + gap)    # The price above level + gap
            cond_2 = (previous["open"] < level and row["close"] > level + gap)  # The prev open is below level and current above the level.
            cond_3 = (previous["open"] < level and row["open"] > level and row["close"] > level)  # The prev open is below level and current open and close are above the level.

        else:
            cond_1 = (row["high"] >= level and row["close"] < level - gap)
            cond_2 = (previous["open"] > level and row["close"] < level - gap)
            cond_3 = (previous["open"] > level and row["open"] < level and row["close"] < level)

        breakout = (cond_1 or cond_2 or cond_3)
        if not breakout:
            continue


        # --- candle body confirmation ---
        body = abs(row["close"] - row["open"])
        candle_range = row["high"] - row["low"]
        candle_is_not_week = (candle_range > 0 and body / candle_range > 0.5) # do not remove candle_rage > 0 will raise devided by zero exception

        if (cond_1 and candle_is_not_week) or cond_2 or cond_3: # for cond_1 we need body_confirmation, for cond_2 and cond_3 we do not need it

            logger.info(f"[breakout_in_last_x_candles], idx: {idx}, level: {level}, retest happened!! ")

            application_state['breakouts'].setdefault(symbol, []).append({
                'side': side,
                'level': level,
                'level_alias': level_alias,
                'idx': idx,
                'time': str(row['date']),
            })
            breakout_idxs.append(idx)
            breakout_happened = True
            offseted_price = chart_helper.get_stacked_mark_price(app_config, application_state, symbol, side='up', price=df['high'].iloc[idx], date=df['date'].iloc[idx], caller_key="breakout_in_last_x_candles_ver_3")
            case_color = get_case_color(app_config, case)
            TradingLedger.add_to_list("signals", (symbol, f'BREAKOUT_{level}', offseted_price, df['date'].iloc[idx], f"BREAKOUT {level_alias} ... {df['date'].iloc[idx].strftime('%H:%M')}... ", case_color) )
    for breakout_idx in breakout_idxs:
        if not breakout_idx - 1 in breakout_idxs:
            # if the previous candle is not breakout candle, we check to markk it as breakout as well
                row = df.iloc[breakout_idx-1]
                if side == 'up':
                    body = abs(row["close"] - row["open"])
                    candle_range = row["high"] - row["low"]
                    candle_is_not_week = (candle_range > 0 and body / candle_range > 0.5)
                    if candle_is_not_week and row["open"] <= level and row["close"] > level:
                        application_state['breakouts'].setdefault(symbol, []).append({
                            'side': side,
                            'level': level,
                            'level_alias': level_alias,
                            'idx': breakout_idx-1,
                            'time': str(row['date']),
                        })
                        offseted_price = chart_helper.get_stacked_mark_price(app_config, application_state, symbol, side='up', price=df['high'].iloc[breakout_idx - 1], date=df['date'].iloc[breakout_idx - 1], caller_key="breakout_in_last_x_candles_ver_3")
                        case_color = get_case_color(app_config, case)
                        TradingLedger.add_to_list("signals", (symbol, f'BREAKOUT_{level}', offseted_price, row['date'], f"BREAKOUT {level_alias} ... {row['date'].strftime('%H:%M')}... ", case_color) )
                else:
                    body = abs(row["close"] - row["open"])
                    candle_range = row["high"] - row["low"]
                    candle_is_not_week = (candle_range > 0 and body / candle_range > 0.5)
                    if candle_is_not_week and row["open"] >= level and row["close"] < level:
                        application_state['breakouts'].setdefault(symbol, []).append({
                            'side': side,
                            'level': level,
                            'level_alias': level_alias,
                            'idx': breakout_idx-1,
                            'time': str(row['date']),
                        })
                        offseted_price = chart_helper.get_stacked_mark_price(app_config, application_state, symbol, side='up', price=df['high'].iloc[breakout_idx - 1], date=df['date'].iloc[breakout_idx - 1], caller_key="breakout_in_last_x_candles_ver_3")
                        case_color = get_case_color(app_config, case)
                        TradingLedger.add_to_list("signals", (symbol, f'BREAKOUT_{level}', offseted_price, row['date'], f"BREAKOUT {level_alias} ... {row['date'].strftime('%H:%M')}... ", case_color) )



    return breakout_happened

def get_break_out_indices_by_level_set(application_state, symbol, level):
    break_out_indices = set()
    breakouts = application_state['breakouts'].get(symbol, [])
    for b in breakouts:
        if b['level'] == level:
            break_out_indices.add(b['idx'])
    return break_out_indices
    
def get_retest_indices_by_level_set(application_state, symbol, level):
    retest_indices = set()
    retests = application_state['retests'].get(symbol, [])
    for r in retests:
        if r['level'] == level:
            retest_indices.add(r['idx'])
    return retest_indices



def price_retest(app_config, application_state, case, symbol, df, side='up', idx_list=[-2], level=0, both_sides=False, level_alias=''):

    if level == 0:
        return False

    # tolerance_amount = app_config['symbols_meta'].get(symbol,{})['retest_tolerance_amount']
    # tolerance_amount = dynamic_tolerance.get('tolerance', 0)
    tolerance_amount = application_state.get('dynamic_tolerances', {}).get(symbol, {}).get('tolerance', 0)

    tolerance_amount = tolerance_amount * app_config['symbols_meta'].get(symbol,{}).get('retest_tolerance_multiplier', 1)
    logger.debug(f"[price_retest] {symbol}, tolerance_amount: {tolerance_amount}")
    retest = False
    case_color = get_case_color(app_config,case)

    for idx in idx_list:
        row = df.iloc[idx]

        if date_utils.get_hhmm_int(row['date']) < 930: # we dont want breaks before 930
            continue


        # --- Retest detection ---
        if side == 'up':
            if level > row["low"] and level - row["low"] <= tolerance_amount and row["close"] > level:
                d = {
                    'side': side,
                    'level': level,
                    'level_alias': level_alias,
                    'idx': idx,
                    'time': str(row['date']),
                }
                application_state['retests'].setdefault(symbol, []).append(d)
                offseted_price = chart_helper.get_stacked_mark_price(app_config, application_state, symbol, side='up', price=df['high'].iloc[idx], date=df['date'].iloc[idx], caller_key="price_retest")
                TradingLedger.add_to_list("signals", (symbol, f'RETEST_{level}', offseted_price, df['date'].iloc[idx], f"RETEST  {level_alias} ... {df['date'].iloc[idx].strftime('%H:%M')}", case_color) )

                logger.info(f"[price_retest] symbol: {symbol}, level: {level}, date:{df.iloc[idx]['date']} ")
                retest = True
                diff = abs(row['low']-level)

            elif both_sides and abs(level - row["low"]) <= tolerance_amount and row["close"] > level:   # close > level.  low is close to the level in both sides.
                d = {
                    'side': side,
                    'level': level,
                    'level_alias': level_alias,
                    'idx': idx,
                    'time': str(row['date']),
                }
                application_state['retests'].setdefault(symbol, []).append(d)
                offseted_price = chart_helper.get_stacked_mark_price(app_config, application_state, symbol, side='up', price=df['high'].iloc[idx], date=df['date'].iloc[idx], caller_key="price_retest")
                TradingLedger.add_to_list("signals", (symbol, f'RETEST_{level}', offseted_price, df['date'].iloc[idx], f"RETEST  {level_alias} ... {df['date'].iloc[idx].strftime('%H:%M')}", case_color) )

                retest = True
                diff = abs(row['low']-level)
        else:
            if row["high"] > level and row["high"] - level <= tolerance_amount and row["close"] < level:
                d = {
                    'side': side,
                    'level': level,
                    'level_alias': level_alias,
                    'idx': idx,
                    'time': str(row['date']),
                }
                application_state['retests'].setdefault(symbol, []).append(d)
                offseted_price = chart_helper.get_stacked_mark_price(app_config, application_state, symbol, side='up', price=df['high'].iloc[idx], date=df['date'].iloc[idx], caller_key="price_retest")
                TradingLedger.add_to_list("signals", (symbol, f'RETEST_{level}', offseted_price, df['date'].iloc[idx], f"RETEST  {level_alias} ... {df['date'].iloc[idx].strftime('%H:%M')}", case_color) )

                retest = True
                diff = abs(row['high'] - level)
            elif both_sides and abs(level - row["high"]) <= tolerance_amount and row["close"] < level:   # close < level.  high is close to the level in both sides.
                d = {
                    'side': side,
                    'level': level,
                    'level_alias': level_alias,
                    'idx': idx,
                    'time': str(row['date']),
                }
                application_state['retests'].setdefault(symbol, []).append(d)
                offseted_price = chart_helper.get_stacked_mark_price(app_config, application_state, symbol, side='up', price=df['high'].iloc[idx], date=df['date'].iloc[idx], caller_key="price_retest")
                TradingLedger.add_to_list("signals", (symbol, f'RETEST_{level}', offseted_price, df['date'].iloc[idx], f"RETEST  {level_alias} ... {df['date'].iloc[idx].strftime('%H:%M')}" , case_color) )

                retest = True
                diff = abs(row['high']-level)

    return retest

def set_and_is_retest_after_breakout(application_state, symbol, case, side='up', level=1, level_alias=''):

    breakout_idxs = get_break_out_indices_by_level_set(application_state, symbol, level)
    retest_idxs = get_retest_indices_by_level_set(application_state, symbol, level)

    if retest_idxs == set() or breakout_idxs == set():
        return False
    if max(retest_idxs) > min(breakout_idxs):
        retest_idx = max(retest_idxs)
        application_state.setdefault('retest_idx', {}).setdefault(symbol, []).append({'retest_idx': retest_idx, 'level': level, 'case': case,'level_alias': level_alias})
        valid_breakouts = [b for b in breakout_idxs if b < retest_idx]   # all the breakout idxs that are before retest_idx
        if valid_breakouts:
            breakout_idx = max(valid_breakouts)  # closest (largest) breakout before retest
            application_state.setdefault('breakout_idx', {}).setdefault(symbol, []).append({'breakout_idx' : breakout_idx, 'level': level, 'case': case, 'level_alias': level_alias })
        return True
    else:
        return False

    return False


def all_levels_in(application_state, symbol):
    levels = application_state['levels'].get(symbol, {})
    required_levels = ['PDH', 'PDL', 'PMH', 'PML', '5MH', '5ML']
    for rl in required_levels:
        if rl not in levels:
            return False
    return True





# def get_breakout_idx(application_state, case, symbol, level):
#     return application_state.get('breakouts_idx', {}).get('symbol', {}).get(symbol, None)
#
# def get_retest_idx(application_state, case, symbol):
#     return application_state.get('retests_idx', {}).get('case', {}).get(symbol, None)

def get_breakout_idx(application_state, case, symbol, level):
   #  application_state.setdefault('breakout_idx', {}).get(symbol,{}) = {'breakout_idx' : breakout_idx, 'level': level, 'level_alias': level_alias }
    for entry in application_state.get('breakout_idx', {}).get(symbol, []): # cases need to be involved
        if entry['level'] == level and entry['case'] == case:
            return entry['breakout_idx']
    return None

def get_retest_idx(application_state, case, symbol, level):
    for entry in application_state.get('retest_idx', {}).get(symbol, []):
        if entry['level'] == level and entry['case'] == case:
            return entry['retest_idx']
    return None


def check_entry_vs_retest(application_state, case, symbol, df, side='up', level=1, retest_ohlc=''):

    retest_idx = get_retest_idx(application_state, case, symbol, level)
    breakout_idx = get_breakout_idx(application_state, case, symbol, level)

    if retest_idx is None or breakout_idx is None:
        return False

    if retest_idx == 0 or breakout_idx == 0:
        return False

    if side == 'up':
        ohlc_field = 'high' if retest_ohlc == '' else retest_ohlc
        if df['high'].iloc[-1] > df[ohlc_field].iloc[retest_idx]:  # clode > retest high
            return True
        else:
            return False
    else:
        ohlc_field = 'low' if retest_ohlc == '' else retest_ohlc
        if df['low'].iloc[-1] < df[ohlc_field].iloc[retest_idx]:
            return True
        else:
            return False
    return False



def no_failure_after_breakout(application_state, case, symbol, df, side='up', level=0, ohlc_field='open'):
    # we want make sure all closes after breakout are above the level.
    # for up, use 'open'
    # for down use 'close'

    breakout_idx = get_breakout_idx(application_state, case, symbol, level)

    if breakout_idx is None or breakout_idx == 0:
        return False

    i = -1 # the last candle
    j = breakout_idx # This is index for breakout...

    if j == i:
        return  False

    start, end = sorted([i, j])  # in case you mix order
    # say start -5 end -3.  this get -5, -4, -3,  it meanns both -5 and -3 is included too.
    if side == 'up':
        if (df.iloc[start:][ohlc_field] >= level).all(): # all highs are above level
            return True
    else:
        if (df.iloc[start:][ohlc_field] <= level).all(): # all opens are less then elvel
            return True

    return False


def check_price_vs_level(app_config, symbol, side='up', price=0, level=0):

    min_required_move_from_level = app_config['symbols_meta'].get(symbol,{}).get('min_required_move_from_level',0.25)

    if side == 'up':
        return price + min_required_move_from_level > level
    else:
        return price < level - min_required_move_from_level


def is_price_close_to_next_levels_ver_2(app_config, application_state, case, symbol, df, side='up', price= 0, current_level=1, next_levels=['PDH']):  # used in the config

    breakout_idx = get_breakout_idx(application_state, case, symbol, current_level)

    if breakout_idx == 0 or breakout_idx is None:
        return False
    if next_levels is None:
        return False

    levels_map = application_state['levels'].get(symbol, {})
    closeness_distance = eval(app_config['closeness_distance'])

    clipped_df = df[breakout_idx:]
    highest_high = clipped_df['high'].max()
    lowest_low = clipped_df['low'].min()
    logger.info(f"[is_price_close_to_next_levels_ver_2] {symbol}, price: side: {side}, {price}, current_level: {current_level}, next_levels:{next_levels}, breakout_idx: {breakout_idx} ,date: {df['date'].iloc[-1]}")

    for key in next_levels:
        next_level = levels_map.get(key, None)
        if next_level is None:
            continue  # skip missing levels

        distance = abs(price - next_level)
        is_close = distance < closeness_distance

        if side == "up":
            # TODO THe first two can merged ..
            # next_level is above the current level, price is below next level but very close
            if next_level > current_level and price > current_level and price < next_level and is_close:
                # add_to_signlas(symbol, 'PRICE_CLODE_TO_LEVEL', price, df['date'].iloc[-1], f'up-1 {price}, to: {next_level} <br> {date_utils.get_hhm_mm_of_last_record(df)}', color='red')
                TradingLedger.add_to_list("signals", (symbol, 'PRICE_CLODE_TO_LEVEL', price, df['date'].iloc[-1], f'PRICE_CLODE_TO_LEVEL-1 {price}, to: {next_level} <br> {date_utils.get_hhm_mm_of_last_record(df)}' , 'red') )
                return True

            # next_level is above the current level, price is above next level
            if next_level > current_level and price > current_level and price > next_level: # This is for once the price passes the next level as well.
                # add_to_signlas(symbol, 'PRICE_CLODE_TO_LEVEL', price, df['date'].iloc[-1], f'up-2. price: {price}, to: {next_level} <br> {date_utils.get_hhm_mm_of_last_record(df)}', color='red')
                TradingLedger.add_to_list("signals", (symbol, 'PRICE_CLODE_TO_LEVEL', price, df['date'].iloc[-1], f'PRICE_CLODE_TO_LEVEL-2. price: {price}, to: {next_level} <br> {date_utils.get_hhm_mm_of_last_record(df)}', 'red') )
                return True

            # next_level is above the current level AND price is above leve AND highest_high after breakout canddle is close to the next level ..
            if next_level > current_level and price > current_level and abs(highest_high - next_level) < closeness_distance:
                # add_to_signlas(symbol, 'PRICE_CLODE_TO_LEVEL', price, df['date'].iloc[-1], f'up-3. price: {price}, to: {next_level} <br> {date_utils.get_hhm_mm_of_last_record(df)}', color='red')
                TradingLedger.add_to_list("signals", (symbol, 'PRICE_CLODE_TO_LEVEL', price, df['date'].iloc[-1], f'PRICE_CLODE_TO_LEVEL-3. price: {price}, to: {next_level} <br> {date_utils.get_hhm_mm_of_last_record(df)}', 'red') )
                return True

            # next_level > current level AND price > c level AND highest high >  next level
            if next_level > current_level and price > current_level and highest_high > next_level:
                # add_to_signlas(symbol, 'PRICE_CLODE_TO_LEVEL', price, df['date'].iloc[-1], f'up-4. price: {price}, to: {next_level} <br> {date_utils.get_hhm_mm_of_last_record(df)}', color='red')
                TradingLedger.add_to_list("signals", (symbol, 'PRICE_CLODE_TO_LEVEL', price, df['date'].iloc[-1], f'PRICE_CLODE_TO_LEVEL-4. price: {price}, to: {next_level} <br> {date_utils.get_hhm_mm_of_last_record(df)}', 'red') )
                return True

        else:
            # next l < c level AND price < c level AND price > next l ...
            if next_level < current_level and price < current_level and price > next_level and is_close:
                # add_to_signlas(symbol, 'PRICE_CLODE_TO_LEVEL', price, df['date'].iloc[-1], f'down-1. price: {price}, to: {next_level} <br> {date_utils.get_hhm_mm_of_last_record(df)}', color='red')
                TradingLedger.add_to_list("signals", (symbol, 'PRICE_CLODE_TO_LEVEL', price, df['date'].iloc[-1], f'PRICE_CLODE_TO_LEVEL-1. price: {price}, to: {next_level} <br> {date_utils.get_hhm_mm_of_last_record(df)}', 'red') )
                return True


            if next_level < current_level and price < current_level and price < next_level:  # see PLTR Oct 09-
                # add_to_signlas(symbol, 'PRICE_CLODE_TO_LEVEL', price, df['date'].iloc[-1], f'down-2. price: {price}, to: {next_level} <br> {date_utils.get_hhm_mm_of_last_record(df)}', color='red')
                TradingLedger.add_to_list("signals", (symbol, 'PRICE_CLODE_TO_LEVEL', price, df['date'].iloc[-1], f'PRICE_CLODE_TO_LEVEL-2. price: {price}, to: {next_level} <br> {date_utils.get_hhm_mm_of_last_record(df)}', 'red') )
                return True

            # next_level < current level AND price is below level AND lowest low after breakout canddle is close to the next level ..
            if next_level < current_level and price < current_level and abs(lowest_low - next_level) < closeness_distance:
                # add_to_signlas(symbol, 'PRICE_CLODE_TO_LEVEL', price, df['date'].iloc[-1], f'down-3. price: {price}, to: {next_level} <br> {date_utils.get_hhm_mm_of_last_record(df)}', color='red')
                TradingLedger.add_to_list("signals", (symbol, 'PRICE_CLODE_TO_LEVEL', price, df['date'].iloc[-1], f'PRICE_CLODE_TO_LEVEL-3. price: {price}, to: {next_level} <br> {date_utils.get_hhm_mm_of_last_record(df)}', 'red') )
                return True

            # next_level < current level AND price < c level AND lowest low  <  next level
            if next_level < current_level and price < current_level and lowest_low < next_level:
                # add_to_signlas(symbol, 'PRICE_CLODE_TO_LEVEL', price, df['date'].iloc[-1], f'down-4. price: {price}, to: {next_level} <br> {date_utils.get_hhm_mm_of_last_record(df)}', color='red')
                TradingLedger.add_to_list("signals", (symbol, 'PRICE_CLODE_TO_LEVEL', price, df['date'].iloc[-1], f'PRICE_CLODE_TO_LEVEL-4. price: {price}, to: {next_level} <br> {date_utils.get_hhm_mm_of_last_record(df)}', 'red') )
                return True


    return False


def get_last_record_hh_mm(df):
    return date_utils.get_last_record_hhmm(df)


def get_mode(application_state):
    return application_state.get('mode', 'live')

def get_current_price(ib, application_state, symbol):
    return application_state.get('latest_prices', {}).get(symbol, None)


def get_case_color(app_config, case):
    return app_config.get('cases', {}).get(case, {}).get('color', 'black')


# ─────────────────────────────────────────────────────────────────────────────
# CASE 5 — Displacement + Retracement helpers
# New functions only — no existing functions above are modified.
# ─────────────────────────────────────────────────────────────────────────────

def breakout_with_displacement(
    app_config, application_state, case, symbol, df,
    side='up',
    idx_list=None,
    level=0,
    level_alias='',
    min_displacement_atr_ratio=0.5,
    min_body_ratio=0.6,
    close_in_top_pct=0.70,
):
    """
    Fresh displacement breakout detector — independent of ver_4 patterns.

    A displacement breakout is a single candle that satisfies ALL 5 gates:

      Gate 1 — Body cross:
          up  : open < level  AND  close > level   (body genuinely crosses above)
          down: open > level  AND  close < level   (body genuinely crosses below)
          Wick-only crosses (open and close both on the same side) are rejected.

      Gate 2 — Close margin:
          The close must clear the level by at least `gap`
          (breakout_confirmation_distance from config).
          Prevents razor-thin, scratch closes right at the level.

      Gate 3 — Impulsive size:
          candle_range >= atr_14 * min_displacement_atr_ratio
          A displacement candle must be meaningfully large relative to recent volatility.

      Gate 4 — Conviction body:
          body / candle_range >= min_body_ratio
          Eliminates doji-like candles or candles with large wicks relative to body.

      Gate 5 — Strong close:
          up  : (close - low)  / candle_range >= close_in_top_pct
          down: (high - close) / candle_range >= close_in_top_pct
          Price must close near its extreme — it didn't reverse back into the candle.

    On success stores into application_state:
        ['breakouts'][symbol]            same schema as other breakout functions
        ['displacement_swings'][symbol]  { side, level, swing, idx, time }
            swing = high of the candle (up) / low (down) — used for SL anchoring.
    """
    if idx_list is None:
        idx_list = [-3, -4, -5, -6, -7, -8, -9, -10, -11]

    logger.info(f"[breakout_with_displacement] {symbol}, side:{side}, level:{level}, idx_list:{idx_list}")

    if level == 0:
        return False

    gap = app_config['symbols_meta'].get(symbol,{}).get('breakout_confirmation_distance', 0.15)
    gap = 0.02 # TODO relaxing for debug only ...
    atr = df['atr_14'].iloc[-2]

    if atr <= 0:
        logger.warning(f"[breakout_with_displacement] {symbol} — atr_14 is zero/negative, skipping")
        return False

    breakout_happened = False

    for idx in idx_list:
        row = df.iloc[idx]

        # skip pre-market candles
        if date_utils.get_hhmm_int(row['date']) < 930:
            continue

        o = row['open']
        h = row['high']
        l = row['low']
        c = row['close']
        d = row['date']

        candle_range = h - l
        if candle_range <= 0:
            continue

        body = abs(c - o)

        # ── Gate 1: body cross ──────────────────────────────────────────────
        # The candle body (open- >close) must straddle the level.
        # Wick touches (open and close both on the same side) are not displacement.
        logger.info(f"[breakout_with_displacement] {symbol} idx:{idx} — {level}, o: {o}, c: {c}, {str(row['date'])}")

        if side == 'up':
            gate_1 = (o < level) and (c > level)
        else:
            gate_1 = (o > level) and (c < level)

        if not gate_1:
            logger.info(f"[breakout_with_displacement] {symbol} idx:{idx} — Gate 1 fail (no body cross), level: {level}, o: {o}, c: {c}")
            continue

        # ── Gate 2: close margin ────────────────────────────────────────────
        # Close must clear the level by at least the confirmation distance.
        if side == 'up':
            gate_2 = c > level + gap
        else:
            gate_2 = c < level - gap

        if not gate_2:
            logger.info(f"[breakout_with_displacement] {symbol} idx:{idx} — Gate 2 fail "
                         f"(close {c:.4f} not past level {level:.4f} + gap {gap:.4f})")
            continue

        # ── Gate 3: impulsive size ──────────────────────────────────────────
        min_range = atr * min_displacement_atr_ratio
        if candle_range < min_range:
            logger.info(f"[breakout_with_displacement] {symbol} idx:{idx} — Gate 3 fail "
                         f"(range {candle_range:.4f} < {min_range:.4f})")
            continue

        # ── Gate 4: conviction body ─────────────────────────────────────────
        body_ratio = body / candle_range
        if body_ratio < min_body_ratio:
            logger.info(f"[breakout_with_displacement] {symbol} idx:{idx} — Gate 4 fail "
                         f"(body_ratio {body_ratio:.2f} < {min_body_ratio})")
            continue

        # ── Gate 5: strong close ────────────────────────────────────────────
        if side == 'up':
            close_position = (c - l) / candle_range
        else:
            close_position = (h - c) / candle_range

        if close_position < close_in_top_pct:
            logger.info(f"[breakout_with_displacement] {symbol} idx:{idx} — Gate 5 fail "
                         f"(close_position {close_position:.2f} < {close_in_top_pct})")
            continue

        # ── All 5 gates passed — record displacement ────────────────────────
        swing = h if side == 'up' else l

        logger.info(
            f"[breakout_with_displacement] {symbol} idx:{idx}  DISPLACEMENT "
            f"side:{side} level:{level} "
            f"range:{candle_range:.4f} body_ratio:{body_ratio:.2f} "
            f"close_pos:{close_position:.2f} time:{row['date']}"
        )

        application_state['breakouts'].setdefault(symbol, []).append({
            'case': case,
            'side':        side,
            'level':       level,
            'level_alias': level_alias,
            'idx':         idx,
            'time':        str(row['date']),
        })

        application_state.setdefault('displacement_swings', {}).setdefault(symbol, []).append({
            'case': case,
            'side':  side,
            'level': level,
            'swing': swing,
            'idx':   idx,
            'time':  str(row['date']),
        })
        offseted_price = chart_helper.get_stacked_mark_price(app_config, application_state, symbol, side='up', price=h, date=d, caller_key="breakout_with_displacement")
        case_color = get_case_color(app_config, case)
        TradingLedger.add_to_list(
            "signals",
            (symbol, 'BREAKOUT', offseted_price, row['date'],
             f"DISPLACEMENT_BREAKOUT {level_alias} "
             f"range:{candle_range:.2f} body:{body_ratio:.0%} close_pos:{close_position:.0%} ... {row['date'].strftime('%H:%M')}",
             case_color)
        )

        breakout_happened = True

    return breakout_happened


def get_displacement_swing(application_state, symbol, level):
    """
    Returns the swing price of the most recent displacement candle for this level.
    swing = high of the candle for 'up' breakout, low for 'down' breakout.
    Returns None when no displacement has been recorded yet.
    """
    logger.info(f"[price_retracement_to_level], {symbol}, {level}, price_retracement_to_level: {application_state.get('displacement_swings', {})} ")
    entries  = application_state.get('displacement_swings', {}).get(symbol, [])
    matching = [e for e in entries if e['level'] == level]
    if not matching:
        return None
    return max(matching, key=lambda e: e['idx'])['swing']


def price_retracement_to_level(
    app_config, application_state, case, symbol, df,
    side='up',
    idx_list=None,
    level=0,
    level_alias='',
    max_retrace_ratio=0.65,
):
    """
    Structured replacement for price_retest used in case_5.

    Requires all three of:
      1. A displacement swing is already recorded for this level.
      2. Retracement candle's extreme (low for 'up', high (down) pulled back
         at most max_retrace_ratio of the displacement range from the swing back
         toward the level.
             For 'up'  : low  must be >= level - tolerance
                         AND  low  <= level + (1 - max_retrace_ratio) * disp_range
             For 'down': high must be <= level + tolerance
                         AND  high >= level - (1 - max_retrace_ratio) * disp_range
      3. The candle CLOSES back on the correct side of the level
         (close > level for up, close < level for down).
         Rules out wick-only touches that fail to reclaim the level.

    On success stores:
        application_state['retests'][symbol]
            same schema as price_retest – set_and_is_retest_after_breakout still works.
        application_state['retracement_extremes'][symbol]
            full detail including extreme price for structured SL computation.
    """
    if idx_list is None:
        idx_list = [-2, -3, -4, -5, -6]

    if level == 0:
        return False

    displacement_swing = get_displacement_swing(application_state, symbol, level)
    if displacement_swing is None:
        logger.info(f"[price_retracement_to_level] {symbol} no displacement swing found for level {level}")
        return False

    displacement_range = abs(displacement_swing - level)
    if displacement_range <= 0:
        logger.info(f"[price_retracement_to_level] {symbol} displacement_range is zero, skip")
        return False

    tolerance_amount = (
        application_state.get('dynamic_tolerances', {}).get(symbol, {}).get('tolerance', 0)
        * app_config['symbols_meta'].get(symbol,{}).get('retest_tolerance_multiplier', 1)
    )
    case_color = get_case_color(app_config, case)
    retest = False

    for idx in idx_list:
        row = df.iloc[idx]

        if date_utils.get_hhmm_int(row['date']) < 930:
            continue

        if side == 'up':
            # zone ceiling: no deeper than (1 - max_retrace_ratio) * range from level
            retrace_zone_ceil = level + (1.0 - max_retrace_ratio) * displacement_range
            came_back_in_zone = (
                row["low"] >= level - tolerance_amount and
                row["low"] <= retrace_zone_ceil
            )
            close_confirms = row["close"] > level
        else:
            retrace_zone_floor = level - (1.0 - max_retrace_ratio) * displacement_range
            came_back_in_zone = (
                row["high"] <= level + tolerance_amount and
                row["high"] >= retrace_zone_floor
            )
            close_confirms = row["close"] < level

        if not (came_back_in_zone and close_confirms):
            logger.info(
                f"[price_retracement_to_level] {symbol} idx:{idx} "
                f"came_back_in_zone:{came_back_in_zone} close_confirms:{close_confirms} – skip")
            continue

        # ── retracement confirmed ─
        retrace_extreme = row["low"] if side == 'up' else row["high"]
        actual_retrace  = abs(retrace_extreme - level)
        retrace_pct     = actual_retrace / displacement_range if displacement_range > 0 else 0

        logger.info(
            f"[price_retracement_to_level] {symbol} idx:{idx} RETRACEMENT confirmed "
            f"level:{level} swing:{displacement_swing:.4f} disp_range:{displacement_range:.4f} "
            f"extreme:{retrace_extreme:.4f} retrace_pct:{retrace_pct:.0%}")

        # store in retests with same schema so existing helpers still work
        application_state['retests'].setdefault(symbol, []).append({
            'side':        side,
            'level':       level,
            'level_alias': level_alias,
            'idx':         idx,
            'time':        str(row['date']),
        })

        # store full detail for structured SL
        application_state.setdefault('retracement_extremes', {}).setdefault(symbol, []).append({
            'side':               side,
            'level':              level,
            'extreme':            retrace_extreme,
            'retrace_pct':        round(retrace_pct, 4),
            'displacement_range': displacement_range,
            'displacement_swing': displacement_swing,
            'idx':                idx,
            'time':               str(row['date']),
        })
        offseted_price = chart_helper.get_stacked_mark_price(app_config, application_state, symbol, side='up', price=df['high'].iloc[idx], date=df['date'].iloc[idx], caller_key="price_retracement_to_level")
        TradingLedger.add_to_list(
            "signals",
            (symbol, 'RETEST', offseted_price, df['date'].iloc[idx],
             f"RETRACEMENT {level_alias} retrace:{retrace_pct:.0%} "
             f"... {df['date'].iloc[idx].strftime('%H:%M')}", "green"))
        retest = True

    return retest


def price_retracement_immediate(
    app_config, application_state, case, symbol, df,
    side='up',
    idx_list=None,
    level=0,
    level_alias='',
    max_retrace_ratio=0.65,
):
    """
    Immediate retest detector — Type 1.

    Price breaks the level with displacement, then within the NEXT 1–3 candles
    dips straight back to the level without any meaningful extension first.

    Conditions (all must pass):
      1. A displacement swing is already recorded for this level.
      2. The retest candle index is within 3 bars of the breakout candle
         (immediate = no extended run away first).
      3. The candle's extreme (low for up / high for down) touches the retest zone:
             up  : low  >= level - tolerance  AND  low  <= level + (1 - max_retrace_ratio) * disp_range
             down: high <= level + tolerance  AND  high >= level - (1 - max_retrace_ratio) * disp_range
      4. Candle closes back on the correct side of the level (no wick-only touch).

    Stores results in the same state keys as price_retracement_to_level so all
    downstream helpers (set_and_is_retest_after_breakout, compute_structured_sl, etc.) work unchanged.
    """
    if idx_list is None:
        idx_list = [-2, -3, -4]   # tight window — immediate means 1–3 bars after breakout

    if level == 0:
        return False

    displacement_swing = get_displacement_swing(application_state, symbol, level)
    if displacement_swing is None:
        logger.debug(f"[price_retracement_immediate] {symbol} no displacement swing for level {level}")
        return False

    displacement_range = abs(displacement_swing - level)
    if displacement_range <= 0:
        return False

    breakout_idx = get_breakout_idx(application_state, case, symbol, level)
    tolerance_amount = (
        application_state.get('dynamic_tolerances', {}).get(symbol, {}).get('tolerance', 0)
        * app_config['symbols_meta'].get(symbol,{}).get('retest_tolerance_multiplier', 1)
    )
    case_color = get_case_color(app_config, case)
    retest = False

    for idx in idx_list:
        row = df.iloc[idx]

        if date_utils.get_hhmm_int(row['date']) < 930:
            continue

        # ── immediacy check: retest candle must be within 3 bars of the breakout ──
        if breakout_idx is not None:
            gap = abs(idx - breakout_idx)
            if gap > 3:
                logger.debug(f"[price_retracement_immediate] {symbol} idx:{idx} — gap {gap} > 3, not immediate")
                continue

        if side == 'up':
            retrace_zone_ceil = level + (1.0 - max_retrace_ratio) * displacement_range
            came_back_in_zone = (row['low'] >= level - tolerance_amount and row['low'] <= retrace_zone_ceil)
            close_confirms    = row['close'] > level
        else:
            retrace_zone_floor = level - (1.0 - max_retrace_ratio) * displacement_range
            came_back_in_zone = (row['high'] <= level + tolerance_amount and row['high'] >= retrace_zone_floor)
            close_confirms    = row['close'] < level

        if not (came_back_in_zone and close_confirms):
            logger.debug(f"[price_retracement_immediate] {symbol} idx:{idx} "
                         f"zone:{came_back_in_zone} close:{close_confirms} — skip")
            continue

        retrace_extreme = row['low'] if side == 'up' else row['high']
        actual_retrace  = abs(retrace_extreme - level)
        retrace_pct     = actual_retrace / displacement_range if displacement_range > 0 else 0

        logger.info(f"[price_retracement_immediate] {symbol} idx:{idx}  IMMEDIATE RETEST "
                    f"level:{level} extreme:{retrace_extreme:.4f} retrace_pct:{retrace_pct:.0%}")

        application_state['retests'].setdefault(symbol, []).append({
            'side': side, 'level': level, 'level_alias': level_alias,
            'idx': idx, 'time': str(row['date']),
        })
        application_state.setdefault('retracement_extremes', {}).setdefault(symbol, []).append({
            'side': side, 'level': level, 'extreme': retrace_extreme,
            'retrace_pct': round(retrace_pct, 4), 'displacement_range': displacement_range,
            'displacement_swing': displacement_swing, 'idx': idx, 'time': str(row['date']),
        })
        offseted_price = chart_helper.get_stacked_mark_price(app_config, application_state, symbol, side='up', price=df['high'].iloc[idx], date=df['date'].iloc[idx], caller_key="price_retracement_immediate")
        TradingLedger.add_to_list(
            "signals",
            (symbol, 'RETRACEMENT_IMMEDIATE', offseted_price, df['date'].iloc[idx],
             f"RETRACEMENT_IMMEDIATE {level_alias} retrace:{retrace_pct:.0%} "
             f"... {df['date'].iloc[idx].strftime('%H:%M')}", "green"))
        retest = True

    return retest


def price_retracement_half_circle(
    app_config, application_state, case, symbol, df,
    side='up',
    idx_list=None,
    level=0,
    level_alias='',
    max_retrace_ratio=0.65,
    min_extension_ratio=0.5,
):
    """
    Half-circle (curved arc) retest detector — Type 2.

    Price breaks the level with displacement, runs away for several candles
    (extension phase), then arcs back to retest the level more precisely.

    Extra condition vs immediate: the candles BETWEEN the breakout and the retest
    must show a meaningful extension away from the level before the arc back.
    This is quantified by min_extension_ratio — the highest high (up) or lowest
    low (down) between breakout and retest must be at least
    min_extension_ratio * displacement_range beyond the displacement swing.

    Conditions (all must pass):
      1. A displacement swing is already recorded for this level.
      2. Retest candle index is between 3–8 bars after the breakout (arc takes time).
      3. Extension check: peak between breakout and retest candle must be at least
             up  : max(highs) >= displacement_swing + min_extension_ratio * disp_range
             down: min(lows)  <= displacement_swing - min_extension_ratio * disp_range
      4. Retrace zone + close confirmation — same as immediate.

    Stores results in the same state keys so all downstream helpers work unchanged.
    """
    if idx_list is None:
        idx_list = [-2, -3, -4, -5, -6, -7, -8]

    if level == 0:
        return False

    displacement_swing = get_displacement_swing(application_state, symbol, level)
    if displacement_swing is None:
        logger.debug(f"[price_retracement_half_circle] {symbol} no displacement swing for level {level}")
        return False

    displacement_range = abs(displacement_swing - level)
    if displacement_range <= 0:
        return False

    breakout_idx = get_breakout_idx(application_state, case, symbol, level)
    tolerance_amount = (
        application_state.get('dynamic_tolerances', {}).get(symbol, {}).get('tolerance', 0)
        * app_config['symbols_meta'].get(symbol,{}).get('retest_tolerance_multiplier', 1)
    )
    case_color = get_case_color(app_config, case)
    retest = False

    for idx in idx_list:
        row = df.iloc[idx]

        if date_utils.get_hhmm_int(row['date']) < 930:
            continue

        # ── arc gap check: must be at least 3 bars after breakout ──
        if breakout_idx is not None:
            gap = abs(idx - breakout_idx)
            if gap < 3:
                logger.debug(f"[price_retracement_half_circle] {symbol} idx:{idx} — gap {gap} < 3, too immediate")
                continue
            if gap > 8:
                logger.debug(f"[price_retracement_half_circle] {symbol} idx:{idx} — gap {gap} > 8, too stale")
                continue

            # ── extension check: was there a meaningful run between breakout and retest? ──
            # slice the candles strictly between breakout and current retest candle
            start = breakout_idx    # negative idx arithmetic — smaller negative = more recent
            end   = idx             # e.g. breakout=-6, retest=-3 - > slice df[-6:-3]
            between_df = df.iloc[start:end] if start < end else df.iloc[end:start]

            if len(between_df) > 0:
                if side == 'up':
                    peak = between_df['high'].max()
                    extension_target = displacement_swing + min_extension_ratio * displacement_range
                    has_extension = peak >= extension_target
                else:
                    trough = between_df['low'].min()
                    extension_target = displacement_swing - min_extension_ratio * displacement_range
                    has_extension = trough <= extension_target

                if not has_extension:
                    logger.debug(f"[price_retracement_half_circle] {symbol} idx:{idx} — no extension "
                                 f"(target:{extension_target:.4f})")
                    continue

        if side == 'up':
            retrace_zone_ceil = level + (1.0 - max_retrace_ratio) * displacement_range
            came_back_in_zone = (row['low'] >= level - tolerance_amount and row['low'] <= retrace_zone_ceil)
            close_confirms    = row['close'] > level
        else:
            retrace_zone_floor = level - (1.0 - max_retrace_ratio) * displacement_range
            came_back_in_zone = (row['high'] <= level + tolerance_amount and row['high'] >= retrace_zone_floor)
            close_confirms    = row['close'] < level

        if not (came_back_in_zone and close_confirms):
            logger.debug(f"[price_retracement_half_circle] {symbol} idx:{idx} "
                         f"zone:{came_back_in_zone} close:{close_confirms} — skip")
            continue

        retrace_extreme = row['low'] if side == 'up' else row['high']
        actual_retrace  = abs(retrace_extreme - level)
        retrace_pct     = actual_retrace / displacement_range if displacement_range > 0 else 0

        logger.info(f"[price_retracement_half_circle] {symbol} idx:{idx}  HALF-CIRCLE RETEST "
                    f"level:{level} extreme:{retrace_extreme:.4f} retrace_pct:{retrace_pct:.0%}")

        application_state['retests'].setdefault(symbol, []).append({
            'side': side, 'level': level, 'level_alias': level_alias,
            'idx': idx, 'time': str(row['date']),
        })
        application_state.setdefault('retracement_extremes', {}).setdefault(symbol, []).append({
            'side': side, 'level': level, 'extreme': retrace_extreme,
            'retrace_pct': round(retrace_pct, 4), 'displacement_range': displacement_range,
            'displacement_swing': displacement_swing, 'idx': idx, 'time': str(row['date']),
        })
        offseted_price = chart_helper.get_stacked_mark_price(app_config, application_state, symbol, side='up', price=df['high'].iloc[idx], date=df['date'].iloc[idx], caller_key="price_retracement_half_circle")
        TradingLedger.add_to_list(
            "signals",
            (symbol, 'RETRACEMENT_HALF_CIRCLE', offseted_price, df['date'].iloc[idx],
             f"RETRACEMENT_HALF_CIRCLE {level_alias} retrace:{retrace_pct:.0%} "
             f"... {df['date'].iloc[idx].strftime('%H:%M')}", "green"))
        retest = True

    return retest


def get_retracement_extreme(application_state, symbol, level):
    """
    Returns the extreme price (low for long / high for short) of the most recent
    confirmed retracement candle for this level.
    Used by compute_structured_sl to anchor the stop-loss.
    Returns None if no retracement has been recorded.
    """
    entries  = application_state.get('retracement_extremes', {}).get(symbol, [])
    matching = [e for e in entries if e['level'] == level]
    if not matching:
        return None
    return max(matching, key=lambda e: e['idx'])['extreme']


def is_retracement_gap_acceptable(application_state, case, symbol, level, max_gap=6):
    """
    Extra safety check for case_5: ensures the gap between the displacement
    breakout candle and the retracement candle is at most max_gap bars.
    A very wide gap means price wandered too long before coming back — the
    displacement conviction has faded.
    Returns False when no breakout / retest has been recorded yet.
    """
    breakout_idx = get_breakout_idx(application_state, case, symbol, level)
    retest_idx   = get_retest_idx(application_state, case, symbol, level)

    if breakout_idx is None or retest_idx is None:
        return False
    if breakout_idx == 0 or retest_idx == 0:
        return False

    gap = abs(retest_idx - breakout_idx)
    logger.info(f"[is_retracement_gap_acceptable] {symbol} level:{level} "
                 f"breakout_idx:{breakout_idx} retest_idx:{retest_idx} gap:{gap} max_gap:{max_gap}")
    return gap <= max_gap


def compute_structured_sl(
    application_state, symbol, side, level, df,
    app_config,
    sl_type='retracement_candle',
    sl_buffer_ticks=2,
    atr_multiplier=1.0,
):
    """
    Computes a structured stop-loss price at entry time for case_5.

    sl_type options
    ───────────────
    'retracement_candle'  (default / recommended)
        SL = low of retracement candle  - buffer   (long / 'up')
        SL = high of retracement candle + buffer   (short / 'down')
        buffer = breakout_confirmation_distance * sl_buffer_ticks

    'atr'
        SL = entry_close - atr_14 * atr_multiplier   (long)
        SL = entry_close + atr_14 * atr_multiplier   (short)

    'level'
        SL = level - dynamic_tolerance * retest_tolerance_multiplier   (long)
        SL = level + dynamic_tolerance * retest_tolerance_multiplier   (short)

    Falls back gracefully to 'level' if retracement extreme is unavailable.
    Returns the SL price (float) or 0 if it cannot be computed.
    """
    tick        = app_config['symbols_meta'].get(symbol,{}).get('breakout_confirmation_distance', 0.15)
    buffer      = tick * sl_buffer_ticks
    atr         = df['atr_14'].iloc[-2]
    entry_price = df['close'].iloc[-1]
    tolerance   = (
        application_state.get('dynamic_tolerances', {}).get(symbol, {}).get('tolerance', 0)
        * app_config['symbols_meta'].get(symbol,{}).get('retest_tolerance_multiplier', 1)
    )

    if sl_type == 'retracement_candle':
        extreme = get_retracement_extreme(application_state, symbol, level)
        if extreme is None:
            logger.warning(f"[compute_structured_sl] {symbol} no retracement extreme found – falling back to level SL")
            sl_type = 'level'
        else:
            sl = (extreme - buffer) if side in ('long', 'up') else (extreme + buffer)
            logger.info(f"[compute_structured_sl] {symbol} {side} retracement_candle: "
                        f"extreme:{extreme:.4f} buffer:{buffer:.4f} - > sl:{sl:.4f}")
            return round(sl, 4)

    if sl_type == 'atr':
        sl = (entry_price - atr * atr_multiplier) if side in ('long', 'up') else (entry_price + atr * atr_multiplier)
        logger.info(f"[compute_structured_sl] {symbol} {side} atr: "
                    f"entry:{entry_price:.4f} atr:{atr:.4f} - > sl:{sl:.4f}")
        return round(sl, 4)

    # 'level' — default / fallback
    sl = (level - tolerance) if side in ('long', 'up') else (level + tolerance)
    logger.info(f"[compute_structured_sl] {symbol} {side} level: "
                f"level:{level:.4f} tol:{tolerance:.4f} - > sl:{sl:.4f}")
    return round(sl, 4)

def does_arc_has_enough_heights(app_config, application_state, case, symbol, df, side='up', level=None, level_alias=None, min_height_atr_ratio=0.5):
    """
    Arc Height filter (R2 quality check).

    Measures the maximum price excursion from the level during the arc
    (from breakout bar up to, but NOT including, the current retest bar).

    arc_pts = break_max - level          (long / 'up')
    arc_pts = level    - break_min       (short / 'down')
    arc_atr = arc_pts  / ATR(14)

    Returns True when arc_atr >= min_height_atr_ratio (default 0.5 ATR).
    """
    # --- guard checks ---
    if not level:
        logger.info(f"[does_arc_has_enough_heights] {symbol} — no level provided")
        return False

    # no retest recorded - > nothing to measure, bail out early
    retest_idx = get_retest_idx(application_state, case, symbol, level)
    if retest_idx is None:
        logger.info(f"[does_arc_has_enough_heights] {symbol} — no retest recorded for level {level}, level_alias: {level_alias}, skipping arc calc")
        return False

    breakout_idx = get_breakout_idx(application_state, case, symbol, level)
    if breakout_idx is None:
        logger.info(f"[does_arc_has_enough_heights] {symbol} — no breakout recorded for level {level}, level_alias: {level_alias}")
        return False

    atr_14 = df['atr_14'].iloc[-2]
    if atr_14 <= 0:
        logger.warning(f"[does_arc_has_enough_heights] {symbol} — atr_14 is zero/negative, skipping")
        return False

    # slice candles from breakout bar up to (but NOT including) the current retest candidate
    # breakout_idx is negative (e.g. -6); -1 stops before the current bar
    arc_df = df.iloc[breakout_idx:-1]

    if len(arc_df) == 0:
        logger.info(f"[does_arc_has_enough_heights] {symbol} — arc window is empty")
        return False

    # find the peak excursion over the arc window
    if side == 'up':
        break_max = arc_df['high'].max()
        arc_pts   = break_max - level
    else:
        break_min = arc_df['low'].min()
        arc_pts   = level - break_min

    arc_atr = arc_pts / atr_14
    passed  = arc_atr >= min_height_atr_ratio

    logger.info(
        f"[does_arc_has_enough_heights] {symbol} {level_alias} side:{side} "
        f"level:{level:.4f} arc_pts:{arc_pts:.4f} atr_14:{atr_14:.4f} "
        f"arc_atr:{arc_atr:.2f} threshold:{min_height_atr_ratio} - > {'PASS' if passed else 'FAIL'}"
    )
    return passed

def check_arc_duration(app_config, application_state, case, symbol, df, side='up', level=None, level_alias=None, arc_min_bars=4):
    """
    Arc Duration filter (R2 quality check).

    Counts the bars elapsed between the breakout bar and the retest bar.
    Returns True when bars >= arc_min_bars (default 4).
    """
    # --- guard checks ---
    if not level:
        logger.info(f"[check_arc_duration] {symbol} — no level provided")
        return False

    # no retest recorded - > nothing to measure, bail out early
    retest_idx = get_retest_idx(application_state, case, symbol, level)
    if retest_idx is None:
        logger.info(f"[check_arc_duration] {symbol} — no retest recorded for level {level}")
        return False

    breakout_idx = get_breakout_idx(application_state, case, symbol, level)
    if breakout_idx is None:
        logger.info(f"[check_arc_duration] {symbol} — no breakout recorded for level {level}")
        return False

    # both indices are negative (e.g. breakout=-6, retest=-2)
    # abs difference gives the bar count between them
    bars = abs(retest_idx - breakout_idx)
    passed = bars >= arc_min_bars

    logger.info(
        f"[check_arc_duration] {symbol} {level_alias} side:{side} "
        f"level:{level:.4f} breakout_idx:{breakout_idx} retest_idx:{retest_idx} "
        f"bars:{bars} threshold:{arc_min_bars} - > {'PASS' if passed else 'FAIL'}"
    )
    return passed

def price_retest_v2(
    app_config, application_state, case, symbol, df,
    side='up',
    idx_list=None,
    level=0,
    level_alias='',
    tol_atr=0.1,        # tol = ATR(14) * tol_atr  (default 0.1 ATR per README)
):
    """
    Retest detector — strictly follows the STRATEGY_NOTES.md R definition.

    R ↑  : low  <= level + tol   AND  close > level
    R ↓  : high >= level - tol   AND  close < level

    tol = ATR(14) * tol_atr  (default 0.1 ATR)

    - Only fires after 9:35 AM ET (first 5-min bar excluded per README).
    - Close must hold on the break side — a close back through = not an R.
    - Appends to application_state['retests'][symbol] on match.
    """
    if idx_list is None:
        idx_list = [-2, -3, -4]

    if level == 0:
        return False

    atr_14 = df['atr_14'].iloc[-2]
    tol    = atr_14 * tol_atr       # e.g. 0.1 * ATR

    retest     = False
    case_color = get_case_color(app_config, case)

    for idx in idx_list:
        row = df.iloc[idx]

        # first 5-minute bar excluded — must be after 9:35
        if date_utils.get_hhmm_int(row['date']) < 935:
            continue

        if side == 'up':
            # low touched back to within tol of level; close held above
            touched = row['low'] <= level + tol
            held    = row['close'] > level
        else:
            # high touched back to within tol of level; close held below
            touched = row['high'] >= level - tol
            held    = row['close'] < level

        if not (touched and held):
            logger.debug(
                f"[price_retest_v2] {symbol} idx:{idx} — "
                f"touched:{touched} held:{held} skip"
            )
            continue

        # --- record the retest ---
        application_state['retests'].setdefault(symbol, []).append({
            'side':        side,
            'level':       level,
            'level_alias': level_alias,
            'idx':         idx,
            'time':        str(row['date']),
        })

        offseted_price = chart_helper.get_stacked_mark_price(
            app_config, application_state, symbol,
            side='up', price=df['high'].iloc[idx],
            date=df['date'].iloc[idx], caller_key="price_retest_v2"
        )
        TradingLedger.add_to_list(
            "signals",
            (symbol, 'RETEST', offseted_price, df['date'].iloc[idx],
             f"RETEST_V2 {level_alias} tol:{tol:.4f} ... {df['date'].iloc[idx].strftime('%H:%M')}",
             case_color)
        )

        logger.info(
            f"[price_retest_v2] {symbol} {level_alias} side:{side} "
            f"level:{level:.4f} tol:{tol:.4f} "
            f"{'low' if side == 'up' else 'high'}:{row['low'] if side == 'up' else row['high']:.4f} "
            f"close:{row['close']:.4f} idx:{idx} time:{row['date']}"
        )
        retest = True

    return retest


def check_close_displacement(app_config, application_state, case, symbol, df, side='up', level=None, level_alias=None, min_close_disp_atr=0.1):
    """
    Close Displacement filter (R2 quality check).

    Finds the closest any close got to the level from the break side
    during the arc window (breakout bar - > retest bar, exclusive).

    long  : cd_pts = min(close) - level   (lowest close above level)
    short : cd_pts = level - max(close)   (highest close below level)

    Returns True when cd_pts / ATR(14) >= min_close_disp_atr (default 0.1).
    Ensures closes stayed displaced and never crept back to the level.
    """
    # --- guard checks ---
    if not level:
        logger.debug(f"[check_close_displacement] {symbol} — no level provided")
        return False

    # no retest recorded - > nothing to measure, bail out early
    retest_idx = get_retest_idx(application_state, case, symbol, level)
    if retest_idx is None:
        logger.debug(f"[check_close_displacement] {symbol} — no retest recorded for level {level}")
        return False

    breakout_idx = get_breakout_idx(application_state, case, symbol, level)
    if breakout_idx is None:
        logger.debug(f"[check_close_displacement] {symbol} — no breakout recorded for level {level}")
        return False

    atr_14 = df['atr_14'].iloc[-2]
    if atr_14 <= 0:
        logger.warning(f"[check_close_displacement] {symbol} — atr_14 is zero/negative, skipping")
        return False

    # slice arc window: breakout bar up to (but NOT including) the retest bar
    arc_df = df.iloc[breakout_idx:retest_idx]

    if len(arc_df) == 0:
        logger.debug(f"[check_close_displacement] {symbol} — arc window is empty")
        return False

    # find the closest close to the level from the correct side
    if side == 'up':
        break_min_close = arc_df['close'].min()  # lowest close above level
        cd_pts = break_min_close - level
    else:
        break_max_close = arc_df['close'].max()  # highest close below level
        cd_pts = level - break_max_close

    cd_atr = cd_pts / atr_14
    passed = cd_atr >= min_close_disp_atr

    logger.info(
        f"[check_close_displacement] {symbol} {level_alias} side:{side} "
        f"level:{level:.4f} cd_pts:{cd_pts:.4f} atr_14:{atr_14:.4f} "
        f"cd_atr:{cd_atr:.2f} threshold:{min_close_disp_atr} - > {'PASS' if passed else 'FAIL'}"
    )
    return passed


def is_second_candle_after(min_seconds=15):
    """
    Returns True if the current time is past min_seconds within the current minute.
    e.g. min_seconds=15 → True when clock shows HH:MM:15 or later.
    """
    return datetime.datetime.now().second >= min_seconds


def has_strong_close(df, side='up'):
    if side == 'up':
        if df['close'].iloc[-1] > ( df['high'].iloc[-1] + df['low'].iloc[-1] ) * 0.6:
            return True
        else:
            return False
    else:
        if df['close'].iloc[-1] < ( df['high'].iloc[-1] + df['low'].iloc[-1] ) * 0.6:
            return True
        else:
            return False

def is_symbol_in_rs_shortlist(app_config, application_state, symbol, side='up', threshold=3):
    """
    Checks if the symbol is in the RS shortlist for the current day.
    Returns True if it is, False otherwise.
    """
    rs_shortlist = application_state.get('rs_ranked_symbols', [])
    if not rs_shortlist or threshold <= 0:
        return False
    if side == 'up':
        top_ranked_symbols = rs_shortlist[:threshold]
        return symbol in top_ranked_symbols
    else:
        ranked_symbols = rs_shortlist[-threshold:]
        return symbol in ranked_symbols
