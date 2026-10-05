import datetime
import logging
from trading_utils import email_utils
import datetime

logger = logging.getLogger(__name__)
import sys
sys.path.insert(0, f'../')


def check_and_send_alarms(app_config, application_state, market_data):
    # Implement the logic for checking and sending alarms here

    if app_config.get("alarms", {}).get("enabled", "False") == False:
        return

    if eval(app_config.get("alarms", {}).get("condition", "1 == 1")) == False:
        return
    user_name = app_config.get("user_name", "unknown_user")
    for symbol in app_config.get("symbols", []):
        logger.info(f"[check_and_send_alarms] {symbol}")
        price = application_state.get("latest_prices", {}).get(symbol) # used in config
        df = market_data.dfs_map.get(symbol) # used in config
        if price is None:
            continue
        logger.info(f"[check_and_send_alarms] {symbol} : {price}")
        for alarm_detail in app_config.get("alarms", {}).get("details", []):
            logger.info(f"[check_and_send_alarms] Evaluating alarm condition for {symbol}: {alarm_detail}")
            if  eval(alarm_detail["condition"]):

                if application_state.get("alarms", {}).get(symbol, {}).get(f"crossed_{alarm_detail['level']}", False) == False :
                    # condition is triggered and was flase in previous run
                    logger.info(f"[check_and_send_alarms] Alarm condition triggered for {symbol}: {alarm_detail}")

                    alarm_state = application_state.setdefault("alarms", {}).setdefault(symbol, {})
                    last_alarm_date = alarm_state.get(f"crossed_{alarm_detail['level']}_alarm_date")
                    now = datetime.datetime.now()

                    should_send_email = True
                    if last_alarm_date:
                        last_alarm_dt = datetime.datetime.fromisoformat(last_alarm_date)
                        should_send_email = (now - last_alarm_dt) >= datetime.timedelta(minutes=app_config.get("alarms", {}).get("email_interval_minutes", 3))

                    if should_send_email:
                        logger.info(f"[check_and_send_alarms] Sending email for {symbol}: {alarm_detail}")
                        recipients = app_config.get('alarms', {}).get('recipients', '')
                        subject = app_config.get('alarms', {}).get('subject', f"Alarm - {symbol} - {alarm_detail['level']} - {user_name}")
                        body = (f"Alarm triggered for {symbol}: price {price} crossed <br> {alarm_detail['level']} level <br> "
                                f"{alarm_detail['condition']} <br> Time: {now.isoformat()} <br> User: {user_name}")

                        email_utils.send_email(to_emails=recipients, subject=subject, body=body)
                        alarm_state[f"crossed_{alarm_detail['level']}_alarm_date"] = str(now.isoformat())

                application_state.setdefault("alarms", {}).setdefault(symbol, {})[f"crossed_{alarm_detail['level']}"] = True

            else:
                application_state.setdefault("alarms", {}).setdefault(symbol, {})[f"crossed_{alarm_detail['level']}"] = False
