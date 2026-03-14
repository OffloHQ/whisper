from django.conf import settings
from .render import render_email


def build_access_request_signup_email(*, signup_url):
    product_name = getattr(settings, "PRODUCT_NAME", "Whisper")
    subject = f"Continue your {product_name} signup"
    html_body, text_body = render_email(
        html_template="emails/access/request_access.html",
        text_template="emails/access/request_access.txt",
        context={
            "signup_url": signup_url,
            "site_base_url": settings.SITE_BASE_URL,
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


def build_access_request_rejection_email():
    subject = "Update on your Whisper access request"
    html_body, text_body = render_email(
        html_template="emails/access/rejection.html",
        text_template="emails/access/rejection.txt",
        context={
            "site_base_url": settings.SITE_BASE_URL,
        },
    )
    return subject, html_body, text_body


def build_access_request_waitlist_email():
    subject = "Whisper isn’t in your area yet — but you’re on the list"
    html_body, text_body = render_email(
        html_template="emails/access/waitlist.html",
        text_template="emails/access/waitlist.txt",
        context={
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
    subject = f"{product_name} Listing Check-In"
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
