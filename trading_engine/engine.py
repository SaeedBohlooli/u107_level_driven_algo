from trading_core.trading_ledger import TradingLedger
from trading_utils import *
import logging
logger = logging.getLogger(__name__)
import sys
sys.path.insert(0, f'../')

from trading_core.ws_server import WSServer
from trading_core.file_manager import FileManager
from trading_core.streamers.state_streamer import StateStreamer
from trading_core.streamers.config_streamer import ConfigStreamer
from trading_core.streamers.open_trades_streamer import OpenTradesStreamer
from trading_core.streamers.contract_strikes_streamer import ContractStrikesStreamer
from trading_core.streamers.quote_cache_streamer import QuoteCacheStreamer
from trading_core.ib_connector import IBConnector
from trading_core.market_data_store import MarketDataStore
from trading_core import market_session_guard
from trading_core import application_state_router
from trading_core import trading_ledger
from trading_core import ib_heartbeat_loop
from trading_core import engine_cycle
from trading_core import user_request_loop

from trading_engine import marketdata_helper
from trading_engine import inidicators
from trading_engine import strategy
from trading_engine import application_state_helper
from trading_engine import scanner
from trading_engine import pricing_helper
from trading_engine import options_helper
from trading_engine import order_helper
from trading_engine import exit_conditions
from trading_engine import chart_helper
from trading_engine import position_helper
from trading_engine import pnl_helper
from trading_engine import user_request_helper
from trading_engine import rs_scanner
from trading_engine import alarms_helper

from trading_utils import position_router
from trading_utils import user_request_router
from trading_utils import ib_account
from trading_utils import ib_pricing_async
from trading_utils import json_utils


from utils import atr_tolerance_helper

class TradingEngine:

    def __init__(self, boot):
        self.boot = boot
        self.logger = boot.logger
        self.app_config = boot.app_config
        self.application_state = boot.application_state
        self.ws = WSServer(host="0.0.0.0", port=self.app_config.get('ws_port', 6666))
        self.runtime = boot.runtime
        self.market_data = MarketDataStore()


    async def do_miscs(self, ib, app_config, application_state, interval_seconds=60):
        while True:
            try:
                if engine_cycle.should_exit(application_state=application_state):
                    logger.info("[do_miscs] Exiting as requested.")
                    break
                logger.info(f"[do_miscs]  ...")
                application_state_router.populate_global_state(application_state=self.application_state)

                if self.runtime.is_due("populate_ib_account_info", interval_sec=60 * 1):
                    await populate_ib_account_info(ib, application_state, app_config.get("ib_account_id", ""))
                    if self.application_state.get("global_state.subscribed_symbols_count") > 80:
                        application_state_router.add_audit_message(application_state, f"Subscribed symbols count is  which is quite high. sub: {self.application_state.get('global_state.subscribed_symbols_count')} q: {self.application_state['global_state.quote_cache_count']} ")

                ib_pricing_async.cleanup_stale_quotes()
                if self.runtime.should_run_once("save_configs_in_chart_folder", min_time_hhmm=1015):
                    chart_helper.save_configs_in_chart_folder(self.app_config)

                await asyncio.sleep(interval_seconds)
            except Exception as e:
                logger.warning(f"[do_miscs] @@@ Unexpected error in do_miscs: {e}")
                logger.error(f"[do_miscs] @@@ error: {traceback.format_exc()}" )
                await asyncio.sleep(interval_seconds)

    async def engine_loop(self, ib):
        run_number = 0
        await application_state_helper.initialize_application_state(ib, self.app_config, self.application_state)

        hover_df_cols = ['symbol', 'time_frame', 'object', 'color', 'date_1', 'price_1', 'date_2', 'price_2', 'memo', 'unique_id']
        TradingLedger.set_dataframe_columns("hover_df", hover_df_cols)

        key_levels_cols = ['symbol', 'time_frame', 'key_level', 'price', 'memo', 'unique_id']
        TradingLedger.set_dataframe_columns("key_levels_df", key_levels_cols)

        capital_flow_df = FileManager.load_my_df("capital_flow_df")
        if len(capital_flow_df) ==0:
            capital_flow_cols = ['timestamp', 'trade_date', 'event', 'capital_before_event', 'cash_flow',
                                 'capital_after_event', 'realized_pnl',
                                 'commission', 'trade_cost', 'is_closed', 'symbol', 'unique_run_number', 'open_order_ref',
                                 'close_order_ref', 'proccesed', 'memo']
            TradingLedger.set_dataframe_columns("capital_flow_df", capital_flow_cols)
        else:
            TradingLedger.set_dataframe("capital_flow_df", capital_flow_df)

        open_close_refs_df = FileManager.load_my_df('open_close_refs_df')
        TradingLedger.set_dataframe("open_close_refs_df", open_close_refs_df)

        open_close_refs_pnl_df = FileManager.load_my_df("open_close_refs_pnl_df")
        TradingLedger.set_dataframe("open_close_refs_pnl_df", open_close_refs_pnl_df)

        while True:
            try:
                start_time = time.time()
                run_number += 1
                current_hh_mm_ny = self.runtime.now_hhmm()
                self.application_state["current_hh_mm_ny"] = current_hh_mm_ny
                unique_run_number_X =  self.runtime.generate_unique_run_number(run_number)
                day_of_week = self.runtime.now_day_of_week()
                symbol_number = 0
                logger.info(f"[engine] ==================== run_number: {run_number}, unique_run_number_X: {unique_run_number_X}")
                self.runtime.reload_runtime_config()
                application_state_helper.initialize_application_state_for_run(self.app_config, self.application_state)

                if engine_cycle.should_exit(application_state=self.application_state):
                    logger.info("[market_session_guard_loop] Exiting as requested.")
                    break

                if ib is None:
                    logger.warning("[engine] ib is None... so give a try to reconnect ...")
                    await asyncio.sleep(3)
                    continue

                self.app_config = self.runtime.reload_config()
                if self.app_config.get('user_name') == 'Saeed-PC19' and self.runtime.is_due('PRINT_CONFIG', interval_sec=20) :
                    logger.info(f"[engine] app_config: {json_utils.print_map_pretty(self.app_config)}")

                if self.runtime.is_due('ORCHESTRATE_EXPIRATIONS_STRIKES', interval_sec=60):
                    await options_helper.orchestrate_expirations_strikes(ib, self.app_config, self.application_state, self.market_data)

                if self.runtime.should_run_once("SUBSCRIBE_FOR_CURRENT_PRICE"): # TODO need to be based on symbol.
                    await pricing_helper.subscribe_for_current_price(ib, self.app_config, self.application_state)

                if self.runtime.is_due("SUBSCRIBE_MARKET_DATA_FOR_ALL_OTM_OPTION_CONTRACTS", interval_sec=60, min_time_hhmm=930):
                    await options_helper.prepare_contracts_for_all_otm_option_contracts(ib, self.app_config, self.application_state, self.market_data)
                    await options_helper.subscribe_market_data_for_all_otm_option_contracts(ib, self.app_config, self.application_state, self.market_data)
                    await options_helper.unsubscribe_market_data_for_itm_option_contracts(ib)
                    await options_helper.unsubscribe_excessively_distant_option_contracts(ib, self.application_state)
                    await options_helper.unsubscribe_for_symbols(ib, self.app_config, self.application_state)

                if False and not self.application_state['is_busy_time'] and self.runtime.is_due('DO_PNL', interval_sec=5*60): # TODO should be not busy_time?!
                    pnl_helper.populate_open_close_refs_pnl_df()
                    pnl_helper.populate_close_orders_in_capital_flow_df()
                    pnl_helper.check_open_orders_in_capital_flow_df(self.application_state)
                    pnl_helper.recompute_capital_flow_df(4000)

                    # We need to write to disk with mode= 'w' as we changing some records.
                    capital_flow_df = TradingLedger.get_dataframe('capital_flow_df')
                    FileManager.save_my_df(capital_flow_df, "capital_flow_df", mode='w', drop_duplicates=True, save_tabular=True)


                for symbol in self.app_config.get('symbols'):
                    symbol_number += 1
                    unique_run_number = f'{unique_run_number_X}-{symbol_number}'
                    self.application_state['unique_run_number'] = unique_run_number
                    logger.info(f"[engine] ------------------- {symbol}, {unique_run_number}, {current_hh_mm_ny} ")
                    symbol_start_time = time.time()

                    application_state_helper.initialize_application_state_for_symbol_run(self.app_config, self.application_state)


                    if self.runtime.is_due(f'SUBSCRIBE_PRICE-{symbol}', interval_sec=60*2):
                        current_price = await ib_pricing_async.get_or_subscribe_symbol_price(ib, symbol, contract_month=self.app_config.get('symbols_meta', {}).get(symbol,{}).get('contract_month'))
                        if current_price is None:
                            current_price = -1.0
                        self.application_state.setdefault('latest_prices', {})[symbol] = current_price

                    logger.info(f"[engine] Starting get_historical_data for {symbol}")
                    df = await marketdata_helper.get_historical_data(ib, symbol, self.app_config, self.application_state, time_frame='1m', historical_days='3 D')
                    logger.info(f"[engine] Finished get_historical_data for {symbol}")
                    if df is None or len(df) ==0:
                        logger.warning(f"[engine] @@@@@ {symbol}, no data found, skip the symbol for now ...")
                        continue

                    df = inidicators.populate_features(self.app_config, df)
                    self.market_data.dfs_map[symbol] = df
                    if self.application_state['is_save_time']:
                        logger.info(f"[engine] {symbol}, df: \n{df[-4:].to_markdown()}")

                    # qqq_df = self.market_data.dfs_map.get('QQQ')
                    # relative_strength_df = inidicators.compute_relative_strength(df, qqq_df, period=20) # TODO do we need this
                    # intraday_rs_df = inidicators.compute_intraday_rs(df, qqq_df)

                    if self.runtime.should_run_once(f'DYNAMIC-TOLERANCE-CALCULATION-{symbol}-{str(df["date"].iloc[-1])}'): # telrance for last closed candle
                        dynamic_tolerance_map = atr_tolerance_helper.get_dynamic_tolerance(df[:-1].copy(), level=0, min_tick=0.01)  # Drop -1 as it fluctuates and SL triggers ...
                        dynamic_tolerance_map['timestamp'] = str(df['date'].iloc[-1])
                        self.market_data.data_store.setdefault(symbol, {})['dynamic_tolerance'] = dynamic_tolerance_map
                        self.application_state.setdefault('dynamic_tolerances', {})[symbol] = dynamic_tolerance_map

                    # Levels
                    if not strategy.all_levels_in(self.application_state, symbol, ['PDH']):
                        strategy.calculate_PDL_PDH(self.application_state, symbol, df, day_of_week)

                    if not strategy.all_levels_in(self.application_state, symbol, ['5MH']):  # if not in, recalculate ...
                        strategy.find_add_5MH_5ML(self.application_state, df, symbol)

                    if not strategy.all_levels_in(self.application_state, symbol, ['PMH', 'PML']):  # if not in, recalculate ...
                        strategy.find_add_PMH_PML(self.application_state, df, symbol)

                    df = strategy.compute_indicators(self.app_config,self.application_state, symbol, df)
                    are_all_levels_in = strategy.all_levels_in(self.application_state, symbol)

                    # once per candle per symbol ...
                    if self.runtime.should_run_once(f'{symbol}-CANDLE-{str(df["date"].iloc[-1])}'):
                        chart_helper.mark_tolerance_to_the_level(self.app_config, self.application_state, symbol, '5ML', self.market_data)
                        chart_helper.mark_tolerance_to_the_level(self.app_config, self.application_state, symbol, '5MH', self.market_data)

                        chart_helper.mark_atr_to_the_level(self.application_state, symbol, 'up', '5MH', self.market_data)
                        chart_helper.mark_atr_to_the_level(self.application_state, symbol, 'down', '5ML', self.market_data)

                        chart_helper.add_atr_to_candle_info(symbol, self.market_data)

                        # chart_helper.add_rs_relative_to_candle_info(symbol, intraday_rs_df, self.market_data)
                        chart_helper.add_open_position_to_candle_info(self.application_state, symbol, self.market_data)


                    if are_all_levels_in and self.runtime.should_run_once(f'{symbol}-CLOSED-LEVELS-MARKED'):  # we have all elvels, so mark them ...
                        chart_helper.mark_close_levels(self.app_config, self.application_state, symbol, df)

                    logger.debug(f"[engine] After levels {symbol}, df: \n{df[-4:].to_markdown()}")

                    buy_sell_case_results_list = scanner.check_buy_and_sell_cases(ib, self.app_config, self.application_state, symbol, self.market_data)
                    buy_sell_case_results_list = order_helper.add_case_manual_order_to_buy_sell_case_results_list(self.application_state, buy_sell_case_results_list, self.market_data)

                    await order_helper.check_buy_sell_result_to_send_order(ib, self.app_config, self.application_state, buy_sell_case_results_list, df, self.market_data, self.runtime)

                    await exit_conditions.check_for_stop_loss_and_take_profit(ib, self.app_config, self.application_state, self.market_data)

                    if self.application_state['is_save_time'] and 931 < current_hh_mm_ny and self.runtime.should_run_once(f'{symbol}-MARK_GAP'):
                        chart_helper.detect_a_mark_market_gap(self.application_state, symbol, df)  # need to happen one time after 9:30

                    chart_helper.add_buy_a_sell_entries_to_signals(self.app_config, self.application_state, buy_sell_case_results_list, self.market_data)

                    position_helper.update_position_for_entry_execution_price(self.application_state)

                    self.application_state["eval_ctx"] = order_helper.create_eval_ctx(self.application_state)

                    symbol_end_time = time.time()
                    symbol_run_spend_time = round(symbol_end_time - symbol_start_time, 2)
                    logger.info(f'[engine] ------------------- {symbol}, {unique_run_number}, symbol_run_spend_time: {symbol_run_spend_time} seconds')
                    self.application_state.setdefault("run_times", {})[symbol] = symbol_run_spend_time


                    # end while for symbols

                # RS Ranking — runs once per minute after all symbols have fresh data + levels
                if self.runtime.is_due('RS_RANKING', interval_sec=20):
                    rs_scanner.sort_symbols_based_on_rs(self.app_config, self.application_state, self.market_data)

                if (position_helper.calculate_number_of_open_positions(self.application_state) > 0 or  # either is open positions or ...
                        self.runtime.is_due(f'UPDATE-IB-POSITIONS', interval_sec=3*60)):
                    position_router.update_application_state_for_ib_positions(ib, self.application_state)

                if self.runtime.is_due('SAVE-SIGNALS', interval_sec=3 * 60):
                    chart_helper.add_candle_info_df_to_signals()
                    chart_helper.convert_signals_to_hover_df()

                if self.runtime.is_due('SAVE-TRADING_LEDGER-STATS', interval_sec=5*60):
                    self.application_state['TradingLedger.get_all_dataframe_stats'] =TradingLedger.get_all_dataframe_stats()
                    self.application_state['TradingLedger.get_all_list_stats'] =TradingLedger.get_all_list_stats()

                if self.application_state['is_save_time'] and self.runtime.is_due('SAVE_OHLC',interval_sec=1*60):
                    marketdata_helper.save_ohlc_for_chart(self.application_state, self.market_data, save_tabular=False)

                alarms_helper.check_and_send_alarms(self.app_config, self.application_state, self.market_data)

                end_time = time.time()
                run_time_spent = round(end_time - start_time, 2)
                logger.info(f"[engine] ==================== unique_run_number: {unique_run_number}, run_spent_time: {run_time_spent} seconds, sleep ... {self.app_config['interval_seconds']['engine_loop']}")
                self.application_state.setdefault("run_times", {})['engine_loop_run_time_spent'] = run_time_spent

            except Exception as e:
                logger.warning(f"[engine] @@@ Unexpected error in engine_loop: {e}")
                logger.error(f"[engine] @@@ error: {traceback.format_exc()}" )
                application_state_router.add_audit_message(self.application_state, str(e))

            await asyncio.sleep(self.app_config['interval_seconds']['engine_loop'])


    async def run(self):
        logger.info("[engine] Starting Trading Engine")
        ib = await IBConnector.connect_from_config(self.app_config)

        ws_server = await self.ws.start()

        # Keep existing application_state cadence unchanged unless explicitly configured.
        state_interval_sec = self.app_config.get("interval_seconds", {}).get("application_state_streamer", 2)
        state_streamer = StateStreamer(self.app_config, self.application_state, self.ws, interval_sec=state_interval_sec)
        config_streamer = ConfigStreamer(self.app_config, self.application_state, self.ws, interval_sec=60)
        open_trades_interval_sec = self.app_config.get("interval_seconds", {}).get("open_trades_streamer", 1)
        contract_strikes_interval_sec = self.app_config.get("interval_seconds", {}).get("contract_strikes_streamer", 1)
        quote_cache_interval_sec = self.app_config.get("interval_seconds", {}).get("quote_cache_streamer", 1)

        open_trades_streamer = OpenTradesStreamer(
            self.app_config,
            self.application_state,
            self.ws,
            interval_sec=open_trades_interval_sec,
        )
        contract_strikes_streamer = ContractStrikesStreamer(
            self.app_config,
            self.application_state,
            self.ws,
            interval_sec=contract_strikes_interval_sec,
        )
        quote_cache_streamer = QuoteCacheStreamer(
            self.app_config,
            self.application_state,
            self.ws,
            interval_sec=quote_cache_interval_sec,
        )

        self.logger.info("[engine] WebSocket server is starting...")

        await asyncio.gather(
            ws_server,
            state_streamer.run(),
            config_streamer.run(),
            open_trades_streamer.run(),
            contract_strikes_streamer.run(),
            quote_cache_streamer.run(),
            self.engine_loop(ib),
            self.do_miscs(ib, self.app_config, self.application_state, interval_seconds=60),
            self.boot.data_saver_manager.run(ib, interval_sec=60),
            user_request_loop.fetch_user_request_loop(self.app_config, self.application_state, interval_sec=1),
            user_request_loop.process_common_user_request_loop(ib, self.app_config, self.application_state,interval_sec=2),
            user_request_helper.process_app_user_request_loop(ib, self.app_config, self.application_state,interval_sec=1),

            ib_heartbeat_loop.ib_heartbeat_loop(ib, app_config=self.app_config,application_state=self.application_state, interval_seconds=60),
            # market_session_guard.market_session_guard_loop(ib, self.application_state, self.runtime, interval_sec=600)
        )

