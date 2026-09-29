import logging
logger = logging.getLogger(__name__)
from trading_utils import email_utils

def send_email(app_config, event='order_sent', symbol='', subject='', body=''):
    if app_config['notification'].get('send_on_trades'):
        recipients = app_config['notification']['recipients']

        if event.lower() == 'order_sent':
            subject = f"Order Sent {symbol} - {app_config['user_name']}"
            body = (f"Order Sent ... <br> {body}"
                    f"<br>Later more detail will come ...<br>")

        elif event.lower() == 'stop_loss_sent':
            subject = f"Stop Loss {symbol} - {app_config['user_name']}"
            body = (f"Stop Loss Sent ... <br> {body}"
                    f"<br>Later more detail will come ...<br>")

        elif event.lower() == 'take_profit_sent':
            subject = f"Take Profit {symbol} - {app_config['user_name']}"
            body = (f"Take Profit Sent ... <br> {body}"
                    f"<br>Later more detail will come ...<br>")

        logger.info(f"[send_email], recipients: {recipients}, subject: {subject}")
        email_utils.send_email(to_emails=recipients, subject=subject, body=body)

    return