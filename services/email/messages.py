from django.conf import settings
from .render import render_email


def build_access_request_signup_email(*, signup_url):
    product_name = getattr(settings, "PRODUCT_NAME", "Whisper")
    subject = f"Complete your {product_name} signup"
    html_body, text_body = render_email(
        html_template="emails/access/request_access.html",
        text_template="emails/access/request_access.txt",
        context={
            "signup_url": signup_url,
            "site_base_url": settings.SITE_BASE_URL,
        },
    )
    return subject, html_body, text_body


def build_front_door_request_access_email(*, request_access_url):
    subject = "Get access to Whisper"
    html_body, text_body = render_email(
        html_template="emails/access/front_door_request_access.html",
        text_template="emails/access/front_door_request_access.txt",
        context={
            "request_access_url": request_access_url,
            "site_base_url": settings.SITE_BASE_URL,
        },
    )
    return subject, html_body, text_body


def build_access_request_signup_reminder_email(*, signup_url, reminder_day):
    subject = "Reminder: complete your Whisper signup"
    html_body, text_body = render_email(
        html_template="emails/access/request_access_reminder.html",
        text_template="emails/access/request_access_reminder.txt",
        context={
            "signup_url": signup_url,
            "site_base_url": settings.SITE_BASE_URL,
            "reminder_day": reminder_day,
        },
    )
    return subject, html_body, text_body


def build_access_request_manual_approval_email(*, continuation_link):
    subject = "Great news — continue your Whisper signup"
    html_body, text_body = render_email(
        html_template="emails/access/manual_approval.html",
        text_template="emails/access/manual_approval.txt",
        context={
            "continuation_link": continuation_link,
            "site_base_url": settings.SITE_BASE_URL,
        },
    )
    return subject, html_body, text_body


def build_access_request_rejection_email(*, review_reason=""):
    subject = "Update on your Whisper access request"
    html_body, text_body = render_email(
        html_template="emails/access/rejection.html",
        text_template="emails/access/rejection.txt",
        context={
            "site_base_url": settings.SITE_BASE_URL,
            "review_reason": review_reason,
        },
    )
    return subject, html_body, text_body


def build_access_request_terminated_email(*, termination_reason=""):
    subject = "Whisper Access Terminated"
    html_body, text_body = render_email(
        html_template="emails/access/terminated.html",
        text_template="emails/access/terminated.txt",
        context={
            "site_base_url": settings.SITE_BASE_URL,
            "termination_reason": termination_reason,
        },
    )
    return subject, html_body, text_body


def build_access_request_waitlist_email():
    subject = "You’re on the list for Whisper"
    html_body, text_body = render_email(
        html_template="emails/access/waitlist.html",
        text_template="emails/access/waitlist.txt",
        context={
            "site_base_url": settings.SITE_BASE_URL,
        },
    )
    return subject, html_body, text_body


def build_waitlist_coming_soon_email(*, unsubscribe_url):
    subject = "Whisper — Coming Soon"
    html_body, text_body = render_email(
        html_template="emails/access/waitlist_coming_soon.html",
        text_template="emails/access/waitlist_coming_soon.txt",
        context={
            "site_base_url": settings.SITE_BASE_URL,
            "unsubscribe_url": unsubscribe_url,
        },
    )
    return subject, html_body, text_body


def build_waitlist_open_signup_email(*, signup_url, unsubscribe_url):
    subject = "Whisper — Open in Your Area"
    html_body, text_body = render_email(
        html_template="emails/access/waitlist_open_signup.html",
        text_template="emails/access/waitlist_open_signup.txt",
        context={
            "site_base_url": settings.SITE_BASE_URL,
            "signup_url": signup_url,
            "unsubscribe_url": unsubscribe_url,
        },
    )
    return subject, html_body, text_body


def build_access_request_activation_email(*, sign_in_url):
    subject = "Whisper is live for you now"
    html_body, text_body = render_email(
        html_template="emails/access/activation.html",
        text_template="emails/access/activation.txt",
        context={
            "sign_in_url": sign_in_url,
            "site_base_url": settings.SITE_BASE_URL,
        },
    )
    return subject, html_body, text_body


def build_account_verification_email(*, agent_name, verification_url):
    product_name = getattr(settings, "PRODUCT_NAME", "Whisper")
    subject = f"Verify your {product_name} email"
    html_body, text_body = render_email(
        html_template="emails/account/verify_email.html",
        text_template="emails/account/verify_email.txt",
        context={
            "agent_name": agent_name,
            "verification_url": verification_url,
            "site_base_url": settings.SITE_BASE_URL,
        },
    )
    return subject, html_body, text_body


def build_listing_checkin_group_email(*, agent_name, listings):
    product_name = getattr(settings, "PRODUCT_NAME", "Whisper")
    subject = f"{product_name} Opportunity Check-In"
    html_body, text_body = render_email(
        html_template="emails/listings/checkin_group.html",
        text_template="emails/listings/checkin_group.txt",
        context={
            "agent_name": agent_name,
            "listings": listings,
            "site_base_url": settings.SITE_BASE_URL,
        },
    )
    return subject, html_body, text_body


def build_magic_sign_in_email(*, sign_in_url):
    product_name = getattr(settings, "PRODUCT_NAME", "Whisper")
    subject = f"Your {product_name} sign-in link"
    html_body, text_body = render_email(
        html_template="emails/access/magic_sign_in.html",
        text_template="emails/access/magic_sign_in.txt",
        context={
            "sign_in_url": sign_in_url,
            "site_base_url": settings.SITE_BASE_URL,
        },
    )
    return subject, html_body, text_body


def build_collection_match_alert_email(*, agent_name, listing, collection_names):
    product_name = getattr(settings, "PRODUCT_NAME", "Whisper")
    if len(collection_names) == 1:
        subject = f"{product_name} — New Opportunity matches {collection_names[0]}"
    else:
        subject = f"{product_name} — New Opportunity matches {len(collection_names)} collection alerts"
    html_body, text_body = render_email(
        html_template="emails/collections/match_alert.html",
        text_template="emails/collections/match_alert.txt",
        context={
            "agent_name": agent_name,
            "listing": listing,
            "collection_names": collection_names,
            "site_base_url": settings.SITE_BASE_URL,
        },
    )
    return subject, html_body, text_body


def build_product_update_email(*, subject_line, heading, body_copy, cta_url=""):
    product_name = getattr(settings, "PRODUCT_NAME", "Whisper")
    subject = f"{product_name} — {subject_line}"
    html_body = (
        f"<p><strong>{heading}</strong></p>"
        f"<p>{body_copy}</p>"
        + (f'<p><a href="{cta_url}">Open Whisper</a></p>' if cta_url else "")
    )
    text_body = f"{heading}\n\n{body_copy}"
    if cta_url:
        text_body += f"\n\nOpen Whisper:\n{cta_url}"
    return subject, html_body, text_body
