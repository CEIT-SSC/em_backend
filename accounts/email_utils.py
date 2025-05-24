import concurrent.futures
from django.core.mail import send_mail
from django.conf import settings
import logging

logger = logging.getLogger(__name__)
thread_pool_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)


def _send_email_blocking(subject, message, recipient_list):
    from_email = settings.DEFAULT_FROM_EMAIL

    try:
        send_mail(
            subject,
            message,
            from_email,
            recipient_list,
            fail_silently=False,
        )
        logger.info(f"Email successfully sent to {recipient_list} with subject: {subject}")
        return True
    except Exception as e:
        logger.error(f"Error sending email to {recipient_list} with subject '{subject}': {e}", exc_info=True)
        return False


def send_email_async_task(subject, message, recipient_list):
    if not recipient_list:
        logger.warning(f"No recipients provided for email with subject: {subject}")
        return

    thread_pool_executor.submit(
        _send_email_blocking,
        subject,
        message,
        recipient_list
    )

    logger.info(f"Email task for subject '{subject}' submitted to thread pool for recipients: {recipient_list}")
