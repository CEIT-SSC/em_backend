import concurrent.futures
from django.core.mail import EmailMultiAlternatives
from django.conf import settings
import logging

logger = logging.getLogger(__name__)
thread_pool_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)


def _send_email_blocking(subject, recipient_list, text_content, html_content=None):
    from_email = settings.DEFAULT_FROM_EMAIL

    try:
        msg = EmailMultiAlternatives(subject, text_content, from_email, recipient_list)
        if html_content:
            msg.attach_alternative(html_content, "text/html")

        msg.send()

        logger.info(f"Email successfully sent to {len(recipient_list)} recipients with subject: {subject}")
        return True
    except Exception as e:
        logger.error(f"Error sending email to {recipient_list} with subject '{subject}': {e}", exc_info=True)
        return False


def send_email_async_task(subject, recipient_list, text_content, html_content=None):
    if not recipient_list:
        logger.warning(f"No recipients provided for email with subject: {subject}")
        return

    thread_pool_executor.submit(
        _send_email_blocking,
        subject,
        recipient_list,
        text_content,
        html_content
    )

    logger.info(f"Email task for subject '{subject}' submitted to thread pool for {len(recipient_list)} recipients.")
