import logging
import traceback

from trading_core.trading_ledger import TradingLedger

logger = logging.getLogger(__name__)
import pandas as pd
from trading_core.file_manager import  FileManager


from trading_utils import date_utils
from trading_utils import json_utils
from trading_utils import ib_orders_async
from trading_utils import ib_positions_async
from trading_utils import number_utils
from trading_engine import notification_helper


async def check_for_stop_loss_and_take_profit(ib, app_config, application_state, market_data):
    symbols_need_to_be_removed = [] # we dont remove in the loop ..

    for symbol, open_trade_info in application_state.get('open_trades_dic', {}).items():
        logger.info(f"[check_for_stop_loss_and_take_profit] symbol {symbol}, open order unique_run_number: {open_trade_info.get('unique_run_number')}" )

        # ###
        # stop loss
        # ###
        if application_state.get('is_save_time'):
            # logger.info(f"[check_for_stop_loss_and_take_profit] {symbol}, {open_trade_info}" )
            #json_utils.print_map_pretty(open_trade_info)
            pass

        if open_trade_info.get('available_quantity', 0) == 0:
            logger.info(f"[check_for_stop_loss_and_take_profit] {symbol}, available_quantity: 0")
            continue

        symbol_df = market_data.dfs_map.get(symbol, pd.DataFrame())
        if symbol_df is None or len(symbol_df) == 0:
            logger.warning(f"[check_for_stop_loss_and_take_profit] @@@  symbol_df is None or len==0 , {symbol}")
            continue
        try:
            seconds_since_last_record = date_utils.seconds_passed_since_last_record(symbol_df)
            logger.info(f"[check_for_stop_loss_and_take_profit] @ symbol: {symbol}, seconds_since_last_record: {seconds_since_last_record}")
            if seconds_since_last_record > 70:
                logger.warning(f"[check_for_stop_loss_and_take_profit] @@@ {symbol}, seconds_since_last_record: {seconds_since_last_record}")
                logger.info(f"[check_for_stop_loss_and_take_profit] @@@ , symbol_df[-1:]\n {symbol_df[-1:].to_markdown()}")
                continue #TODO we should not do that. it maybe for manual SL or manual TP
        except Exception as e:
            logger.error(f"[check_for_stop_loss_and_take_profit] @@@@@@ , error in date check , {symbol}, e: {e}")

        # TODO check date to make sure that the data is not old
        entry_underlying_price = float(open_trade_info.get('entry_underlying_price', -1))  # used in config ...

        position_order_ref = open_trade_info.get('order_ref')
        case = open_trade_info.get('case') #
        right = open_trade_info.get('right', '') # used in config
        side = open_trade_info.get('side') # used in config
        level_used_to_open = open_trade_info['level_used_to_open'] # used in config
        dynamic_tolerance = market_data.data_store.get(symbol,{}).get('dynamic_tolerance', {})
        tolerance_amount = dynamic_tolerance.get('tolerance', 0)  # used in config
        start_quantity = application_state.get('open_trades_dic', {}).get(symbol, {}).get('starting_quantity', 0) # used in config
        available_quantity = application_state.get('open_trades_dic', {}).get(symbol, {}).get('available_quantity', 0)  # used in config
        user_defined_stop_loss = open_trade_info.get('stop_loss', 0)  # used in config
        user_defined_take_profit = open_trade_info.get('user_take_profit', 0)  # used in config
        user_defined_take_profit_quantity = open_trade_info.get('user_take_profit_quantity', 0)  # used in config

        entry_price = 0 # used in config ...
        if open_trade_info.get('entry_execution_price', 0) != 0:
            entry_price = open_trade_info.get('entry_execution_price', 0)
        else:
            entry_price = open_trade_info['entry_ask']

        from trading_utils import ib_pricing_async
        contract_month = app_config.get('symbols_meta', {}).get(symbol,{}).get('contract_month')
        underlying_current_price = await ib_pricing_async.get_or_subscribe_symbol_price(ib, symbol, contract_month)
        if len(symbol_df) == 0:
            # it maybe first run, and we don't have it yet in the dic ...
            underlying_previous_candle_close = underlying_current_price
        else:
            underlying_previous_candle_close = symbol_df['close'].iloc[-2] # used in config

        expiry = open_trade_info['expiry']
        strike = open_trade_info['strike']
        right = open_trade_info['right']
        if open_trade_info.get('position_type') == 'OPTION':
            current_bid, current_ask, current_last = await ib_pricing_async.get_or_subscribe_option_price(ib, symbol, expiry, strike,right)  # used in config
        else:
            current_bid, current_ask, current_last = await ib_pricing_async.get_or_subscribe_symbol_price(ib, symbol, contract_month)  # used in config
        if current_bid is None or current_ask is None:
            logger.warning(f"[check_for_stop_loss_and_take_profit] @@@  symbol: {symbol}, current_bid or current_ask is None, current_bid: {current_bid}, current_ask: {current_ask}")
            continue
        mid_price = (current_bid + current_ask) / 2

        # TODO handle Future ...
        if not number_utils.is_valid_price(current_bid) or not number_utils.is_valid_price(current_ask):
            logger.warning(f"[check_for_stop_loss_and_take_profit] @@@  symbol: {symbol}, current_bid or current_ask is invalid, current_bid: {current_bid}, current_ask: {current_ask}")
            continue

        # update app status ...
        if app_config['symbols_meta'].get(symbol,{}).get('contract_type','Equity') == 'Equity':
            open_trade_info['current_bid'] = current_bid
            open_trade_info['current_ask'] = current_ask
            open_trade_info['current_underlying_price'] = underlying_current_price
            open_trade_info['current_value'] = round( current_ask * open_trade_info['starting_quantity'] * 100 , 3)
            current_estimated_unrealized_pnl = round(( mid_price - entry_price) * available_quantity * 100  , 2)
            open_trade_info['current_estimated_unrealized_pnl'] = current_estimated_unrealized_pnl
            open_trade_info['current_estimated_realized_pnl'] = calculate_estimated_realized_pnl(open_trade_info)
            open_trade_info['min_bid'] = current_bid if open_trade_info.get('min_bid') == 0 else min(open_trade_info.get('min_bid'), current_bid)
            open_trade_info['max_bid'] = max(open_trade_info.get('max_bid'), current_bid)

        else: # it is future ...
            open_trade_info['current_bid'] = current_bid
            open_trade_info['current_ask'] = current_ask
            open_trade_info['current_underlying_price'] = underlying_current_price
            open_trade_info['current_value'] = underlying_current_price * 1 # TODO available...

        xui_position_setting = app_config.get("xui_positions_settings",{}).get(position_order_ref, {})

        logger.info(f"[check_for_stop_loss_and_take_profit] level_used_to_open: {level_used_to_open}, entry_underlying_price: {entry_underlying_price}, "
                    f"underlying_current_price:, {underlying_current_price}, underlying_previous_candle_close: {underlying_previous_candle_close} ,tolerance_amount: {tolerance_amount}")
        logger.info(f"[check_for_stop_loss_and_take_profit] current_bid: {current_bid}, current_ask: {current_ask}")
        stop_loss_condition_evaluated = False
        for stop_loss_condition in app_config.get('stop_losses', []):
            if stop_loss_condition_evaluated:
                logger.info(f"[check_for_stop_loss_and_take_profit] Already evalauted, so skip, stop_loss_condition_evaluated: {stop_loss_condition_evaluated}")
                break
            stop_loss_condition = stop_loss_condition.replace('\n', ' ')
            stop_loss_condition_evaluated = eval(stop_loss_condition)

            logger.info(f"[check_for_stop_loss_and_take_profit] symbol {symbol}, stop_loss_condition: {stop_loss_condition}, stop_loss_condition_evaluated: {stop_loss_condition_evaluated}")

            if stop_loss_condition_evaluated:
                logger.warning(f"[check_for_stop_loss_and_take_profit] {symbol} SL condition met ... {stop_loss_condition}")
                order_ref = ib_orders_async.generate_order_ref(application_state.get('portfolio_id'), event='CLOSE', symbol=symbol, alias='SL', unique_run_number=application_state.get('unique_run_number'))
                con_id = open_trade_info.get('con_id')
                close_result = ib_positions_async.close_position_by_con_id(ib, con_id=con_id, order_ref=order_ref )
                # close_option_positions(option_positions_to_monitor, symbol=symbol, order_ref=order_ref)
                if not close_result:
                    logger.warning(f"[check_for_stop_loss_and_take_profit] @@@@ we couldn't close the position for SL, so we skip the rest {symbol}  {open_trade_info.get('order_ref')}  needs more investigation ")
                    continue
                data = {
                    'symbol': symbol,
                    'right': open_trade_info['right'],
                    'strike':  open_trade_info['strike'],
                    'expiry': open_trade_info['expiry'],
                    'current_bid': current_bid,
                    'current_ask': current_ask,
                    'underlying_current_price': underlying_current_price,
                    'available_quantity_b4': available_quantity,
                    'sl_u_run_number': application_state.get('unique_run_number'),
                    'stop_loss_condition': stop_loss_condition,
                    'candle_date': str(symbol_df['date'].iloc[-1]),
                    'open_u_run_number': '',
                    'open_order_ref': '',
                    'sl_order_ref': order_ref,
                    'local_symbol': open_trade_info.get('local_symbol'),
                    'con_id': open_trade_info.get('con_id'),
                    }
                open_trade_info.setdefault('stop_loss_history', []).append(data) # save it in the
                archive_open_trade_dic(application_state, symbol)
                application_state.setdefault('open_trades_dic', {})[symbol] = {}  #  TODO This need to be happened after we get required inf from dic...
                add_order_ref_to_application_state(application_state, open_order_ref=open_trade_info.get('order_ref'), close_order_ref=order_ref)

                # add_to_stop_loss_history_df(data)
                TradingLedger.add_to_dataframe('stop_loss_history_df', data)

                # add_to_signals(symbol, 'STOP_LOSS_SENT', underlying_current_price, df['date'].iloc[-1], f"STOP_LOSS  <BR> {json_utils.polish_map_to_show_in_hover(data)}")
                TradingLedger.add_to_list("signals",(symbol, 'STOP_LOSS_SENT', underlying_current_price, symbol_df['date'].iloc[-1], f"STOP_LOSS  <BR> {json_utils.polish_map_to_show_in_hover(data)}"))

                notification_helper.send_email(app_config, event='stop_loss_sent', symbol=symbol, body=json_utils.polish_map_to_show_in_hover(data))



        # ###
        # Take profit
        # ###

        take_profit_condition = ''
        tp_is_enabled = True

        if app_config.get('take_profit_policy',{}).get('default_enabled',True) == False:
            logger.warning(f"[check_for_stop_loss_and_take_profit] {symbol} TP condition is disabled in the take_profit_policy config")
            tp_is_enabled = False
        elif app_config.get('take_profit_policy',{}).get('symbols',{}).get(symbol,{}).get('tp_enabled', True) == False:
            logger.warning(f"[check_for_stop_loss_and_take_profit] {symbol} TP condition is disabled in the take_profit_policy config")
            tp_is_enabled = False
        elif app_config.get('xui_symbol_controls', {}).get("symbols",{}).get(symbol,{}).get('tp_enabled', True) == False:
            logger.warning(f"[check_for_stop_loss_and_take_profit] {symbol} TP condition is disabled in the xui_symbol_controls config")
            tp_is_enabled = False

        order_closed_by_tp = False

        for take_profit_lable in app_config['take_profits']:
            logger.info(f"[check_for_stop_loss_and_take_profit] symbol {symbol}, take_profit_lable: {take_profit_lable}")

            if tp_is_enabled == False and not app_config.get('take_profits',{}).get(take_profit_lable ,{}).get('always_run',False):
                logger.info(f"[check_for_stop_loss_and_take_profit] tp_is_enabled is False. so no check ...symbol: {symbol}")
                continue

            if application_state['open_trades_dic'].get(symbol,{}).get('available_quantity',0) == 0:
                logger.info(f"[check_for_stop_loss_and_take_profit] {symbol}, {take_profit_lable}, available_quantity is 0 ")
                continue
            #
            if is_take_profit_executed(open_trade_info, take_profit_lable): # except t7_user_defined_take_profit
                logger.info(f"[check_for_stop_loss_and_take_profit] {symbol}, TP already is executed ... {take_profit_lable}")
                continue
            # both are False
            if xui_position_setting.get("take_profits",{}).get(take_profit_lable, False) == False and app_config.get('take_profits',{}).get(take_profit_lable ,{}).get('enabled',True) == False:
                logger.info(f"[check_for_stop_loss_and_take_profit] {symbol}, TP is disabled in take_profits  ... {take_profit_lable}")
                continue
            # check of specific position only
            if xui_position_setting.get("take_profits",{}).get(take_profit_lable, {}).get('enabled',True) == False:
                logger.info(f"[check_for_stop_loss_and_take_profit] {symbol}, TP is disabled in for this postion only  ... {take_profit_lable}")
                continue
            if app_config.get('take_profit_configs',{}).get(take_profit_lable ,{}).get('enabled',True) == False:
                logger.info(f"[check_for_stop_loss_and_take_profit] {symbol}, TP is disabled in take_profit_configs  ... {take_profit_lable}")
                continue

            close_type = app_config.get('take_profit_configs',{}).get(take_profit_lable ,{}).get('close_type') # percentage or price   # used in config
            if close_type is None: # if not in the config, we will use the legacy one ...
                close_type = app_config.get('take_profits',{}).get(take_profit_lable ,{}).get('close_type', "percentage") # percentage or price   # used in config
            number_of_trails = app_config.get('take_profit_configs',{}).get(take_profit_lable ,{}).get('number_of_trails',0)   # used in config
            percentage_change = 1.0 + app_config.get('take_profit_configs',{}).get(take_profit_lable ,{}).get('percentage_change',0)   # used in config
            absolute_change = app_config.get('take_profit_configs',{}).get(take_profit_lable ,{}).get('absolute_change',0)  # used in config

            take_profit_condition = app_config['take_profits'][take_profit_lable].get('condition', '1 == 2')
            close_quantity_percentage = app_config['take_profits'][take_profit_lable].get('close_quantity_percentage', 0)
            close_quantity_numbner = app_config['take_profits'][take_profit_lable].get('close_quantity_numbner', "1*0")
            take_profit_condition_evaluated = eval(take_profit_condition.replace('\n', ' '))
            logger.info(f"[check_for_stop_loss_and_take_profit] symbol {symbol}, take_profit_condition: {take_profit_condition}")

            if close_type == 'percentage':
                if close_quantity_percentage == -1: # close all
                    close_quantity = available_quantity
                else:
                    close_quantity = int(start_quantity * close_quantity_percentage )   # we take the less. dont do round
                    close_quantity = 1 if close_quantity == 0 else close_quantity  # we want to make sure 0.4 * 1 will return 1.
            elif close_type == 'absolute':  # this is trail, not absolute
                close_quantity = available_quantity - number_of_trails
            elif close_type == 'real_absolute':
                close_quantity = eval(close_quantity_numbner)
            else:
                logger.info(f"[check_for_stop_loss_and_take_profit] @@@ close_type is not supported. close_type: {close_type}")

            logger.info(f"[check_for_stop_loss_and_take_profit] available_quantity: {available_quantity}, close_quantity_percentage: {close_quantity_percentage}, close_quantity: {close_quantity}, start_quantity:{start_quantity}")
            logger.info(f"[check_for_stop_loss_and_take_profit] take_profit_condition: {take_profit_condition}")
            logger.info(f"[check_for_stop_loss_and_take_profit] {take_profit_lable}, take_profit_condition_evaluated: {take_profit_condition_evaluated}")
            order_ref = ''

            if take_profit_condition_evaluated and available_quantity > 0 and close_quantity != 0 and close_quantity <= available_quantity :
                logger.info(f"[check_for_stop_loss_and_take_profit] Sending TP ...{take_profit_lable}")
                if app_config['symbols_meta'].get(symbol,{}).get('contract_type','Equity') == 'Equity':
                    order_ref = ib_orders_async.generate_order_ref(application_state.get('portfolio_id'), event='CLOSE', symbol=symbol, alias=take_profit_lable, unique_run_number=application_state.get('unique_run_number'))
                    con_id = open_trade_info.get('con_id')
                    close_result = ib_positions_async.close_position_by_con_id(ib, con_id = con_id, qty_to_close=close_quantity, order_ref=order_ref)
                    if not close_result:
                        logger.info(f"[check_for_stop_loss_and_take_profit] @@@@ we couldn't close the position for TP, so we skip the rest ... {symbol} - needs more investigation ")
                elif app_config['symbols_meta'].get(symbol,{}).get('contract_type','Equity') == 'Future':
                    order_ref = ib_orders_async.generate_order_ref(application_state.get('portfolio_id'), event='CLOSE', symbol=symbol, alias=take_profit_lable, unique_run_number=application_state.get('unique_run_number'))
                    con_id = open_trade_info.get('con_id')
                    ib_positions_async.close_position_by_con_id(ib, con_id=con_id, qty_to_close=close_quantity,order_ref=order_ref)
                else:
                    logger.warning(f"[check_for_stop_loss_and_take_profit] @@@@ TBD")
                order_closed_by_tp = True

                # DO NOT REMOVE BREAK. after tp is executed we need to remove the loop. if not, other may be executed and also wrong info in the email
                break



        for forced_exit_entry in application_state.get('forced_exits', []):
            if order_closed_by_tp:
                continue
            if 'SENT_TO_IB' in forced_exit_entry.get('status'):
                continue
            if forced_exit_entry.get('symbol') == symbol  :

                logger.info(f"[check_for_stop_loss_and_take_profit] Forced exit for {symbol} is found in application_state, so we will execute the exit as well ...")
                order_ref = ib_orders_async.generate_order_ref(application_state.get('portfolio_id'), event='CLOSE', symbol=symbol, alias=f"FORCED_EXIT", unique_run_number=application_state.get('unique_run_number'))
                con_id = forced_exit_entry.get('contract_id')
                close_quantity = forced_exit_entry.get('quantity', available_quantity) # if quantity is not provided we will close all ...
                close_result = ib_positions_async.close_position_by_con_id(ib, con_id=con_id, qty_to_close=close_quantity, order_ref=order_ref)
                if not close_result:
                    logger.info(f"[check_for_stop_loss_and_take_profit] @@@@ we couldn't close the position for TP, so we skip the rest ... {symbol} - needs more investigation ")
                order_closed_by_tp = True
                take_profit_lable = 'tp_forced_exit'
                forced_exit_entry['status'] += '|SENT_TO_IB'
            else:
                logger.info(f"[check_for_stop_loss_and_take_profit] we are processing {symbol} ...")

        if order_closed_by_tp:
            open_trade_info['available_quantity'] = available_quantity - close_quantity
            entry_execution_price = open_trade_info['entry_execution_price']
            take_profit_estimated_pnl = (mid_price - entry_execution_price) * close_quantity * 100 if entry_execution_price !=0 else 0
            take_profit_estimated_pnl = round(take_profit_estimated_pnl, 3)
            data = {
                'status': 'SENT',
                'available_quantity_b4' : available_quantity,
                'close_quantity': close_quantity,
                'candle_date': str(symbol_df['date'].iloc[-1]),
                'take_profit_estimated_pnl': take_profit_estimated_pnl,
                'entry_execution_price': entry_execution_price,
                'current_bid': current_bid,
                'current_ask': current_ask,
                'tp_u_run_number': application_state.get('unique_run_number'),
                'order_ref': order_ref,
            }
            # open_trade_info.setdefault('take_profits', {})[take_profit_lable] = data

            data = {
                'symbol': symbol,
                'right': open_trade_info.get('right'),
                'strike': open_trade_info.get('strike'),
                'expiry': open_trade_info.get('expiry'),
                'entry_execution_price': entry_execution_price,
                'current_bid': current_bid,
                'current_ask': current_ask,
                'underlying_current_price': underlying_current_price,
                'available_quantity_b4': available_quantity,
                'take_profit_case': take_profit_lable,
                'take_profit_condition': take_profit_condition,
                'take_profit_estimated_pnl': take_profit_estimated_pnl,
                'close_quantity': close_quantity,
                'candle_date': str(symbol_df['date'].iloc[-1]),
                'tp_u_run_number': application_state.get('unique_run_number'),
                'order_ref': order_ref,
                'local_symbol': open_trade_info.get('local_symbol'),
                'con_id': open_trade_info.get('con_id'),
                'user_defined_take_profit': user_defined_take_profit,
                'user_defined_take_profit_quantity': user_defined_take_profit_quantity,
            }
            open_trade_info.setdefault('take_profit_history', []).append(data)

            add_order_ref_to_application_state(application_state, open_order_ref=open_trade_info.get('order_ref'), close_order_ref=order_ref)
            TradingLedger.add_to_dataframe('take_profit_history_df', data)
            TradingLedger.add_to_list("signals", (symbol, 'TAKE_PROFIT_SENT', underlying_current_price, symbol_df['date'].iloc[-1], f"TAKE-PROFIT-{take_profit_lable} <BR>{json_utils.polish_map_to_show_in_hover(data)}"))
            notification_helper.send_email(app_config, event='take_profit_sent', symbol=symbol, body=json_utils.polish_map_to_show_in_hover(data), subject=take_profit_lable)
            increment_wins(application_state, symbol)

            # This is very import. There was a case that after t1 execution, t2 condition meet also
            # but the available_quantity was not updates. look at the for iterator. we are updating what we are iterating it ...
            # DO MOT DELETE THIS. we go out, and we will come back i next .... if break didn't work we need to use return ...
            break




    for symbol, open_trade_info in application_state.get('open_trades_dic', {}).items():
        if application_state['open_trades_dic'].get(symbol, {}) != {} and open_trade_info.get('available_quantity', 0) <= 0:
            logger.info(f"{symbol}, the available_quantity is zero, so we set empty dic for it")
            symbols_need_to_be_removed.append(symbol)

    for s in symbols_need_to_be_removed:
        archive_open_trade_dic(application_state, s)
        remove_symbol_from_open_trade_dic(application_state, s)

    return

def add_order_ref_to_application_state(application_state, open_order_ref='', close_order_ref=''):
    return

    if open_order_ref != '' and close_order_ref == '': # this is for open order ...
        application_state.setdefault('open_close_refs_map', {})[open_order_ref] = []
    elif open_order_ref != '' and close_order_ref != '': # this is for close ...
        entry = {open_order_ref: close_order_ref}
        application_state.setdefault('open_close_refs_list', []).append(entry) # add to the list ...

        if  not application_state.get('open_close_refs_map', {}).get(open_order_ref): #  opn is not there,sso add it …
            application_state.setdefault('open_close_refs_map', {})[open_order_ref] = []

        application_state.get('open_close_refs_map', {}).get(open_order_ref).append(close_order_ref) # now add the close ...
    else:
        logger.info(f"@@@ add_order_ref_to_application_state, is not supported, open_order_ref: {open_order_ref}, close_order_ref: {close_order_ref}")
    data = {
        'open_order_ref': open_order_ref,
        'close_order_ref': close_order_ref,
        'ib_exec_id': ''
    }

    TradingLedger.add_to_dataframe('open_close_refs_df', data)
    return

def archive_open_trade_dic(application_state, symbol):
    FileManager.save_named_json(application_state, file_name=f"84-{application_state.get('unique_run_number')}-{symbol}.json",
                            dir='intermediate')
    order_info = application_state.get("open_trades_dic", {}).get(symbol, {})

    order_ref  = order_info.get('order_ref', 'x')
    symbol = order_info.get('symbol','')
    side = order_info.get('side','')
    right = order_info.get('right','')

    file_name = f"{application_state.get('unique_run_number')}-{symbol}-{side}-{right}.json"

    FileManager.save_named_json(application_state.get("open_trades_dic",{}).get(symbol),
                                file_name=file_name, dir='intermediate')
    return



def remove_symbol_from_open_trade_dic(application_state, symbol):
    application_state['open_trades_dic'].pop(symbol, None)
    return





def is_price_crossed_levels(application_state, side='down', symbol='', next_levels=['PDL'], current_price=-1, entry_underlying_price=-1):
    if next_levels is None:
        return False

    levels_map = application_state.get('levels', {}).get(symbol, {})
    for key in next_levels:
        next_level_price = levels_map.get(key, None)
        if next_level_price is None:
            continue  # skip missing levels

        # current_price > the next level AND the open price < next level , so we croessed the level
        if side == "up" and current_price >  next_level_price and entry_underlying_price < next_level_price:
            return True
        if side == "down" and current_price < next_level_price and entry_underlying_price > next_level_price:
            return True

    return False


def check_mark_revers_candles(application_state, symbol, take_profit_alias=None, market_data= None):
    # TODO remove try later ...
    try:
        df = market_data.dfs_map.get(symbol)
        logger.info(f"[check_mark_revers_candles] ... {symbol}")
        result = False
        tp_candle_date = application_state['open_trades_dic'].get(symbol,{}).get('take_profits',{}).get(take_profit_alias,{}).get('candle_date',None)
        logger.info(f"[check_mark_revers_candles] {symbol}, tp_candle_date: {tp_candle_date}")

        if tp_candle_date == None:
           return False

        right = application_state['open_trades_dic'].get(symbol,{}).get('right', '')
        logger.info(f"[check_mark_revers_candles] {symbol}, right: {right} ")


        if len(df) == 0:
            logger.warning(f"@@ len(df) is zero")
            return False
        logger.info(f"[check_mark_revers_candles] {symbol}, df[-5:]\n {df[-5:].to_markdown()}")
        crossed_ema9 = False
        if right == 'C':
            crossed_ema9 = True if df["close"].iloc[-1] < df["ema_9"].iloc[-1] else False
        else:
            crossed_ema9 = True if df["close"].iloc[-1] > df["ema_9"].iloc[-1] else False

        check_date = df['date'].iloc[-1]
        prev_close = df["close"].iloc[-2]

        target_date = pd.Timestamp(tp_candle_date)

        df = df[df["date"] >= target_date]
        logger.info(f"@@ check_mark_revers_candles, {symbol}, prev_close: {prev_close}. ")
        logger.info(f"[check_mark_revers_candles] {symbol}, first two \n {df[:2].to_markdown()}")
        logger.info(f"[check_mark_revers_candles] {symbol}, last two \n {df[-2:].to_markdown()}")

        df = df[:-2]                           # cut the latest row and the prev one as we comparing against it ...
        logger.info(f"@@ check_mark_revers_candles, {symbol}, candles we checking - after cutting last two (need to be verified)\n {df[-5:].to_markdown()}")
        if right == 'C':

            df["is_bearish"] = df["close"] < df["open"]
            lowest_bearish_low = df.loc[df["is_bearish"], "low"].min()

            result = prev_close < lowest_bearish_low and crossed_ema9
            logger.info(f"[check_mark_revers_candles] {symbol}, lowest_bearish_low: {lowest_bearish_low}, prev_close: {prev_close}, {result}, \n{df[-5:].to_markdown()}")
            if result:
                logger.info(f"[check_mark_revers_candles] {symbol}, The break happened. lowest_bearish_low: {lowest_bearish_low}, prev_close: {prev_close}")
                # add_to_signlas(symbol, 'LEVEL_REPLACED', df['close'].iloc[-1], check_date, f'Level is break out {check_date}<br> t_date: {target_date} <br>  lowest_bearish_low: {lowest_bearish_low} <br> prev_close: {prev_close}' )
                TradingLedger.add_to_list("signals",(symbol, 'LEVEL_REPLACED', df['close'].iloc[-1], check_date, f'Level is break out {check_date}<br> t_date: {target_date} <br>  lowest_bearish_low: {lowest_bearish_low} <br> prev_close: {prev_close}' ))

        else:

            df["is_bulish"] = df["close"] > df["open"]
            highest_bulish_high = df.loc[df["is_bulish"], "high"].max()

            result = prev_close > highest_bulish_high and crossed_ema9
            logger.info(f"[check_mark_revers_candles] {symbol}, highest_bulish_high: {highest_bulish_high}, prev_close: {prev_close}, result: {result}, \n {df[-5:].to_markdown()}")
            if result:
                logger.info(f"[check_mark_revers_candles] {symbol}, The break happened. highest_bulish_high: {highest_bulish_high}, prev_close: {prev_close}")
                # add_to_signlas(symbol, 'LEVEL_REPLACED', df['close'].iloc[-1], check_date, f'Level is break out {check_date}<br> t_date: {target_date} <br>  highest_bulish_high: {highest_bulish_high} <br> prev_close: {prev_close}' )
                TradingLedger.add_to_list("signals", (symbol, 'LEVEL_REPLACED', df['close'].iloc[-1], check_date, f'Level is break out {check_date}<br> t_date: {target_date} <br>  highest_bulish_high: {highest_bulish_high} <br> prev_close: {prev_close}' ))

        if result:
            logger.info(f"[check_mark_revers_candles] The break happened. ")

        return result
    except Exception as e:
        logger.error(f"@@@ error: {e}")
        logger.error(traceback.format_exc())
    return result



def increment_wins(application_state, symbol):
    trading_date = application_state.get('trading_date', 'N/A')

    wins_by_day = application_state.setdefault('number_of_wins', {})
    wins_by_day[trading_date] = wins_by_day.get(trading_date, 0) + 1

    logger.info(f"increment_wins, {trading_date}, number_of_wins: {application_state['number_of_wins'][trading_date]}")
    return

def calculate_estimated_realized_pnl(open_trade_info):
    estimated_realized_pnl = 0

    for tp in open_trade_info.get('take_profit_history', []):
        estimated_realized_pnl += tp.get('take_profit_estimated_pnl',0)

    return round(estimated_realized_pnl,3)

def is_executed_take_profits(application_state, symbol, take_profit_list=[]): # used in config
    for take_profit_alias in take_profit_list:
        open_trade_info =  application_state.get('open_trades_dic',{}).get(symbol,{})
        for tp in open_trade_info.get('take_profit_history', []):
            if tp.get('take_profit_case') == take_profit_alias:
                return True

    return False

def is_take_profit_executed(open_trade_info, take_profit_alias):

    for tph in open_trade_info.get('take_profit_history', []):
        if tph.get('take_profit_case') == take_profit_alias:
            if take_profit_alias == "t7_user_defined_take_profit":
                # if it is user defined, check the price as well, because user can change it in the config and we need to make sure that we are not executing the same one again ...
                if open_trade_info.get('user_take_profit', 0) ==  tph.get('user_defined_take_profit', -1):
                    return True
            else: # other ones we dont check price
                return True
    return False

