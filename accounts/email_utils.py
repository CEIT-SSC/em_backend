import concurrent.futures
import logging
import random
import smtplib
import socket
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from ssl import create_default_context

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection

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


def _send_liara_email(
    subject,
    recipient_list,
    text_content,
    html_content=None,
    timeout=15,
):
    """Directly sends via Liara using port 465 (SSL) or 587 (STARTTLS)."""
    liara_host = getattr(settings, "MAIL_HOST", "smtp.c1.liara.email")
    liara_port = int(getattr(settings, "MAIL_PORT", 465))
    liara_user = getattr(settings, "MAIL_USER", None)
    liara_password = getattr(settings, "MAIL_PASSWORD", None)
    liara_from_address = getattr(
        settings, "MAIL_FROM_ADDRESS", "service@ceit-ssc.ir"
    )
    liara_from_name = getattr(settings, "MAIL_FROM_NAME", "CEIT SSC")

    if not liara_user or not liara_password:
        raise ValueError("Liara MAIL_USER or MAIL_PASSWORD is not set.")

    context = create_default_context()

    # Build the MIME message
    def create_mime_message(to_email):
        msg = MIMEMultipart("alternative")
        msg["From"] = f"{liara_from_name} <{liara_from_address}>"
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.add_header("x-liara-tag", "transactional")
        msg.attach(MIMEText(text_content, "plain", "utf-8"))
        if html_content:
            msg.attach(MIMEText(html_content, "html", "utf-8"))
        return msg

    # Port 465 -> Implicit SSL
    if liara_port == 465:
        with smtplib.SMTP_SSL(
            liara_host, liara_port, context=context, timeout=timeout
        ) as server:
            server.login(liara_user, liara_password)
            for recipient in recipient_list:
                msg = create_mime_message(recipient)
                server.sendmail(
                    liara_from_address, recipient, msg.as_string()
                )

    # Port 587 -> Explicit TLS (STARTTLS)
    else:
        with smtplib.SMTP(liara_host, liara_port, timeout=timeout) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(liara_user, liara_password)
            for recipient in recipient_list:
                msg = create_mime_message(recipient)
                server.sendmail(
                    liara_from_address, recipient, msg.as_string()
                )

    return True


def _send_email_blocking(
    subject,
    recipient_list,
    text_content,
    html_content=None,
    max_attempts_per_provider: int = 2,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
):
    if not recipient_list:
        logger.warning("No recipients provided for subject '%s'", subject)
        return False

    # 1. Primary Attempt: Liara Service
    try:
        logger.info(
            "Attempting to send email via primary provider (Liara) to %d recipient(s).",
            len(recipient_list),
        )
        _send_liara_email(
            subject, recipient_list, text_content, html_content
        )
        logger.info(
            "Email successfully sent via Liara to %d recipients (subject='%s')",
            len(recipient_list),
            subject,
        )
        return True
    except Exception as e:
        logger.error(
            "Failed to send email via primary provider (Liara). Exception: %s. Falling back to Gmail options.",
            e,
            exc_info=True,
        )

    # 2. Fallback: Gmail / Secondary Providers
    email_providers = getattr(settings, "EMAIL_PROVIDERS", [])
    if not email_providers:
        logger.error(
            "No EMAIL_PROVIDERS configured in settings.py (Fallback failed)"
        )
        return False

    for i, provider in enumerate(email_providers):
        from_email = provider.get("user")
        if not from_email:
            logger.warning(
                "Skipping email provider #%d due to missing 'user' config.",
                i + 1,
            )
            continue

        logger.info(
            "Attempting to send email via fallback provider: %s", from_email
        )
        attempt = 0
        delay = initial_delay
        last_exc = None

        while attempt < max_attempts_per_provider:
            attempt += 1
            try:
                connection = get_connection(
                    backend=settings.EMAIL_BACKEND,
                    host=provider.get("host"),
                    port=int(provider.get("port", 587)),
                    username=provider.get("user"),
                    password=provider.get("password"),
                    use_tls=provider.get("use_tls", True),
                    fail_silently=False,
                )

                msg = EmailMultiAlternatives(
                    subject=subject,
                    body=text_content,
                    from_email=from_email,
                    to=recipient_list,
                    connection=connection,
                )
                if html_content:
                    msg.attach_alternative(html_content, "text/html")

                sent_count = msg.send()
                if sent_count:
                    logger.info(
                        "Email successfully sent via %s to %d recipients (subject='%s')",
                        from_email,
                        len(recipient_list),
                        subject,
                    )
                    return True

            except smtplib.SMTPAuthenticationError as e:
                logger.error(
                    "SMTP authentication failed for provider %s. Trying next provider.",
                    from_email,
                    exc_info=True,
                )
                last_exc = e
                break

            except TRANSIENT_EXCEPTIONS as e:
                last_exc = e
                logger.warning(
                    "Transient SMTP error on attempt %d for provider %s: %s",
                    attempt,
                    from_email,
                    e,
                )

            except Exception as e:
                logger.exception(
                    "Unexpected error with provider %s. Trying next provider.",
                    from_email,
                )
                last_exc = e
                break

            if attempt >= max_attempts_per_provider:
                break

            sleep_time = delay + random.uniform(0, delay * 0.5)
            time.sleep(sleep_time)
            delay *= backoff_factor

        logger.error(
            "Failed to send email with provider %s after %d attempts. Last exception: %s",
            from_email,
            attempt,
            repr(last_exc),
        )

    logger.error(
        "All configured email providers (Primary & Fallbacks) failed for subject '%s'",
        subject,
    )
    return False


def send_email_async_task(
    subject, recipient_list, text_content, html_content=None
):
    if not recipient_list:
        logger.warning("No recipients provided for subject '%s'", subject)
        return

    thread_pool_executor.submit(
        _send_email_blocking,
        subject,
        recipient_list,
        text_content,
        html_content,
        max_attempts_per_provider=2,
        initial_delay=1.0,
        backoff_factor=2.0,
    )

    logger.info(
        "Email task for subject '%s' submitted to thread pool for %d recipients.",
        subject,
        len(recipient_list),
    )