import json
import logging
import traceback

import pandas as pd

from trading_core.file_manager import FileManager

logger = logging.getLogger(__name__)
from trading_utils import date_utils
from trading_utils import notification_utls
from trading_utils import ib_orders_async
from trading_utils import ib_contract
from trading_utils import json_utils
from trading_utils import number_utils
from trading_utils import config_utils
from trading_utils import ib_pricing_async

from trading_core import application_state_router

from trading_core.trading_ledger import TradingLedger
from trading_core.file_manager import FileManager

from trading_engine import options_helper
from trading_engine import pricing_helper
from trading_engine import risk_helper
from trading_engine import notification_helper
from trading_engine import position_helper

def add_case_manual_order_to_buy_sell_case_results_list(application_state, buy_sell_case_results_list, market_data):
    needs_to_be_removed = []
    for user_request in application_state.get('case_manual_orders',[]):

        case = "case_manual"
        symbol = user_request.get('symbol')
        logger.info(f"[add_case_manual_order_to_buy_sell_case_results_list] processing user request for manual order, symbol:{symbol}, user_request:{user_request}")
        quantity = int(user_request.get('quantity', 0))
        side = user_request.get('side', 'long')
        side = 'long' if side.lower() in ('long', 'buy') else 'short'
        right = user_request.get('right', 'C')
        right = 'C' if right.lower() in ('c', 'call') else 'P'
        order_type = user_request.get('order_type', 'Option')
        user_defined_stop_loss = user_request.get('stop_loss', 0) # if there is stop loss in the user request, we should not send it
        user_defined_expiry = user_request.get('expiry', 0)
        user_defined_strike = user_request.get('strike', 0)


        can_buy = True if right == 'C' else False
        can_sell = True if right == 'P' else False
        symbol_df = market_data.dfs_map.get(symbol)  # used in config
        latest_price = symbol_df['close'].iloc[-1] if symbol_df is not None and not symbol_df.empty else 0

        details_map = {
            'symbol': symbol,
            'res_str': (
                f"Manual order from user request, quantity: {quantity}, side: {side}, "
                f"order_type: {order_type}, strike: {user_defined_strike}, expiry: {user_defined_expiry}"
            ),
            'long_level': 0,
            'short_level': 0,
            'right': right,
            'user_defined_quantity': quantity,
            'user_defined_stop_loss': user_defined_stop_loss,
            'user_defined_expiry': user_defined_expiry,
            'user_defined_strike': user_defined_strike,
            'price': latest_price
        }
        res = (case, can_buy, can_sell, details_map)
        buy_sell_case_results_list.append(res)
        needs_to_be_removed.append(user_request)

    for x in needs_to_be_removed:
        application_state['case_manual_orders'].remove(x)

    return buy_sell_case_results_list



def add_entry_message_to_application_state(application_state, symbol, date, message):
    existing = application_state.setdefault('entry_signals', [])
    # MAKE SURE IT IS UNIQUE ...
    if not any(e['timestamp'] == date and e['symbol'] == symbol and e['message'] == message for e in existing):
        existing.append({'timestamp': str(date), 'symbol': symbol, 'message': message})
        application_state_router.add_audit_message(application_state, message, severity="medium" )


async def check_buy_sell_result_to_send_order(ib, app_config, application_state, buy_sell_case_results_list, df, market_data, runtime):

    current_hh_mm_ny = date_utils.get_current_hhmm_ny() # used in config ...
    is_trade_time = eval(app_config['trading_session']['trade_time'])

    for buy_sell_case_result in buy_sell_case_results_list:

        logger.debug(f"buy_sell_case_result: {buy_sell_case_result} , type(buy_sell_case_result): {type(buy_sell_case_result)}")
        case = buy_sell_case_result[0]
        can_buy = buy_sell_case_result[1]
        can_sell = buy_sell_case_result[2]
        details_map = buy_sell_case_result[3]
        symbol = details_map.get('symbol')

        logger.debug(f"[check_buy_sell_result_to_send_order], {symbol}, can_buy: {can_buy}, can_sell:{can_sell}")
        if can_buy == False and can_sell == False: # no success ...
            continue
        if not case in app_config['order_cases']:
            logger.info(f"[check_buy_sell_result_to_send_order], {symbol}, case: {case} is not in order_cases in config, skipping...")
            continue
        logger.info(f"[check_buy_sell_result_to_send_order] order signal , {symbol}, can_buy: {can_buy}, can_sell:{can_sell}")

        case_result = details_map.get('res_str')
        long_level = details_map.get('long_level', 0)
        short_level = details_map.get('short_level', 0)
        level_used = long_level if can_buy else short_level
        contract_type = app_config['symbols_meta'].get(symbol,{}).get('contract_type','Equity')
        do_check = True if case != 'case_manual' else False
        user_defined_quantity = 0 if case != 'case_manual' else int(details_map.get('user_defined_quantity', 0))
        user_defined_expiry = 0 if case != 'case_manual' else details_map.get('user_defined_expiry', 0)
        user_defined_strike = 0 if case != 'case_manual' else details_map.get('user_defined_strike', 0)

        market_trend = 'up' if can_buy else 'down' #
        right = 'C' if can_buy else 'P'

        # context_filter(application_state, details_map, df, application_state['unique_run_number'], symbol, right, level_used)

        if do_check and not app_config['symbols_meta'].get(symbol,{})['can_trade']:
            logger.info(f"[check_buy_sell_result_to_send_order] @@ We are not trading {symbol}.")
            continue
        if do_check and not is_trade_time:
            logger.warning(f"[check_buy_sell_result_to_send_order] @@ is_trade_time:{is_trade_time}, {symbol}, {app_config['trading_session']['trade_time']}")
            continue
        if application_state.get('open_trades_dic', {}).get(symbol,{}).get('available_quantity', 0) != 0:
            logger.warning(f"[check_buy_sell_result_to_send_order] @@ You already have open position. Don't be greedy!!!  symbol: {symbol}")
            continue
        if do_check and number_of_positions_today(application_state, symbol) > app_config['positioning']['max_num_of_trade_per_symbol_per_day']:
            logger.warning(f"[check_buy_sell_result_to_send_order] @@  We already sent enough orders for {symbol}")
            continue
        if do_check and number_of_total_positions_today(application_state) >= app_config['positioning']['max_number_of_trades_per_day']:
            logger.warning(f"[check_buy_sell_result_to_send_order] @@  We already sent enough orders for {symbol}")
            continue
        if do_check and has_open_order_in_same_group(app_config, application_state, symbol):
            logger.warning(f"[check_buy_sell_result_to_send_order] @@  We already have open order in same group {symbol}")
            continue
        if do_check and symbol in app_config.get('manual_settings',{}).get('blocked_symbols',{})[right]:
            logger.warning(f"[check_buy_sell_result_to_send_order] @@  This symbol is blocked, {symbol}, {app_config.get('manual_settings',{}).get('blocked_symbols',{})[right]}")
            continue
        if do_check and  number_of_wins(application_state, symbol) >= app_config.get('positioning',{}).get('stop_after_wins', 100):
            logger.warning(f"[check_buy_sell_result_to_send_order] @@  Today we had enough wins, {symbol}")
            continue
        if do_check and not check_manual_conditions(app_config, application_state, symbol, right):
            logger.warning(f"[check_buy_sell_result_to_send_order] @@  check_manual_conditions failed, {symbol}")
            if runtime.should_run_once(f"manual-condition-failed-{symbol}-{str(df['date'].iloc[-1])}"):
                TradingLedger.add_to_list("signals", (symbol, f"MANUAL_CONDITION_FAILED", df['high'].iloc[-1], df['date'].iloc[-1], f"{case} - blocked by manual settings", 'YELLOW'))
                add_entry_message_to_application_state(application_state, symbol, str(df['date'].iloc[-1]), f'{symbol} {right} is blocked by manual entry')

            continue

        if do_check and not check_xui_symbol_controls(app_config, application_state, symbol, right):
            logger.warning(f"[check_buy_sell_result_to_send_order] @@  check_xui_symbol_controls failed, {symbol}")
            add_entry_message_to_application_state(application_state, symbol, str(df['date'].iloc[-1]), f'{symbol} {right} is blocked by XUI')
            if runtime.should_run_once(f"check_xui_symbol_controls-failed-{symbol}-{str(df['date'].iloc[-1])}"):
                TradingLedger.add_to_list("signals", (symbol, f"CHECK_XUI_SYMBOL_CONTROLS_FAILED", df['high'].iloc[-1], df['date'].iloc[-1], f"{case} - CHECK_XUI_SYMBOL_CONTROLS_FAILED ", 'BLACK'))
            continue


        # FIXME mark_score_in_the_chart(market_trend)

        if contract_type.lower() == 'equity' and (can_buy or can_sell): # go for buy
            right = 'C' if can_buy else 'P'
            option_contract = await options_helper.prepare_option_contract(ib, app_config, application_state, market_data, symbol, right=right, user_defined_expiry=user_defined_expiry, user_defined_strike=user_defined_strike)
            if option_contract == None:
                notification_utls.notify_user(app_config, application_state, subject= f"Contract is null - {symbol} - {app_config.get('user_name')}",
                                              msg=f"@@@@@ prepare_contract returned None. We are not sending order. symbol={symbol}, option_contract={option_contract}")
                logger.warning(f"[check_buy_sell_result_to_send_order] @@@@@ We are not sending order. {symbol}, option_contract: {option_contract}")
                continue
            bid, ask, last = await pricing_helper.get_quote_for_option_bid_ask(ib, symbol=symbol, expiry=option_contract.lastTradeDateOrContractMonth, strike=option_contract.strike, right=option_contract.right)
            if not number_utils.is_valid_price(bid) or not number_utils.is_valid_price(ask):
                notification_utls.notify_user(app_config, application_state, subject= f"Bid or Ask is null {app_config.get('user_name')}", msg=f"Bid or Ask returned None. We are not sending order. symbol={symbol}, option_contract={option_contract}")
                logger.warning(f"[check_buy_sell_result_to_send_order] @@@@@ We are not sending order. bid: {bid} or ask: {ask}")
                continue

            total_quantity, capital_data = risk_helper.calculate_number_of_option_contracts(app_config, application_state, symbol, option_contract.strike, ask, user_defined_quantity)
            add_to_capital_allocation_df(application_state, capital_data)
            if total_quantity == 0:  # we don't have enough capital
                logger.warning(f"[check_buy_sell_result_to_send_order] @@ We dont have enough capital {symbol} ....")
                TradingLedger.add_to_list("signals", (symbol, f"NOT_ENOUGH_CAPITAL", df['high'].iloc[-1], df['date'].iloc[-1], f"NOT_ENOUGH_CAPITAL- ask: {ask}", 'YELLOW'))
                continue
            order_ref = ib_orders_async.generate_order_ref(application_state.get('portfolio_id'), event='OPEN', symbol=symbol, side='long', unique_run_number=application_state.get('unique_run_number'), right= right)

            underlying_price = await ib_pricing_async.get_or_subscribe_symbol_price(ib, symbol)
            await send_order(ib, option_contract, side='long', total_quantity=total_quantity, order_ref=order_ref, ib_account_id=app_config.get('ib_account_id'))
            data = {
                'date': f'{date_utils.time_now()}',
                'symbol': symbol,
                'side': 'long',
                'right': right,
                'starting_quantity':total_quantity,
                'available_quantity':total_quantity,
                'entry_underlying_price': underlying_price,
                'entry_bid': bid, #TODO need to be fixed ...
                'entry_ask': ask, #TODO need to be fixed ...
                'entry_execution_price': 0,
                "current_bid": 0,
                "current_ask": 0,
                "current_underlying_price": 0,
                "current_value": 0,
                "current_estimated_unrealized_pnl":0,
                "current_estimated_realized_pnl": 0,
                'max_bid': 0,
                'min_bid': 0,
                'strike': option_contract.strike,
                'expiry': option_contract.lastTradeDateOrContractMonth,
                'position_type': 'OPTION',
                'unique_run_number': application_state.get('unique_run_number'),
                'case': case,
                'level_used_to_open': level_used,
                'level_name': '',
                'stop_loss': details_map.get('user_defined_stop_loss',0),
                'local_symbol': option_contract.localSymbol,
                'con_id': option_contract.conId,
                'order_ref': order_ref
            }
            application_state.setdefault('open_trades_dic', {})[symbol] = data
            order_sent_price = df['close'].iloc[-1]
            if case == "case_manual":
                order_sent_price = details_map.get("price", 1)

            TradingLedger.add_to_list("signals", (symbol, f'ORDER_SENT',order_sent_price, df['date'].iloc[-1], json_utils.polish_map_to_show_in_hover(data)))

            TradingLedger.add_to_dataframe("order_history_df", data)

            add_to_number_of_positions_today(application_state, symbol)
            notification_helper.send_email(app_config, event='order_sent', symbol=symbol, body=json_utils.polish_map_to_show_in_hover(data))
            add_order_ref_to_application_state(application_state, open_order_ref=order_ref)
            add_open_order_to_capital_flow_df(data, capital_data)
            add_entry_message_to_application_state(application_state, symbol, str(df['date'].iloc[-1]), f'Order is sent {symbol} {case} {right}')
            # save settings to config-xui-positions-runtime ...
            xui_position_defaults = app_config.get("xui_position_defaults")
            config_xui_order_runtime = config_utils.load_config(file_name=f'config-xui-positions-runtime.yaml', deep_merge=True)
            config_xui_order_runtime.setdefault(f"xui_positions_settings", {})[order_ref] = xui_position_defaults
            FileManager.save_yaml(config_xui_order_runtime, f"../configs/config-xui-positions-runtime.yaml")
        elif contract_type.lower() == 'future' and (can_buy or can_sell):
            right = 'long' if can_buy else 'short'
            side = 'long' if can_buy else 'short' # TODO need to be rmeoved ...


            contract_month =  app_config['symbols_meta'].get(symbol,{})['contract_month']
            contract = ib_contract.get_cached_contract(ib, symbol, contract_month)
            stop_loss_price = eval(app_config['symbols_meta'].get(symbol,{})[side.lower()]['stop_loss'])
            take_profit_price = eval(app_config['symbols_meta'].get(symbol,{})[side.lower()]['take_profit'])
            total_quantity = 1
            order_ref = ib_orders_async.generate_order_ref(application_state.get('portfolio_id'), event='OPEN', symbol=symbol, side='long', unique_run_number=application_state.get('unique_run_number'))

            candle_date = str(df['date'].iloc[-1])

            result_dic = await ib_orders_async.submit_linear_order_with_sl_tp(ib, side, contract, stop_loss_price, take_profit_price, total_quantity, order_ref, candle_date)

            data = {
                'symbol': symbol,
                'side': side,
                'right': right,
                'position_type': 'FUTURE',
                'starting_quantity':total_quantity,
                'available_quantity':total_quantity,
                'entry_underlying_price': underlying_price,
                'unique_run_number': application_state.get('unique_run_number'),
                'level_used_to_open': level_used,
                'level_name': '',
                'order_ref': order_ref,
            }
            data.update(result_dic)
            json_utils.print_map_pretty(data, msg = 'after MNQ order ')

            application_state.setdefault('open_trades_dic', {})[symbol] = data
            TradingLedger.add_to_list("signals", (symbol, f'ORDER_SENT',df['close'].iloc[-1],df['date'].iloc[-1], polish_map_to_show_in_hover(data)))
            TradingLedger.add_to_list("signals", (symbol, f'STOP_LOSS_SENT', stop_loss_price, df['date'].iloc[-1], polish_map_to_show_in_hover(data)))
            TradingLedger.add_to_list("signals", (symbol, f'TAKE_PROFIT_SENT', take_profit_price, df['date'].iloc[-1], polish_map_to_show_in_hover(data)))
            TradingLedger.add_to_dataframe("futures_order_history_df", data)
            add_to_number_of_positions_today(application_state, symbol)
            notification_helper.send_email(app_config, event='order_sent', symbol=symbol, body=polish_map_to_show_in_hover(data))
            add_order_ref_to_application_state(open_order_ref=order_ref)
            add_order_ref_to_application_state(open_order_ref=order_ref, close_order_ref=f'{order_ref}-TP')  #TODO need to be passed to the send order ...
            add_order_ref_to_application_state(open_order_ref=order_ref, close_order_ref=f'{order_ref}-SL')  #TODO need to be passed to the send order ...
            total_quantity, capital_data = calculate_number_of_future_contracts(symbol) # move it up ...
            add_to_capital_allocation_df(capital_data)
            add_open_order_to_capital_flow_df(data, capital_data)

    return




def number_of_positions_today(application_state, symbol):
    number_of_positions_today = application_state.get('number_of_trades', {}).get(date_utils.get_yyyymmdd(), {}).get(symbol, 0)
    return number_of_positions_today

def number_of_total_positions_today(application_state):
    """
    Returns total number of trades for a given trading_date
    across all symbols.
    "20260114": {
      "PLTR": 2,
      "TSLA": 1
    },
    """
    trading_date = date_utils.get_yyyymmdd()
    number_of_trades = application_state.get("number_of_trades", {})
    trades_for_day = number_of_trades.get(trading_date)
    if not trades_for_day:
        return 0
    else:
        return sum(trades_for_day.values())


def has_open_order_in_same_group(app_config, application_state, symbol):
    symbol_group = app_config['symbols_meta'].get(symbol,{}).get('group')
    if symbol_group is None: # no group ...
        return False
    open_orders = application_state.get('open_trades_dic', {})
    logger.info(f"@ has_open_order_in_same_group, symbol: {symbol}, symbol_group: {symbol_group}, open_orders: {open_orders}")
    for open_order_symbol, open_order_data in open_orders.items():
        if open_order_data.get('available_quantity', 0) == 0: # if there is no open quantity, skip
            continue
        open_order_symbol_group = app_config['symbols_meta'].get(open_order_symbol, {}).get('group', 'no-group')

        if open_order_symbol_group == symbol_group:
            logger.info(f"has_open_order_in_same_group, found open order in same group, symbol: {symbol}, open_order_symbol: {open_order_symbol}, group: {symbol_group}")
            return True
    return False


def check_xui_symbol_controls(app_config, application_state, symbol, right):
    # ###
    # xui_symbol_controls:
    #   symbols:
    #       AAPL:
    #           tp_enabled: true
    #           call_enabled: true
    #           put_enabled: true
    #
    if app_config.get('xui_symbol_controls', {}).get("trading_enabled", True) == False:
        return False
    if right == 'C' and app_config.get('xui_symbol_controls', {}).get("calls_enabled", True) == False:
        return False
    if right == 'P' and app_config.get('xui_symbol_controls', {}).get("puts_enabled", True) == False:
        return False
    right_enabled = "call_enabled" if right == 'C' else "put_enabled"
    if app_config.get('xui_symbol_controls', {}).get("symbols",{}).get(symbol,{}).get(right_enabled, True) == False:
        return False

    return True


def check_manual_conditions(app_config, application_state, symbol, right):

    try:
        eval_ctx = create_eval_ctx(application_state)
        application_state['eval_ctx'] = eval_ctx

        for condition in app_config.get('manual_settings', {}).get(right,{}).get('conditions', []):
            # evaluated_condition = eval(condition)
            evaluated_condition = eval(condition, {}, eval_ctx)

            if not evaluated_condition:
                logger.info(f"[check_manual_conditions] {symbol}, price: {eval_ctx.get(f'{symbol}_price')} QQQ_price: {eval_ctx.get(f'QQQ_price')}")
                logger.info(f"[check_manual_conditions] {symbol}, {right}, evaluated_condition: {evaluated_condition} , condition: {condition}")
                return False

        for condition in app_config.get('manual_settings', {}).get('symbols',{}).get(symbol, {}).get(right,[]):
            # evaluated_condition
            evaluated_condition = eval(condition, {}, eval_ctx)

            if not evaluated_condition:
                logger.info(f"[check_manual_conditions] {symbol}, price: {eval_ctx.get(f'{symbol}_price')} QQQ_price: {eval_ctx.get(f'QQQ_price')}")
                logger.info(f"[check_manual_conditions] {symbol}, {right}, evaluated_condition: {evaluated_condition} , condition: {condition}")
                return False

    except Exception as e:
        logger.error(f"[check_manual_conditions] @@@ TODO This is temp .... {e}")
        logger.error(f"[check_manual_conditions] @@@ TODO This is temp .... {traceback.format_exc()}")

    return True



def add_to_capital_allocation_df(application_state, data):
    # capital_allocation_df = pd.concat([capital_allocation_df, pd.DataFrame([data])])
    application_state.setdefault('capital_allocation', []).append(data)
    TradingLedger.add_to_dataframe("capital_allocation_df", data)
    return


async def send_order(ib, contract, side='long', total_quantity=1, order_ref=None, ib_account_id=None): #TODO move to utils ...

    await ib_orders_async.submit_option_order_prequalified_contract(ib, q_contract=contract, side=side, total_quantity=total_quantity, order_ref=order_ref, ib_account_id=ib_account_id)
    return


def polish_map_to_show_in_hover(data):
    logger.warning(f"@ {type(data)},  data: {data}, ")
    try:
        # return json.dumps(data).replace(',', ',<br>')
        return json.dumps(data, default=str).replace(',', ',<br>') # use str for .Object of type int64 is not JSON serializable error

    except Exception as e:
        logger.warning(f"[polish_map_to_show_in_hover] @@ we have paring issue ...{e}")
        return {}
    #


def add_to_number_of_positions_today(application_state, symbol):
    current_number = number_of_positions_today(application_state, symbol)
    if current_number == 0:
        application_state.setdefault('number_of_trades', {}).setdefault(application_state.get('trading_date'), {})[symbol] = 1
    else:
        application_state.setdefault('number_of_trades', {})[application_state.get('trading_date')][symbol] += 1
    return


def add_order_ref_to_application_state(application_state, open_order_ref='', close_order_ref=''):
    return
    if open_order_ref != '' and close_order_ref == '': # this is for open order ...
        application_state.setdefault('open_close_refs_map', {})[open_order_ref] = []
    elif open_order_ref != '' and close_order_ref != '': # this is for close ...
        entry = {open_order_ref: close_order_ref}
        application_state.setdefault('open_close_refs_list', []).append(entry) # add to the list ...

        if  not application_state.get('open_close_refs_map', {}).get(open_order_ref): #  opn is not there,sso add it ..
            application_state.setdefault('open_close_refs_map', {})[open_order_ref] = []

        application_state.get('open_close_refs_map', {}).get(open_order_ref).append(close_order_ref) # now add the close ...
    else:
        logger.info(f"[add_order_ref_to_application_state] @@@ add_order_ref_to_application_state, is not supported, open_order_ref: {open_order_ref}, close_order_ref: {close_order_ref}")
    data = {
        'open_order_ref': open_order_ref,
        'close_order_ref': close_order_ref,
        'ib_exec_id': ''
    }


    TradingLedger.add_to_dataframe("open_close_refs_df", data)

    return


def add_open_order_to_capital_flow_df(data, capital_data):
    try:
        d = {
            'timestamp': str(date_utils.time_now()),
            'trade_date' : date_utils.get_yyyymmdd(),
            'event': 'OPEN_ORDER',
            'capital_before_event': 0,
            'cash_flow': -1 * capital_data.get('capital_used'),
            'capital_after_event': 0,
            'realized_pnl': 0,
            'commission': 0,
            'trade_cost': capital_data.get('capital_used'),
            'is_closed': 'NO',
            'symbol': data.get('symbol'),
            'unique_run_number': data.get('unique_run_number'),
            'open_order_ref': data.get('order_ref'),
            'memo': 'Order opened.'
        }
        logger.info(f"add_open_order_to_capital_flow_df, data: {d}")
        TradingLedger.add_to_dataframe("capital_flow_df", d)
    except Exception as e:
    # TODO add
        logger.error(f"[add_open_order_to_capital_flow_df] @@@@ add_open_order_to_capital_flow_df e")
        logger.error(traceback.format_exc())

    return



def calculate_number_of_future_contracts(app_config, application_state, symbol):

    available_capital = risk_helper.calcualte_availale_capital(app_config, application_state)

    capital_per_trade_percentage = app_config['positioning'].get('capital_per_trade_percentage', 0.5)

    logger.info(f"calculate_number_of_future_contracts(), {symbol}, available_capital: {available_capital}, capital_per_trade_percentage: {capital_per_trade_percentage}")

    capital_per_trade = max(available_capital * capital_per_trade_percentage, 800)  # TODO put in a function
    num_of_contracts = 1

    logger.info(f"capital_per_trade: {capital_per_trade}")
    logger.info(f"symbol: {symbol}, num_of_contracts: {num_of_contracts}")
    if num_of_contracts == 0:
        logger.warning(f"[calculate_number_of_future_contracts] @@@@ we don't have enough capital ...")
    capital_used = num_of_contracts * 2500
    capital_remaining_after_order = available_capital - capital_used
    open_trades_count_at_entry = position_helper.calculate_number_of_open_positions(application_state)
    # update ...
    application_state.get('risk')['available_capital'] = capital_remaining_after_order

    data = {
            'timestamp': str(date_utils.time_now()),
            'trade_date': date_utils.get_yyyymmdd(),
            'symbol': symbol,
            'unique_run_number': application_state.get('unique_run_number'),
            'starting_capital': available_capital,
            'capital_used': capital_used,
            'capital_remaining_after_order': capital_remaining_after_order,
            'allowed_capital_per_trade': capital_per_trade,
            'strike': 0,
            'ask': 0,
            'num_of_contracts': num_of_contracts,
            'daily_loss_so_far': 0,
            'daily_win_so_far': 0,
            'open_trades_count_at_entry': open_trades_count_at_entry,
            'memo': '',
            }
    return num_of_contracts, data




def create_eval_ctx(application_state):
    eval_ctx = {}
    levels = application_state.get('levels', {})

    for symbol, lvl_map in levels.items():
        for name, value in lvl_map.items():
            eval_ctx[
                f"{symbol}_{name}"] = value  # {'QQQ_PDH': 625.52, 'QQQ_PDL': 623.14, 'QQQ_5MH': 620.57, 'QQQ_5ML': 619.11, 'QQQ_PMH': 624.36, 'QQQ_PML': 618.92}

    for key, val in application_state.get('latest_prices', {}).items():
        eval_ctx[f"{key}_price"] = val


    return eval_ctx

def number_of_wins(application_state, symbol):
    trading_date = application_state.get('trading_date')
    number_of_wins = application_state.get('number_of_wins', {}).get(trading_date, 0)
    return number_of_wins


def context_filter(application_state, details_map, df, unique_run_number, symbol, right, level_used):
    try:
        from utils.context_filter import run_context_filter_for_order

        result = run_context_filter_for_order(
            application_state, details_map, df, symbol, right, level_used
        )
        if result is None:
            return
        logger.info(f"[context_filter] result: {result}")
        result_dict = result.__dict__
        logger.info(f"[context_filter] result_dict: {result_dict}")
        FileManager.save_named_json(
            result_dict,
            file_name=f"context_filter-{unique_run_number}-{symbol}.json",
            dir="default",
        )
    except Exception as e:
        logger.error(f"[context_filter] @@@ error: {traceback.format_exc()}")
        application_state_router.add_audit_message(application_state, str(e))

