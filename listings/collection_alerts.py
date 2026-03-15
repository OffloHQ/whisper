import logging
from urllib.parse import urlencode

from django.db.models import Exists, OuterRef

from services.email import send_email
from services.email.messages import build_collection_match_alert_email

from .models import Collection, EmailNotificationLog, InAppNotification, Listing

logger = logging.getLogger(__name__)


def build_collection_alert_link(collection):
    query = urlencode(collection.get_filter_query_params())
    return f"/board/?{query}" if query else "/board/"


def listing_matches_collection_filter(listing, saved_filter):
    if saved_filter is None:
        return False
    if saved_filter.city and listing.city != saved_filter.city:
        return False
    if saved_filter.stage and listing.stage != saved_filter.stage:
        return False
    if saved_filter.min_beds is not None and listing.beds < saved_filter.min_beds:
        return False
    if saved_filter.min_baths is not None and listing.baths < saved_filter.min_baths:
        return False
    if saved_filter.min_price is not None and listing.price_max < saved_filter.min_price:
        return False
    if saved_filter.max_price is not None and listing.price_min > saved_filter.max_price:
        return False
    return True


def get_collections_requiring_match_alert_for_listing(listing):
    existing_log = EmailNotificationLog.objects.filter(
        collection=OuterRef("pk"),
        listing=listing,
        notification_type=EmailNotificationLog.NotificationType.COLLECTION_MATCH,
    )
    collections = (
        Collection.objects.filter(
            notifications_enabled=True,
            agent__is_active=True,
            agent__collection_match_emails=True,
        )
        .exclude(agent=listing.agent)
        .select_related("agent", "saved_filter")
        .annotate(has_match_log=Exists(existing_log))
        .filter(has_match_log=False)
        .order_by("agent__email", "name")
    )
    return [collection for collection in collections if listing_matches_collection_filter(listing, getattr(collection, "saved_filter", None))]


def send_collection_match_alerts_for_listing(listing):
    if not listing.is_active or listing.status != Listing.Status.ACTIVE:
        return 0

    matching_collections = get_collections_requiring_match_alert_for_listing(listing)
    grouped_matches = {}
    for collection in matching_collections:
        grouped_matches.setdefault(collection.agent_id, {"agent": collection.agent, "collections": []})
        grouped_matches[collection.agent_id]["collections"].append(collection)

    sent_count = 0
    for payload in grouped_matches.values():
        agent = payload["agent"]
        collections = payload["collections"]
        subject, html_body, text_body = build_collection_match_alert_email(
            agent_name=agent.name,
            listing=listing,
            collection_names=[collection.name for collection in collections],
        )
        try:
            send_email(
                to_email=agent.email,
                subject=subject,
                html_body=html_body,
                text_body=text_body,
            )
        except Exception:
            logger.warning(
                "Collection match alert email failed for listing_id=%s agent_id=%s",
                listing.id,
                agent.id,
                exc_info=True,
            )
            continue

        EmailNotificationLog.objects.bulk_create(
            [
                EmailNotificationLog(
                    agent=agent,
                    collection=collection,
                    listing=listing,
                    recipient_email=agent.email,
                    notification_type=EmailNotificationLog.NotificationType.COLLECTION_MATCH,
                )
                for collection in collections
            ],
            ignore_conflicts=True,
        )
        InAppNotification.objects.bulk_create(
            [
                InAppNotification(
                    agent=agent,
                    notification_type=InAppNotification.NotificationType.COLLECTION_MATCH,
                    title=f"New board posting matches {collection.name}",
                    body=f"{listing.title} matches your saved collection alert.",
                    collection=collection,
                    listing=listing,
                    link_url=build_collection_alert_link(collection),
                )
                for collection in collections
            ],
            ignore_conflicts=True,
        )
        sent_count += 1
    return sent_count
