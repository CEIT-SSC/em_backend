import time
import random
import smtplib
import socket
import concurrent.futures
from django.core.mail import EmailMultiAlternatives, get_connection
from django.conf import settings
import logging


logger = logging.getLogger(__name__)
thread_pool_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

TRANSIENT_EXCEPTIONS = (
    smtplib.SMTPServerDisconnected,
    smtplib.SMTPConnectError,
    smtplib.SMTPHeloError,
    smtplib.SMTPDataError,
    smtplib.SMTPRecipientsRefused,
    smtplib.SMTPResponseException,
    socket.timeout,
    ConnectionResetError,
    OSError,
)


def _send_email_blocking(
    subject,
    recipient_list,
    text_content,
    html_content=None,
    max_attempts: int = 4,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
):
    if not recipient_list:
        logger.warning("No recipients provided for subject '%s'", subject)
        return False

    from_email = settings.DEFAULT_FROM_EMAIL
    attempt = 0
    delay = initial_delay

    last_exc = None

    while attempt < max_attempts:
        attempt += 1
        try:
            msg = EmailMultiAlternatives(subject, text_content, from_email, recipient_list)
            if html_content:
                msg.attach_alternative(html_content, "text/html")

            connection = get_connection()
            sent_count = None
            try:
                connection.open()
                sent_count = connection.send_messages([msg])
            finally:
                try:
                    connection.close()
                except Exception:
                    logger.debug("Exception while closing SMTP connection", exc_info=True)

            if sent_count:
                logger.info("Email successfully sent to %s recipients (subject=%s) on attempt %d",
                            len(recipient_list), subject, attempt)
                return True

            logger.warning("send_messages returned 0 for subject %s on attempt %d", subject, attempt)
            last_exc = RuntimeError("send_messages returned 0")

        except smtplib.SMTPAuthenticationError as e:
            logger.error("SMTP authentication failed for sending email to %s (subject=%s): %s",
                         recipient_list, subject, e, exc_info=True)
            return False
        except TRANSIENT_EXCEPTIONS as e:
            last_exc = e
            logger.warning("Transient SMTP error on attempt %d for subject %s: %s",
                           attempt, subject, e, exc_info=True)
        except Exception as e:
            logger.exception("Unexpected error while sending email to %s (subject=%s) on attempt %d",
                             recipient_list, subject, attempt, e)
            return False

        if attempt >= max_attempts:
            break

        sleep_time = delay + random.uniform(0, delay * 0.5)
        logger.info("Retrying email (subject=%s) in %.1f sec (attempt %d/%d)",
                    subject, sleep_time, attempt + 1, max_attempts)
        time.sleep(sleep_time)
        delay *= backoff_factor

    logger.error(
        "All %d attempts to send email with subject '%s' failed. Last exception: %s",
        max_attempts, subject, repr(last_exc),
        exc_info=True
    )
    return False


def send_email_async_task(subject, recipient_list, text_content, html_content=None):
    if not recipient_list:
        logger.warning("No recipients provided for subject '%s'", subject)
        return

    thread_pool_executor.submit(
        _send_email_blocking,
        subject,
        recipient_list,
        text_content,
        html_content,
        3,
        1.0,
        2.0
    )

    logger.info(
        "Email task for subject '%s' submitted to thread pool for %d recipients.",
        subject, len(recipient_list)
    )
