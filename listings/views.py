import logging
from urllib.parse import urlencode

from django.contrib import messages
from django.conf import settings
from django.core import signing
from django.db.models import Value
from django.db.models.functions import Coalesce, Lower
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache

from .auth_links import (
    get_auth_access_token,
    get_valid_auth_access_token,
)
from .checkins import is_listing_stale, load_signed_listing_token
from .collection_alerts import send_collection_match_alerts_for_listing
from .email_flows import (
    build_access_request_signup_token,
    load_access_request_continuation_token,
    load_access_request_signup_token,
    load_waitlist_unsubscribe_token,
    send_front_door_sign_in_email,
    send_access_request_signup_email,
    build_agent_email_verification_token,
    load_agent_email_verification_token,
    send_agent_email_verification,
)
from .forms import (
    AccountDeletionForm,
    AgentEmailForm,
    AgentPhoneForm,
    AssignSavedListingForm,
    CollectionAlertSaveForm,
    CollectionAlertSettingsForm,
    CollectionForm,
    EmailEntryForm,
    FeedFilterForm,
    LegalAcceptanceForm,
    ListingForm,
    NotificationPreferencesForm,
    RequestAccessForm,
    StateWaitlistForm,
    SignupContactForm,
    SignupIdentityForm,
)
from .intake import (
    MANUAL_REVIEW_MESSAGE,
    WAITLIST_TOAST_MESSAGE,
    apply_failed_verification_routing,
    waitlist_access_request,
)
from .models import AccessRequest, AgentEmail, AgentPhone, AgentUser, AuthAccessToken, Collection, CollectionFilter, CollectionItem, InAppNotification, Listing, SavedListing
from .utils import format_listing_price
from .verification import VerificationService, VerificationStatus
from .verification.utils import get_state_display_name, normalize_license_number

CURRENT_AGENT_SESSION_KEY = "current_agent_id"
CURRENT_AGENT_LOCKED_OUT_KEY = "current_agent_logged_out"
PENDING_SIGNUP_AGENT_SESSION_KEY = "pending_signup_agent_id"
PENDING_ACCESS_REQUEST_SESSION_KEY = "pending_access_request_id"
DEV_SIGNUP_LINK_SESSION_KEY = "dev_access_request_signup_link"

logger = logging.getLogger(__name__)
FRONT_DOOR_NEUTRAL_TOAST = "If this email is registered, a sign-in link has been sent. Please check your inbox and spam folder."
DUPLICATE_SIGNUP_MESSAGE = "This email is already registered. Enter it on the sign-in page to get a magic link."
LEGAL_NOTICE_MESSAGE = (
    "By continuing, you acknowledge that Whisper is not an MLS, brokerage, or legal compliance service, "
    "and that licensed professionals remain responsible for complying with brokerage, MLS, licensing, and local law requirements."
)

def get_active_agent_queryset():
    return AgentUser.objects.filter(
        is_active=True,
        is_verified=True,
        signup_status=AgentUser.SignupStatus.ACTIVE,
        deleted_at__isnull=True,
    ).order_by("pk")


def get_unread_notification_count(agent):
    if agent is None:
        return 0
    return InAppNotification.objects.filter(agent=agent, is_read=False).count()


def set_current_agent(request, agent):
    if request is None:
        return
    request.session[CURRENT_AGENT_SESSION_KEY] = agent.id
    request.session[CURRENT_AGENT_LOCKED_OUT_KEY] = False


def clear_current_agent(request):
    if request is None:
        return
    request.session.pop(CURRENT_AGENT_SESSION_KEY, None)
    request.session[CURRENT_AGENT_LOCKED_OUT_KEY] = True


def clear_pending_signup(request):
    if request is None:
        return
    request.session.pop(PENDING_SIGNUP_AGENT_SESSION_KEY, None)
    request.session.pop(PENDING_ACCESS_REQUEST_SESSION_KEY, None)


def get_session_agent(request):
    if request is None:
        return None
    if request.session.get(CURRENT_AGENT_LOCKED_OUT_KEY):
        return None

    session_agent_id = request.session.get(CURRENT_AGENT_SESSION_KEY)
    if not session_agent_id:
        return None
    return get_active_agent_queryset().filter(pk=session_agent_id).first()


def get_current_agent(request=None):
    if request is not None:
        return get_session_agent(request)

    return get_active_agent_queryset().first()


def requires_legal_acceptance(agent):
    if agent is None:
        return False
    return not agent.has_completed_legal_acceptance


def get_post_auth_redirect(agent):
    return reverse("feed")


def get_pending_signup_agent(request):
    pending_agent_id = request.session.get(PENDING_SIGNUP_AGENT_SESSION_KEY)
    if not pending_agent_id:
        return None
    return AgentUser.objects.filter(pk=pending_agent_id).first()


def get_pending_access_request(request):
    access_request_id = request.session.get(PENDING_ACCESS_REQUEST_SESSION_KEY)
    if not access_request_id:
        return None
    return AccessRequest.objects.filter(pk=access_request_id).first()


def get_registered_agent_for_email(email):
    return get_active_agent_queryset().filter(email=email).first()


def get_agent_initials(agent):
    if agent is None:
        return "WW"

    name_parts = [part for part in agent.name.split() if part]
    if len(name_parts) >= 2:
        return f"{name_parts[0][0]}{name_parts[1][0]}".upper()
    if len(name_parts) == 1:
        return name_parts[0][:2].upper()
    return agent.email[:2].upper()


def get_agent_membership_level(agent):
    if agent is None:
        return "Guest"
    return "Founding Member" if agent.is_verified else "Free"


def get_agent_membership_status(agent):
    if agent is None:
        return "Inactive"
    return "Active"


def get_agent_license_status(agent):
    if agent is None:
        return ""
    return "Verified" if agent.is_verified else "On file"


def mask_license_number(license_number):
    if not license_number:
        return ""
    if len(license_number) <= 4:
        return license_number
    return f'{"*" * max(len(license_number) - 4, 2)}{license_number[-4:]}'


def get_agent_contact_emails(agent):
    if agent is None:
        return AgentEmail.objects.none()
    return agent.emails.all()


def get_agent_phones(agent):
    if agent is None:
        return AgentPhone.objects.none()
    return agent.phones.all()


def set_primary_agent_email(agent, agent_email):
    if not agent_email.is_verified:
        raise ValueError("Primary email must be verified.")

    agent.emails.exclude(pk=agent_email.pk).filter(is_primary=True).update(is_primary=False)
    if not agent_email.is_primary:
        agent_email.is_primary = True
        agent_email.save(update_fields=["is_primary"])
    if agent.email != agent_email.email:
        agent.email = agent_email.email
        agent.save(update_fields=["email"])


def build_remove_filter_url(request, keys_to_remove):
    params = request.GET.copy()
    if isinstance(keys_to_remove, str):
        keys_to_remove = [keys_to_remove]
    for key_to_remove in keys_to_remove:
        if key_to_remove in params:
            del params[key_to_remove]
    query = params.urlencode()
    return f"{request.path}?{query}" if query else request.path


def build_active_filters(request, cleaned_data):
    active_filters = []

    if cleaned_data.get("city"):
        active_filters.append(
            {
                "label": cleaned_data["city"],
                "remove_url": build_remove_filter_url(request, "city"),
            }
        )

    if cleaned_data.get("stage"):
        active_filters.append(
            {
                "label": Listing.Stage(cleaned_data["stage"]).label,
                "remove_url": build_remove_filter_url(request, "stage"),
            }
        )

    if cleaned_data.get("min_beds"):
        active_filters.append(
            {
                "label": f'{cleaned_data["min_beds"]}+ Beds',
                "remove_url": build_remove_filter_url(request, "min_beds"),
            }
        )

    if cleaned_data.get("min_baths") is not None:
        active_filters.append(
            {
                "label": f'{cleaned_data["min_baths"]}+ Baths',
                "remove_url": build_remove_filter_url(request, "min_baths"),
            }
        )

    min_price = cleaned_data.get("min_price")
    max_price = cleaned_data.get("max_price")
    if min_price and max_price:
        price_label = f"{format_listing_price(min_price)}–{format_listing_price(max_price)}"
        active_filters.append(
            {
                "label": price_label,
                "remove_url": build_remove_filter_url(request, ["min_price", "max_price"]),
            }
        )
    elif min_price:
        active_filters.append(
            {
                "label": f"{format_listing_price(min_price)}+",
                "remove_url": build_remove_filter_url(request, "min_price"),
            }
        )
    elif max_price:
        active_filters.append(
            {
                "label": f"Up to {format_listing_price(max_price)}",
                "remove_url": build_remove_filter_url(request, "max_price"),
            }
        )

    if request.GET.get("mine") == "on":
        active_filters.append(
            {
                "label": "My Opportunities",
                "remove_url": build_remove_filter_url(request, "mine"),
            }
        )

    return active_filters


def build_collection_url(request, collection):
    query = urlencode(collection.get_filter_query_params())
    return f"{request.path}?{query}" if query else request.path


def build_saved_collections(request, agent):
    if agent is None:
        return []

    collections = (
        Collection.objects.filter(agent=agent)
        .select_related("saved_filter")
        .order_by("name", "-created_at")
    )
    return [
        {
            "name": collection.name,
            "url": build_collection_url(request, collection),
            "notifications_enabled": collection.notifications_enabled and hasattr(collection, "saved_filter"),
        }
        for collection in collections
    ]


def get_collection_filter_data(collection):
    saved_filter = getattr(collection, "saved_filter", None)
    if saved_filter is None:
        return {}

    return {
        "city": saved_filter.city or "",
        "stage": saved_filter.stage or "",
        "min_beds": saved_filter.min_beds,
        "min_baths": saved_filter.min_baths,
        "min_price": saved_filter.min_price,
        "max_price": saved_filter.max_price,
    }


def build_collection_summary(collection):
    filter_data = get_collection_filter_data(collection)
    summary_parts = []

    if filter_data.get("city"):
        summary_parts.append(filter_data["city"])
    if filter_data.get("stage"):
        summary_parts.append(Listing.Stage(filter_data["stage"]).label)
    if filter_data.get("min_beds") is not None:
        summary_parts.append(f'{filter_data["min_beds"]}+ Beds')
    if filter_data.get("min_baths") is not None:
        summary_parts.append(f'{filter_data["min_baths"]}+ Baths')

    min_price = filter_data.get("min_price")
    max_price = filter_data.get("max_price")
    if min_price and max_price:
        summary_parts.append(f"{format_listing_price(min_price)}–{format_listing_price(max_price)}")
    elif min_price:
        summary_parts.append(f"{format_listing_price(min_price)}+")
    elif max_price:
        summary_parts.append(f"Up to {format_listing_price(max_price)}")

    return ", ".join(summary_parts) if summary_parts else "No saved criteria yet."


def get_collection_items(collection):
    return (
        CollectionItem.objects.filter(collection=collection, listing__is_active=True)
        .select_related("listing", "listing__agent")
        .order_by("-created_at")
    )


def build_workspace_context(request, *, section="collections"):
    current_agent = get_current_agent(request)
    active_section = section if section in {"collections", "saved", "posts"} else "collections"
    collections = []
    saved_listings = SavedListing.objects.none()
    saved_listing_cards = []
    my_posts = Listing.objects.none()

    if current_agent is not None:
        collection_qs = (
            Collection.objects.filter(agent=current_agent)
            .select_related("saved_filter")
            .prefetch_related("items")
            .order_by("name", "-created_at")
        )
        collections = [
            {
                "id": collection.id,
                "name": collection.name,
                "summary": build_collection_summary(collection),
                "listing_count": collection.items.count(),
                "detail_url": reverse("workspace_collection_detail", args=[collection.id]),
                "notifications_enabled": collection.notifications_enabled and hasattr(collection, "saved_filter"),
            }
            for collection in collection_qs
        ]
        organized_listing_ids = CollectionItem.objects.filter(
            collection__agent=current_agent
        ).values_list("listing_id", flat=True)
        saved_listings = (
            SavedListing.objects.filter(agent=current_agent)
            .exclude(listing_id__in=organized_listing_ids)
            .select_related("listing", "listing__agent")
            .order_by("-created_at")
        )
        saved_listing_cards = [
            {
                "saved": saved,
                "form": AssignSavedListingForm(agent=current_agent, prefix=f"saved-{saved.listing_id}"),
            }
            for saved in saved_listings
        ]
        my_posts = (
            Listing.objects.filter(agent=current_agent, is_active=True)
            .select_related("agent")
            .order_by("-created_at")
        )

    return {
        "active_section": active_section,
        "collections": collections,
        "current_agent": current_agent,
        "current_agent_initials": get_agent_initials(current_agent),
        "current_agent_membership": get_agent_membership_level(current_agent),
        "unread_notification_count": get_unread_notification_count(current_agent),
        "my_posts": my_posts,
        "saved_listing_cards": saved_listing_cards,
        "saved_listings": saved_listings,
    }


def build_account_context(request, *, deletion_form=None):
    current_agent = get_current_agent(request)
    if current_agent is None:
        return None

    active_posts_count = Listing.objects.filter(agent=current_agent, is_active=True).count()
    saved_count = SavedListing.objects.filter(agent=current_agent).count()
    collections_count = Collection.objects.filter(agent=current_agent).count()

    return {
        "current_agent": current_agent,
        "current_agent_initials": get_agent_initials(current_agent),
        "current_agent_membership": get_agent_membership_level(current_agent),
        "unread_notification_count": get_unread_notification_count(current_agent),
        "account_membership_status": get_agent_membership_status(current_agent),
        "account_license_status": get_agent_license_status(current_agent),
        "masked_license_number": mask_license_number(current_agent.license_number),
        "member_since": current_agent.created_at,
        "agent_emails": get_agent_contact_emails(current_agent),
        "agent_phones": get_agent_phones(current_agent),
        "show_email_to_agents": current_agent.show_email_to_agents,
        "agent_email_form": AgentEmailForm(),
        "agent_phone_form": AgentPhoneForm(),
        "activity_summary": [
            {"label": "My Active Posts", "value": active_posts_count, "url": f'{reverse("workspace")}?section=posts'},
            {"label": "Saved Opportunities", "value": saved_count, "url": f'{reverse("workspace")}?section=saved'},
            {"label": "Collections", "value": collections_count, "url": f'{reverse("workspace")}?section=collections'},
        ],
        "notification_preferences_form": NotificationPreferencesForm(
            initial={
                "freshness_reminder_emails": current_agent.freshness_reminder_emails,
                "collection_match_emails": current_agent.collection_match_emails,
                "product_update_emails": current_agent.product_update_emails,
            }
        ),
        "deletion_form": deletion_form or AccountDeletionForm(),
    }


def build_filter_query_from_cleaned_data(cleaned_data):
    params = {}
    if cleaned_data.get("city"):
        params["city"] = cleaned_data["city"]
    if cleaned_data.get("stage"):
        params["stage"] = cleaned_data["stage"]
    if cleaned_data.get("min_beds") is not None:
        params["min_beds"] = str(cleaned_data["min_beds"])
    if cleaned_data.get("min_baths") is not None:
        params["min_baths"] = str(cleaned_data["min_baths"])
    if cleaned_data.get("min_price") is not None:
        params["min_price"] = str(cleaned_data["min_price"])
    if cleaned_data.get("max_price") is not None:
        params["max_price"] = str(cleaned_data["max_price"])
    return params


def apply_listing_filters(listings, cleaned_data, current_agent=None):
    if cleaned_data.get("city"):
        listings = listings.filter(city__icontains=cleaned_data["city"])
    if cleaned_data.get("stage"):
        listings = listings.filter(stage=cleaned_data["stage"])
    if cleaned_data.get("min_beds") is not None:
        listings = listings.filter(beds__gte=cleaned_data["min_beds"])
    if cleaned_data.get("min_baths") is not None:
        listings = listings.filter(baths__gte=cleaned_data["min_baths"])
    if cleaned_data.get("min_price") is not None:
        listings = listings.filter(price_max__gte=cleaned_data["min_price"])
    if cleaned_data.get("max_price") is not None:
        listings = listings.filter(price_min__lte=cleaned_data["max_price"])
    return listings


def get_feed_context(
    request,
    *,
    form=None,
    filter_form=None,
    collection_form=None,
    show_filter_panel=False,
    show_listing_form=False,
):
    sort_key = request.GET.get("sort", "newest")
    sort_dir = request.GET.get("dir", "desc")
    valid_sorts = {"newest", "opportunity", "stage", "price", "specs"}
    if sort_key not in valid_sorts:
        sort_key = "newest"
    if sort_dir not in {"asc", "desc"}:
        sort_dir = "desc"

    listings = (
        Listing.objects.filter(is_active=True)
        .select_related("agent")
        .prefetch_related("agent__phones", "agent__emails")
    )
    current_agent = get_current_agent(request)
    filter_form = filter_form or FeedFilterForm(request.GET or None)
    active_filters = []
    collection_form = collection_form or CollectionAlertSaveForm(agent=current_agent, initial={"notifications_enabled": False})
    saved_listing_ids = set()
    saved_listing_count = 0
    mine_only = request.GET.get("mine") == "on"
    saved_view = request.GET.get("view") == "saved"

    if filter_form.is_bound:
        if filter_form.is_valid():
            cleaned_data = filter_form.cleaned_data
            listings = apply_listing_filters(listings, cleaned_data, current_agent=current_agent)
            if mine_only:
                if current_agent is None:
                    listings = listings.none()
                else:
                    listings = listings.filter(agent=current_agent)
            if saved_view:
                if current_agent is None:
                    listings = listings.none()
                else:
                    listings = listings.filter(saved_by_agents__agent=current_agent)
            active_filters = build_active_filters(request, cleaned_data)
        else:
            show_filter_panel = True
    elif saved_view:
        if current_agent is None:
            listings = listings.none()
        else:
            listings = listings.filter(saved_by_agents__agent=current_agent)

    if sort_key == "opportunity":
        listings = listings.annotate(feed_sort_title=Lower(Coalesce("title", "city", Value(""))))
        order_fields = ["feed_sort_title", "-created_at"]
    elif sort_key == "stage":
        order_fields = ["stage", "-created_at"]
    elif sort_key == "price":
        listings = listings.annotate(feed_sort_price=Coalesce("price_min", "price_max"))
        order_fields = ["feed_sort_price", "-created_at"]
    elif sort_key == "specs":
        order_fields = ["beds", "baths", "-created_at"]
    else:
        order_fields = ["-created_at"]

    if sort_key != "newest" and sort_dir == "desc":
        order_fields = [field if field.startswith("-") else f"-{field}" for field in order_fields]
    elif sort_key == "newest" and sort_dir == "asc":
        order_fields = ["created_at"]
    listings = listings.order_by(*order_fields)

    sort_defaults = {
        "opportunity": "asc",
        "stage": "asc",
        "price": "desc",
        "specs": "desc",
    }
    sort_headers = []
    for key, label in (
        ("opportunity", "Opportunity"),
        ("stage", "Stage"),
        ("price", "Price"),
        ("specs", "Specs"),
    ):
        params = request.GET.copy()
        next_dir = sort_defaults[key]
        if sort_key == key:
            next_dir = "desc" if sort_dir == "asc" else "asc"
        params["sort"] = key
        params["dir"] = next_dir
        sort_headers.append(
            {
                "key": key,
                "label": label,
                "url": f"{request.path}?{params.urlencode()}",
                "is_active": sort_key == key,
                "direction": sort_dir if sort_key == key else "",
            }
        )
    sort_header_map = {header["key"]: header for header in sort_headers}

    current_sort_label = {
        "newest": "Newest",
        "opportunity": "Opportunity",
        "stage": "Stage",
        "price": "Price",
        "specs": "Specs",
    }[sort_key]
    if sort_key != "newest":
        if sort_key in {"opportunity", "stage"}:
            direction_label = "A-Z" if sort_dir == "asc" else "Z-A"
        else:
            direction_label = "Low-High" if sort_dir == "asc" else "High-Low"
        current_sort_label = f"{current_sort_label} ({direction_label})"

    my_listing_count = 0
    if current_agent is not None:
        my_listing_count = Listing.objects.filter(agent=current_agent, is_active=True).count()
        saved_listing_count = SavedListing.objects.filter(agent=current_agent).count()
        saved_listing_ids = set(
            SavedListing.objects.filter(agent=current_agent, listing__in=listings).values_list("listing_id", flat=True)
        )

    return {
        "active_filters": active_filters,
        "clear_filters_url": request.path,
        "collection_form": collection_form,
        "current_agent": current_agent,
        "current_agent_initials": get_agent_initials(current_agent),
        "current_agent_membership": get_agent_membership_level(current_agent),
        "unread_notification_count": get_unread_notification_count(current_agent),
        "filter_form": filter_form,
        "form": form or ListingForm(),
        "feed_current_sort_label": current_sort_label,
        "feed_sort_header_map": sort_header_map,
        "feed_sort_headers": sort_headers,
        "listings": listings,
        "my_listing_count": my_listing_count,
        "post_form_action": "/post/?source=feed",
        "save_collection_action": "/collections/save/",
        "saved_listing_count": saved_listing_count,
        "saved_collections": build_saved_collections(request, current_agent),
        "saved_listing_ids": saved_listing_ids,
        "show_filter_panel": show_filter_panel,
        "show_listing_form": show_listing_form,
        "has_filtered_empty_state": bool(active_filters) and not listings.exists(),
        "toggle_save_listing_action": "/listings/save-toggle/",
        "draft_listings": [],
        "workspace_active": mine_only or saved_view,
        "home_active": not mine_only and not saved_view,
        "workspace_saved_url": f'{reverse("feed")}?view=saved',
        "workspace_my_listings_url": f'{reverse("feed")}?mine=on',
    }


@never_cache
def feed(request):
    current_agent = get_session_agent(request)
    if current_agent is None:
        messages.error(request, "Log in to access the Whisper board.")
        return redirect("landing")
    return render(request, "feed.html", get_feed_context(request))


def landing(request):
    request_access_email = request.GET.get("email", "").strip().lower()
    request_access_url = reverse("request_access")
    if request_access_email:
        request_access_url = f"{request_access_url}?{urlencode({'email': request_access_email})}"
    if request.method == "POST":
        form = EmailEntryForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"].lower()

            messages.info(request, FRONT_DOOR_NEUTRAL_TOAST)
            try:
                send_front_door_sign_in_email(request, email=email)
            except Exception:
                if not settings.DEBUG:
                    raise
                logger.warning(
                    "Front-door sign-in email delivery failed in DEBUG mode for %s. Continuing without outbound email.",
                    email,
                    exc_info=True,
                )
                messages.warning(request, "Local email delivery failed.")
            return redirect("landing")

    else:
        form = EmailEntryForm()

    return render(
        request,
        "landing.html",
        {
            "form": form,
            "request_access_url": request_access_url,
        },
    )


def request_access(request):
    if request.method == "POST":
        form = RequestAccessForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"].lower()
            logger.info("request_access form valid for %s", email)
            existing_agent = get_registered_agent_for_email(email)
            if existing_agent is not None:
                messages.error(request, "This email already has Whisper access. Use Log In.")
                return redirect("landing")

            access_request, _ = AccessRequest.objects.get_or_create(email=email)
            access_request.status = AccessRequest.Status.LINK_SENT
            access_request.signup_sent_at = timezone.now()
            access_request.save(update_fields=["status", "signup_sent_at", "updated_at"])
            logger.info("request_access saved access request %s for %s", access_request.pk, email)
            signup_url = None
            if getattr(settings, "DEV_EXPOSE_SIGNUP_LINKS", False):
                signup_url = request.build_absolute_uri(
                    reverse("signup_identity", args=[build_access_request_signup_token(access_request)])
                )
            try:
                logger.info("request_access attempting signup email for %s", email)
                send_access_request_signup_email(request, access_request)
                logger.info("request_access signup email sent for %s", email)
            except Exception:
                logger.exception("request_access signup email failed for %s", email)
                if not settings.DEBUG:
                    raise
                logger.warning(
                    "Request access email delivery failed in DEBUG mode for %s. Continuing without outbound email.",
                    email,
                    exc_info=True,
                )
                messages.warning(request, "Local email delivery failed. Use the dev signup link below to continue testing.")
            if signup_url:
                request.session[DEV_SIGNUP_LINK_SESSION_KEY] = signup_url
            messages.success(request, "Check your email for a secure Whisper signup link.")
            return redirect("request_access")
    else:
        form = RequestAccessForm(initial={"email": request.GET.get("email", "")})

    dev_signup_link = None
    if getattr(settings, "DEV_EXPOSE_SIGNUP_LINKS", False):
        dev_signup_link = request.session.pop(DEV_SIGNUP_LINK_SESSION_KEY, None)

    return render(request, "request_access.html", {"form": form, "dev_signup_link": dev_signup_link})


def state_waitlist(request):
    submitted = False
    submitted_state_name = ""

    if request.method == "POST":
        form = StateWaitlistForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"].lower()
            existing_agent = get_registered_agent_for_email(email)
            if existing_agent is not None:
                messages.error(request, "This email already has Whisper access. Use Log In.")
                return redirect("landing")

            access_request, _ = AccessRequest.objects.get_or_create(email=email)
            access_request.full_name = form.cleaned_data["full_name"].strip()
            access_request.state = form.cleaned_data["state"]
            access_request.status = AccessRequest.Status.WAITLIST
            access_request.queue_type = AccessRequest.QueueType.WAITLIST
            access_request.decision_status = AccessRequest.DecisionStatus.PENDING
            access_request.reason_code = AccessRequest.Reason.UNSUPPORTED_STATE
            access_request.verification_status = AccessRequest.VerificationStatus.PENDING
            access_request.verification_provider = ""
            access_request.verification_attempted_at = None
            access_request.verified_at = None
            access_request.requires_manual_review = False
            access_request.waitlist_unsubscribed_at = None
            access_request.waitlist_removed_at = None
            access_request.waitlist_removed_by = None
            access_request.save(
                update_fields=[
                    "full_name",
                    "state",
                    "status",
                    "queue_type",
                    "decision_status",
                    "reason_code",
                    "verification_status",
                    "verification_provider",
                    "verification_attempted_at",
                    "verified_at",
                    "requires_manual_review",
                    "waitlist_unsubscribed_at",
                    "waitlist_removed_at",
                    "waitlist_removed_by",
                    "updated_at",
                ]
            )
            waitlist_access_request(access_request)
            submitted = True
            submitted_state_name = get_state_display_name(access_request.state)
            form = StateWaitlistForm()
    else:
        form = StateWaitlistForm()

    return render(
        request,
        "state_waitlist.html",
        {
            "form": form,
            "submitted": submitted,
            "submitted_state_name": submitted_state_name,
        },
    )


def unsubscribe_waitlist(request, token):
    try:
        payload = load_waitlist_unsubscribe_token(token)
    except signing.BadSignature:
        return render(
            request,
            "waitlist_unsubscribe_result.html",
            {"message": "This waitlist unsubscribe link is invalid or has expired."},
            status=400,
        )

    access_request = AccessRequest.objects.filter(
        pk=payload["access_request_id"],
        email=payload["email"],
    ).first()
    if access_request is None:
        return render(
            request,
            "waitlist_unsubscribe_result.html",
            {"message": "This waitlist record could not be found."},
            status=404,
        )

    if access_request.waitlist_unsubscribed_at is None:
        access_request.waitlist_unsubscribed_at = timezone.now()
        access_request.save(update_fields=["waitlist_unsubscribed_at", "updated_at"])
        access_request.log_waitlist_outreach_event(
            outreach_type=AccessRequest.WaitlistOutreachType.UNSUBSCRIBED,
            sent_at=access_request.waitlist_unsubscribed_at,
            sent_by=None,
            note="User unsubscribed from waitlist outreach.",
        )

    return render(
        request,
        "waitlist_unsubscribe_result.html",
        {"message": "You’ve been unsubscribed from Whisper waitlist updates."},
    )


@never_cache
def consume_auth_access_token(request, token):
    auth_access_token = get_valid_auth_access_token(token, scope=AuthAccessToken.Scope.SIGN_IN)
    if auth_access_token is None:
        messages.error(request, "This sign-in link is invalid or has expired.")
        return redirect("landing")

    if auth_access_token.delivery_method == AuthAccessToken.DeliveryMethod.QR:
        auth_access_token.mark_qr_completed()
        set_current_agent(request, auth_access_token.agent)
        messages.success(request, "Signed in to Whisper.")
        return redirect(get_post_auth_redirect(auth_access_token.agent))

    auth_access_token.mark_used()
    set_current_agent(request, auth_access_token.agent)
    messages.success(request, "Signed in to Whisper.")
    return redirect(get_post_auth_redirect(auth_access_token.agent))


@never_cache
def qr_sign_in_status(request, token):
    auth_access_token = get_auth_access_token(token, scope=AuthAccessToken.Scope.SIGN_IN)
    if (
        auth_access_token is None
        or auth_access_token.delivery_method != AuthAccessToken.DeliveryMethod.QR
    ):
        return JsonResponse({"status": "invalid"}, status=404)

    if auth_access_token.is_expired and auth_access_token.completed_at is None:
        return JsonResponse({"status": "expired"})

    if auth_access_token.completed_at is None:
        return JsonResponse({"status": "pending"})

    if auth_access_token.desktop_authenticated_at is not None:
        return JsonResponse({"status": "completed"})

    set_current_agent(request, auth_access_token.agent)
    auth_access_token.mark_desktop_authenticated()
    return JsonResponse({"status": "authenticated", "redirect_url": get_post_auth_redirect(auth_access_token.agent)})


def update_access_request_verification_fields(access_request, result, *, full_name):
    access_request.full_name = full_name
    access_request.state = result.state
    access_request.license_number = result.submitted_license_number
    access_request.verification_status = result.status.value
    access_request.verification_provider = result.provider
    access_request.verification_attempted_at = timezone.now()
    access_request.verified_at = timezone.now() if result.status == VerificationStatus.VERIFIED else None
    access_request.verification_reason = result.reason
    access_request.requires_manual_review = result.requires_manual_review
    access_request.verification_payload = result.raw_payload or {}
    access_request.matched_license_name = result.matched_name
    access_request.matched_license_type = result.matched_license_type
    access_request.matched_business_name = result.matched_business_name
    access_request.matched_business_city = result.matched_business_city
    access_request.matched_business_state = result.matched_business_state
    access_request.matched_expiration_date = result.matched_expiration_date
    access_request.full_name = full_name


def update_agent_verification_fields(agent, result):
    agent.is_verified = result.status == VerificationStatus.VERIFIED


def signup_identity(request, token):
    try:
        payload = load_access_request_signup_token(token)
    except signing.BadSignature:
        return render(
            request,
            "signup_identity.html",
            {
                "form": SignupIdentityForm(),
                "signup_email": "",
                "token_invalid": True,
            },
            status=400,
        )

    access_request = AccessRequest.objects.filter(
        pk=payload["access_request_id"],
        email=payload["email"],
    ).first()
    if access_request is None:
        return render(
            request,
            "signup_identity.html",
            {
                "form": SignupIdentityForm(),
                "signup_email": payload.get("email", ""),
                "token_invalid": True,
            },
            status=404,
        )

    signup_email = access_request.email
    if get_registered_agent_for_email(signup_email) is not None:
        clear_pending_signup(request)
        messages.error(request, DUPLICATE_SIGNUP_MESSAGE)
        return redirect("landing")

    if request.method == "POST":
        form = SignupIdentityForm(request.POST)
        if form.is_valid():
            full_name = form.cleaned_data["full_name"].strip()
            state = form.cleaned_data["state"].strip()
            license_number = form.cleaned_data["license_number"].strip()
            normalized_license_number = normalize_license_number(license_number)
            verification_service = VerificationService()
            verification_result = verification_service.verify_license(
                full_name=full_name,
                state=state,
                license_number=license_number,
            )
            duplicate_license = (
                AgentUser.objects.filter(license_number=normalized_license_number)
                .exclude(email=signup_email)
                .exists()
            )
            agent = AgentUser.objects.filter(email=signup_email).first()

            if agent is None:
                agent = AgentUser(
                    email=signup_email,
                    name=full_name,
                    state=state,
                    license_number=normalized_license_number,
                    is_active=False,
                )
            else:
                agent.name = full_name
                agent.state = state
                if not duplicate_license or agent.license_number == normalized_license_number:
                    agent.license_number = normalized_license_number
                agent.is_active = False

            if duplicate_license:
                verification_result = verification_result.__class__(
                    success=False,
                    status=VerificationStatus.MANUAL_REVIEW,
                    state=verification_result.state,
                    submitted_full_name=verification_result.submitted_full_name,
                    submitted_license_number=verification_result.submitted_license_number,
                    normalized_submitted_name=verification_result.normalized_submitted_name,
                    matched_name=verification_result.matched_name,
                    matched_license_number=verification_result.matched_license_number,
                    matched_license_type=verification_result.matched_license_type,
                    matched_business_name=verification_result.matched_business_name,
                    matched_business_city=verification_result.matched_business_city,
                    matched_business_state=verification_result.matched_business_state,
                    matched_expiration_date=verification_result.matched_expiration_date,
                    provider=verification_result.provider,
                    raw_payload=verification_result.raw_payload,
                    reason="That license number is already associated with another Whisper account.",
                    requires_manual_review=True,
                )

            update_access_request_verification_fields(
                access_request,
                verification_result,
                full_name=full_name,
            )

            if not verification_result.success:
                queue_type, _ = apply_failed_verification_routing(
                    access_request,
                    verification_status=access_request.verification_status,
                    reason_text=verification_result.reason,
                    duplicate_license=duplicate_license,
                )
                access_request.save(
                    update_fields=[
                        "status",
                        "queue_type",
                        "decision_status",
                        "reason_code",
                        "full_name",
                        "state",
                        "license_number",
                        "verification_status",
                        "verification_provider",
                        "verification_attempted_at",
                        "verified_at",
                        "verification_reason",
                        "requires_manual_review",
                        "verification_payload",
                        "matched_license_name",
                        "matched_license_type",
                        "matched_business_name",
                        "matched_business_city",
                        "matched_business_state",
                        "matched_expiration_date",
                        "updated_at",
                    ]
                )
                if queue_type == AccessRequest.QueueType.WAITLIST:
                    waitlist_access_request(access_request)
                    messages.info(request, WAITLIST_TOAST_MESSAGE)
                    return render(
                        request,
                        "signup_identity.html",
                        {
                            "form": form,
                            "signup_email": signup_email,
                            "token_invalid": False,
                        },
                    )

                if not duplicate_license and (agent.pk is None or agent.email == signup_email):
                    agent.signup_status = AgentUser.SignupStatus.MANUAL_REVIEW
                    update_agent_verification_fields(agent, verification_result)
                    try:
                        agent.save()
                    except Exception:
                        pass
                messages.error(request, MANUAL_REVIEW_MESSAGE)
                return render(
                    request,
                    "signup_identity.html",
                    {
                        "form": form,
                        "signup_email": signup_email,
                        "token_invalid": False,
                        "manual_review": True,
                    },
                )

            update_agent_verification_fields(agent, verification_result)
            agent.signup_status = AgentUser.SignupStatus.PENDING_CONTACT
            agent.is_active = False
            agent.save()
            access_request.save(
                update_fields=[
                    "full_name",
                    "state",
                    "license_number",
                    "queue_type",
                    "decision_status",
                    "reason_code",
                    "verification_status",
                    "verification_provider",
                    "verification_attempted_at",
                    "verified_at",
                    "verification_reason",
                    "requires_manual_review",
                    "verification_payload",
                    "matched_license_name",
                    "matched_license_type",
                    "matched_business_name",
                    "matched_business_city",
                    "matched_business_state",
                    "matched_expiration_date",
                    "updated_at",
                ]
            )
            messages.success(request, "License verified. Continue with contact details.")
            request.session[PENDING_SIGNUP_AGENT_SESSION_KEY] = agent.id
            request.session[PENDING_ACCESS_REQUEST_SESSION_KEY] = access_request.id
            return redirect("signup_contact")
    else:
        form = SignupIdentityForm()

    return render(
        request,
        "signup_identity.html",
        {
            "form": form,
            "signup_email": signup_email,
            "token_invalid": False,
        },
    )


def signup_contact_continue(request, token):
    try:
        payload = load_access_request_continuation_token(token)
    except signing.BadSignature:
        messages.error(request, "This continuation link is invalid or has expired.")
        return redirect("request_access")

    access_request = AccessRequest.objects.filter(
        pk=payload["access_request_id"],
        email=payload["email"],
    ).first()
    if get_registered_agent_for_email(payload["email"]) is not None:
        clear_pending_signup(request)
        messages.error(request, DUPLICATE_SIGNUP_MESSAGE)
        return redirect("landing")
    if (
        access_request is None
        or access_request.queue_type != AccessRequest.QueueType.MANUAL_REVIEW
        or access_request.decision_status != AccessRequest.DecisionStatus.APPROVED
        or access_request.completed_at is not None
    ):
        messages.error(request, "This continuation link is no longer available.")
        return redirect("request_access")

    agent = AgentUser.objects.filter(email=access_request.email).first()
    if agent is None or agent.signup_status != AgentUser.SignupStatus.PENDING_CONTACT:
        messages.error(request, "This continuation link is no longer available.")
        return redirect("request_access")

    request.session[PENDING_SIGNUP_AGENT_SESSION_KEY] = agent.id
    request.session[PENDING_ACCESS_REQUEST_SESSION_KEY] = access_request.id
    messages.success(request, "License verified. Continue with contact details.")
    return redirect("signup_contact")


def signup_contact(request):
    agent = get_pending_signup_agent(request)
    access_request = get_pending_access_request(request)
    if agent is None or access_request is None:
        messages.error(request, "Your signup session has expired. Request a new access link.")
        return redirect("request_access")
    if get_registered_agent_for_email(agent.email) is not None:
        clear_pending_signup(request)
        messages.error(request, DUPLICATE_SIGNUP_MESSAGE)
        return redirect("landing")

    if request.method == "POST":
        form = SignupContactForm(request.POST)
        if form.is_valid():
            primary_phone = agent.primary_phone
            if primary_phone is None:
                AgentPhone.objects.create(agent=agent, phone_number=form.cleaned_data["phone_number"])
            else:
                primary_phone.phone_number = form.cleaned_data["phone_number"]
                primary_phone.save(update_fields=["phone_number"])
            agent.brokerage = form.cleaned_data["brokerage"].strip()
            agent.city = form.cleaned_data["city"].strip()
            agent.signup_status = AgentUser.SignupStatus.ACTIVE
            agent.is_active = True
            agent.save(update_fields=["brokerage", "city", "signup_status", "is_active"])
            access_request.status = AccessRequest.Status.COMPLETED
            access_request.decision_status = AccessRequest.DecisionStatus.COMPLETED
            access_request.completed_at = timezone.now()
            access_request.save(update_fields=["status", "decision_status", "completed_at", "updated_at"])
            clear_pending_signup(request)
            set_current_agent(request, agent)
            messages.success(request, "Signup complete. Review the Terms of Use and Privacy Policy to continue.")
            return redirect("legal_acceptance")
    else:
        initial = {
            "brokerage": agent.brokerage,
            "city": agent.city,
        }
        if agent.primary_phone:
            initial["phone_number"] = agent.primary_phone.phone_number
        form = SignupContactForm(initial=initial)

    return render(
        request,
        "signup_contact.html",
        {
            "form": form,
            "signup_email": agent.email,
        },
    )


@never_cache
def legal_acceptance(request):
    current_agent = get_session_agent(request)
    if current_agent is None:
        messages.error(request, "Log in to continue.")
        return redirect("landing")
    if current_agent.signup_status != AgentUser.SignupStatus.ACTIVE:
        messages.error(request, "Finish signup before continuing.")
        return redirect("request_access")
    if current_agent.has_completed_legal_acceptance:
        return redirect("feed")

    if request.method == "POST":
        form = LegalAcceptanceForm(request.POST)
        if form.is_valid():
            accepted_at = timezone.now()
            current_agent.terms_accepted = True
            current_agent.terms_accepted_at = accepted_at
            current_agent.terms_version = settings.WHISPER_TERMS_VERSION
            current_agent.privacy_accepted = True
            current_agent.privacy_accepted_at = accepted_at
            current_agent.privacy_version = settings.WHISPER_PRIVACY_VERSION
            current_agent.legal_acceptance_ip = request.META.get("REMOTE_ADDR") or None
            current_agent.legal_acceptance_user_agent = request.META.get("HTTP_USER_AGENT", "")
            current_agent.save(
                update_fields=[
                    "terms_accepted",
                    "terms_accepted_at",
                    "terms_version",
                    "privacy_accepted",
                    "privacy_accepted_at",
                    "privacy_version",
                    "legal_acceptance_ip",
                    "legal_acceptance_user_agent",
                ]
            )
            messages.success(request, "Welcome to Whisper.")
            return redirect("feed")
    else:
        form = LegalAcceptanceForm()

    return render(
        request,
        "legal_acceptance.html",
        {
            "form": form,
            "legal_notice_message": LEGAL_NOTICE_MESSAGE,
        },
    )


def terms_of_use(request):
    return render(request, "terms_of_use.html")


def privacy_policy(request):
    return render(request, "privacy_policy.html")


def account(request):
    context = build_account_context(request)
    if context is None:
        messages.error(request, "No active agent account is available.")
        return redirect("feed")
    return render(request, "account.html", context)


@never_cache
def notifications(request):
    current_agent = get_session_agent(request)
    if current_agent is None:
        messages.error(request, "Log in to access notifications.")
        return redirect("landing")

    notifications_qs = InAppNotification.objects.filter(agent=current_agent).select_related("collection", "listing")
    return render(
        request,
        "notifications.html",
        {
            "notifications": notifications_qs,
            "current_agent": current_agent,
            "current_agent_initials": get_agent_initials(current_agent),
            "current_agent_membership": get_agent_membership_level(current_agent),
            "unread_notification_count": get_unread_notification_count(current_agent),
        },
    )


@never_cache
def open_notification(request, notification_id):
    current_agent = get_session_agent(request)
    if current_agent is None:
        messages.error(request, "Log in to access notifications.")
        return redirect("landing")

    notification = get_object_or_404(
        InAppNotification.objects.filter(agent=current_agent),
        pk=notification_id,
    )
    if not notification.is_read:
        notification.is_read = True
        notification.save(update_fields=["is_read"])
    return redirect(notification.link_url or reverse("feed"))


def add_agent_email(request):
    if request.method != "POST":
        return redirect("account")

    current_agent = get_current_agent(request)
    if current_agent is None:
        messages.error(request, "No active agent account is available.")
        return redirect("feed")

    form = AgentEmailForm(request.POST)
    if not form.is_valid():
        context = build_account_context(request)
        context["agent_email_form"] = form
        return render(request, "account.html", context, status=200)

    email = form.cleaned_data["email"].lower()
    if current_agent.emails.filter(email=email).exists():
        messages.error(request, "That email is already on your account.")
        return redirect("account")

    agent_email = AgentEmail.objects.create(agent=current_agent, email=email)
    send_agent_email_verification(request, agent_email)
    messages.success(request, "Verification email sent.")
    return redirect("account")


def verify_agent_email(request, token):
    try:
        payload = load_agent_email_verification_token(token)
    except signing.BadSignature:
        return render(
            request,
            "account_email_verification_result.html",
            {"message": "This email verification link is invalid or has expired."},
            status=400,
        )

    agent_email = AgentEmail.objects.filter(pk=payload["agent_email_id"], agent_id=payload["agent_id"]).first()
    if agent_email is None:
        return render(
            request,
            "account_email_verification_result.html",
            {"message": "This email could not be found."},
            status=404,
        )

    if not agent_email.is_verified:
        agent_email.is_verified = True
        agent_email.verified_at = timezone.now()
        agent_email.save(update_fields=["is_verified", "verified_at"])
    if not agent_email.agent.emails.exclude(pk=agent_email.pk).filter(is_primary=True).exists():
        set_primary_agent_email(agent_email.agent, agent_email)

    return render(
        request,
        "account_email_verification_result.html",
        {"message": "Your email has been verified."},
    )


def make_primary_agent_email(request, email_id):
    if request.method != "POST":
        return redirect("account")

    current_agent = get_current_agent(request)
    agent_email = AgentEmail.objects.filter(pk=email_id, agent=current_agent).first()
    if current_agent is None or agent_email is None:
        messages.error(request, "That email could not be found.")
        return redirect("account")

    if not agent_email.is_verified:
        messages.error(request, "Verify this email before making it primary.")
        return redirect("account")

    set_primary_agent_email(current_agent, agent_email)
    messages.success(request, "Primary email updated.")
    return redirect("account")


def remove_agent_email(request, email_id):
    if request.method != "POST":
        return redirect("account")

    current_agent = get_current_agent(request)
    agent_email = AgentEmail.objects.filter(pk=email_id, agent=current_agent).first()
    if current_agent is None or agent_email is None:
        messages.error(request, "That email could not be found.")
        return redirect("account")

    if current_agent.emails.count() == 1:
        messages.error(request, "You must keep at least one email on the account.")
        return redirect("account")

    if agent_email.is_primary:
        messages.error(request, "You cannot remove the primary email.")
        return redirect("account")

    agent_email.delete()
    messages.success(request, "Email removed.")
    return redirect("account")


def update_contact_visibility(request):
    if request.method != "POST":
        return redirect("account")

    current_agent = get_current_agent(request)
    if current_agent is None:
        messages.error(request, "No active agent account is available.")
        return redirect("feed")

    current_agent.show_email_to_agents = request.POST.get("show_email_to_agents") == "on"
    current_agent.save(update_fields=["show_email_to_agents"])
    messages.success(request, "Contact visibility updated.")
    return redirect("account")


def add_agent_phone(request):
    if request.method != "POST":
        return redirect("account")

    current_agent = get_current_agent(request)
    if current_agent is None:
        messages.error(request, "No active agent account is available.")
        return redirect("feed")

    form = AgentPhoneForm(request.POST)
    if not form.is_valid():
        context = build_account_context(request)
        context["agent_phone_form"] = form
        return render(request, "account.html", context, status=200)

    AgentPhone.objects.create(agent=current_agent, phone_number=form.cleaned_data["phone_number"])
    messages.success(request, "Phone number added.")
    return redirect("account")


def update_agent_phone(request, phone_id):
    if request.method != "POST":
        return redirect("account")

    current_agent = get_current_agent(request)
    phone = AgentPhone.objects.filter(pk=phone_id, agent=current_agent).first()
    if current_agent is None or phone is None:
        messages.error(request, "That phone number could not be found.")
        return redirect("account")

    form = AgentPhoneForm(request.POST)
    if not form.is_valid():
        context = build_account_context(request)
        context["agent_phone_form"] = form
        return render(request, "account.html", context, status=200)

    phone.phone_number = form.cleaned_data["phone_number"]
    phone.save(update_fields=["phone_number"])
    messages.success(request, "Phone number updated.")
    return redirect("account")


def delete_agent_phone(request, phone_id):
    if request.method != "POST":
        return redirect("account")

    current_agent = get_current_agent(request)
    phone = AgentPhone.objects.filter(pk=phone_id, agent=current_agent).first()
    if current_agent is None or phone is None:
        messages.error(request, "That phone number could not be found.")
        return redirect("account")

    if current_agent.phones.count() <= 1:
        messages.error(request, "A phone number is required for agent contact.")
        return redirect("account")

    phone.delete()
    messages.success(request, "Phone number removed.")
    return redirect("account")


def logout_account(request):
    clear_current_agent(request)
    clear_pending_signup(request)
    request.session.pop(DEV_SIGNUP_LINK_SESSION_KEY, None)
    messages.success(request, "You have been logged out.")
    return redirect("landing")


def delete_account(request):
    current_agent = get_current_agent(request)
    if current_agent is None:
        messages.error(request, "No active agent account is available.")
        return redirect("feed")

    if request.method == "POST":
        form = AccountDeletionForm(request.POST)
        if form.is_valid():
            current_agent.deactivate_account()
            clear_current_agent(request)
            messages.success(
                request,
                "Your membership has been deactivated. Active posts have been removed from the board.",
            )
            return redirect("feed")
    else:
        form = AccountDeletionForm()

    context = build_account_context(request, deletion_form=form)
    context["page_title"] = "Delete Membership"
    return render(request, "account_delete_confirm.html", context)


def workspace(request):
    if get_current_agent(request) is None:
        messages.error(request, "Log in to access the Whisper workspace.")
        return redirect("landing")
    return render(
        request,
        "workspace.html",
        build_workspace_context(request, section=request.GET.get("section", "collections")),
    )


def workspace_collection_detail(request, collection_id):
    current_agent = get_current_agent(request)
    if current_agent is None:
        messages.error(request, "Log in to access the Whisper workspace.")
        return redirect("landing")
    collection = get_object_or_404(
        Collection.objects.select_related("saved_filter"),
        pk=collection_id,
        agent=current_agent,
    )
    collection_items = get_collection_items(collection)
    if request.method == "POST":
        if "clear_alert" in request.POST:
            CollectionFilter.objects.filter(collection=collection).delete()
            collection.notifications_enabled = False
            collection.save(update_fields=["notifications_enabled"])
            messages.success(request, "Collection alert cleared")
            return redirect("workspace_collection_detail", collection_id=collection.id)

        form = CollectionAlertSettingsForm(request.POST)
        if form.is_valid():
            collection.name = form.cleaned_data["name"]
            collection.notifications_enabled = form.cleaned_data["notifications_enabled"]
            collection.save(update_fields=["name", "notifications_enabled"])
            filter_params = build_filter_query_from_cleaned_data(form.cleaned_data)
            if filter_params:
                CollectionFilter.objects.update_or_create(
                    collection=collection,
                    defaults={
                        "city": form.cleaned_data.get("city", ""),
                        "stage": form.cleaned_data.get("stage", ""),
                        "min_beds": form.cleaned_data.get("min_beds"),
                        "min_baths": form.cleaned_data.get("min_baths"),
                        "min_price": form.cleaned_data.get("min_price"),
                        "max_price": form.cleaned_data.get("max_price"),
                    },
                )
            elif not collection.notifications_enabled:
                CollectionFilter.objects.filter(collection=collection).delete()
            else:
                messages.error(request, "Add at least one filter before enabling collection alerts.")
                return redirect("workspace_collection_detail", collection_id=collection.id)
            messages.success(request, "Collection alert updated")
            return redirect("workspace_collection_detail", collection_id=collection.id)
        messages.error(request, "Enter a valid collection setup before saving.")
        return redirect("workspace_collection_detail", collection_id=collection.id)

    return render(
        request,
        "workspace_collection_detail.html",
        {
            "collection": collection,
            "collection_summary": build_collection_summary(collection),
            "collection_alert_form": CollectionAlertSettingsForm(
                initial={
                    "name": collection.name,
                    "notifications_enabled": collection.notifications_enabled,
                    **get_collection_filter_data(collection),
                }
            ),
            "current_agent": current_agent,
            "current_agent_initials": get_agent_initials(current_agent),
            "current_agent_membership": get_agent_membership_level(current_agent),
            "unread_notification_count": get_unread_notification_count(current_agent),
            "collection_items": collection_items,
        },
    )


def add_saved_listing_to_collection(request, listing_id):
    if request.method != "POST":
        return redirect("workspace")

    agent = get_current_agent(request)
    redirect_target = request.POST.get("next") or f'{reverse("workspace")}?section=saved'

    if agent is None:
        messages.error(request, "We can't update collections yet because no agent account is available.")
        return redirect(redirect_target)

    saved_listing = SavedListing.objects.filter(agent=agent, listing_id=listing_id).select_related("listing").first()
    if saved_listing is None:
        messages.error(request, "That saved opportunity is no longer available in your workspace.")
        return redirect(redirect_target)

    form = AssignSavedListingForm(request.POST, agent=agent, prefix=f"saved-{listing_id}")
    if not form.is_valid():
        messages.error(request, "Choose a collection or enter a new collection name.")
        return redirect(redirect_target)

    collection_choice = form.cleaned_data.get("collection_choice")
    new_collection_name = form.cleaned_data.get("new_collection_name")

    if collection_choice == "__new__":
        collection, _ = Collection.objects.get_or_create(agent=agent, name=new_collection_name)
    else:
        collection = Collection.objects.filter(agent=agent, pk=collection_choice).first()

    if collection is None:
        messages.error(request, "Choose a valid collection before adding this listing.")
        return redirect(redirect_target)

    CollectionItem.objects.get_or_create(collection=collection, listing=saved_listing.listing)
    messages.success(request, "Added to collection")
    return redirect(redirect_target)


def build_listing_title(*, beds, baths, city):
    return f"{beds} Bed / {baths} Bath in {city}"


def populate_listing_certification_timestamps(listing):
    now = timezone.now()

    if listing.seller_direction_certified and not listing.seller_direction_certified_at:
        listing.seller_direction_certified_at = now
    if listing.agent_compliance_acknowledged and not listing.agent_compliance_acknowledged_at:
        listing.agent_compliance_acknowledged_at = now
    if listing.information_accuracy_certified and not listing.information_accuracy_certified_at:
        listing.information_accuracy_certified_at = now
    if listing.stage == Listing.Stage.PRIVATE and listing.private_marketing_certified and not listing.private_marketing_certified_at:
        listing.private_marketing_certified_at = now


def render_post_listing(request, form, template_name, show_listing_form=False):
    if template_name == "feed.html":
        return render(
            request,
            template_name,
            get_feed_context(request, form=form, show_listing_form=show_listing_form),
        )
    return render(
        request,
        template_name,
        {
            "form": form,
            "post_form_action": "/post/?source=feed",
            "form_action": request.path,
            "page_title": "Share an Opportunity",
            "submit_label": "Share Opportunity",
            "show_listing_form": show_listing_form,
        },
    )


def post_listing(request):
    template_name = "feed.html" if request.GET.get("source") == "feed" else "post_listing.html"
    if get_current_agent(request) is None:
        messages.error(request, "Log in to share an opportunity in Whisper.")
        return redirect("landing")

    if request.method == "POST":
        form = ListingForm(request.POST)
        if form.is_valid():
            agent = get_current_agent(request)
            if agent is None:
                messages.error(
                    request,
                    "We can't share this opportunity yet because no agent account is available. Please add an agent and try again.",
                )
                return render_post_listing(
                    request,
                    form,
                    template_name,
                    show_listing_form=template_name == "feed.html",
                )
            if not agent.phones.exists():
                messages.error(request, "Add a phone number to your account before sharing opportunities.")
                return redirect(f'{reverse("account")}#contact-settings')

            listing = form.save(commit=False)
            listing.agent = agent
            listing.is_active = True
            listing.title = build_listing_title(
                beds=listing.beds,
                baths=listing.baths,
                city=listing.city,
            )
            populate_listing_certification_timestamps(listing)
            listing.save()
            send_collection_match_alerts_for_listing(listing)
            return redirect("feed")
    else:
        form = ListingForm()

    return render_post_listing(
        request,
        form,
        template_name,
        show_listing_form=template_name == "feed.html",
    )


def edit_listing(request, listing_id):
    current_agent = get_current_agent(request)
    redirect_target = f'{reverse("workspace")}?section=posts'
    listing = Listing.objects.filter(pk=listing_id).select_related("agent").first()

    if current_agent is None or listing is None or listing.agent_id != current_agent.id:
        messages.error(request, "Only the opportunity owner can edit this post.")
        return redirect(redirect_target)

    if request.method == "POST":
        form = ListingForm(request.POST, instance=listing)
        if form.is_valid():
            updated_listing = form.save(commit=False)
            updated_listing.agent = listing.agent
            updated_listing.title = build_listing_title(
                beds=updated_listing.beds,
                baths=updated_listing.baths,
                city=updated_listing.city,
            )
            populate_listing_certification_timestamps(updated_listing)
            updated_listing.save()
            if updated_listing.is_active:
                send_collection_match_alerts_for_listing(updated_listing)
            messages.success(request, "Opportunity updated")
            return redirect(redirect_target)
    else:
        form = ListingForm(instance=listing)

    return render(
        request,
        "post_listing.html",
        {
            "form": form,
            "form_action": request.path,
            "page_title": "Edit Opportunity",
            "submit_label": "Save Changes",
            "show_listing_form": False,
        },
    )


def remove_listing(request, listing_id):
    if request.method != "POST":
        return redirect(f'{reverse("workspace")}?section=posts')

    current_agent = get_current_agent(request)
    redirect_target = f'{reverse("workspace")}?section=posts'
    listing = Listing.objects.filter(pk=listing_id).select_related("agent").first()

    if current_agent is None or listing is None or listing.agent_id != current_agent.id:
        messages.error(request, "Only the opportunity owner can remove this post.")
        return redirect(redirect_target)

    listing.mark_removed_by_agent()
    messages.success(request, "Opportunity removed")
    return redirect(redirect_target)


def confirm_listing_from_email(request, token):
    try:
        payload = load_signed_listing_token(token, "confirm")
    except signing.BadSignature:
        return render(
            request,
            "listing_checkin_result.html",
            {"message": "This opportunity confirmation link is invalid or has expired."},
            status=400,
        )

    listing = Listing.objects.filter(pk=payload["listing_id"], agent_id=payload["agent_id"]).first()
    if listing is None:
        return render(
            request,
            "listing_checkin_result.html",
            {"message": "This opportunity could not be found."},
            status=404,
        )

    if is_listing_stale(listing):
        if listing.is_active and listing.status == Listing.Status.ACTIVE and listing.removed_at is None:
            listing.mark_stale()
        return render(
            request,
            "listing_checkin_result.html",
            {
                "title": "Opportunity Check-In Expired",
                "message": "This refresh link has expired because the opportunity is no longer active on Whisper.",
            },
            status=410,
        )

    was_inactive = not listing.is_active
    listing.mark_confirmed()
    if was_inactive:
        send_collection_match_alerts_for_listing(listing)
    return render(
        request,
        "listing_checkin_result.html",
        {
            "title": "Opportunity Refreshed",
            "message": "Thanks! Your opportunity has been refreshed.",
        },
    )


def remove_listing_from_email(request, token):
    try:
        payload = load_signed_listing_token(token, "remove")
    except signing.BadSignature:
        return render(
            request,
            "listing_checkin_result.html",
            {"message": "This opportunity removal link is invalid or has expired."},
            status=400,
        )

    listing = Listing.objects.filter(pk=payload["listing_id"], agent_id=payload["agent_id"]).first()
    if listing is None:
        return render(
            request,
            "listing_checkin_result.html",
            {"message": "This opportunity could not be found."},
            status=404,
        )

    if is_listing_stale(listing):
        if listing.is_active and listing.status == Listing.Status.ACTIVE and listing.removed_at is None:
            listing.mark_stale()
        return render(
            request,
            "listing_checkin_result.html",
            {
                "title": "Opportunity Check-In Expired",
                "message": "This refresh link has expired because the opportunity is no longer active on Whisper.",
            },
            status=410,
        )

    listing.mark_removed_by_agent()
    return render(
        request,
        "listing_checkin_result.html",
        {
            "title": "Opportunity Removed",
            "message": "Your opportunity has been removed.",
        },
    )


def move_listing_to_mls_from_email(request, token):
    try:
        payload = load_signed_listing_token(token, "moved_to_mls")
    except signing.BadSignature:
        return render(
            request,
            "listing_checkin_result.html",
            {"message": "This moved-to-MLS link is invalid or has expired."},
            status=400,
        )

    listing = Listing.objects.filter(pk=payload["listing_id"], agent_id=payload["agent_id"]).first()
    if listing is None:
        return render(
            request,
            "listing_checkin_result.html",
            {"message": "This opportunity could not be found."},
            status=404,
        )

    if is_listing_stale(listing):
        if listing.is_active and listing.status == Listing.Status.ACTIVE and listing.removed_at is None:
            listing.mark_stale()
        return render(
            request,
            "listing_checkin_result.html",
            {
                "title": "Opportunity Check-In Expired",
                "message": "This refresh link has expired because the opportunity is no longer active on Whisper.",
            },
            status=410,
        )

    listing.mark_moved_to_mls()
    return render(
        request,
        "listing_checkin_result.html",
        {
            "title": "Opportunity Removed",
            "message": "Your opportunity was marked as moved to MLS and removed from Whisper.",
        },
    )


def save_collection(request):
    if request.method != "POST":
        return redirect("feed")

    agent = get_current_agent(request)
    if agent is None:
        messages.error(
            request,
            "We can't save this filter yet because no agent account is available. Please add an agent and try again.",
        )
        return redirect("feed")

    collection_form = CollectionAlertSaveForm(request.POST, agent=agent)
    filter_form = FeedFilterForm(request.POST)
    raw_filter_params = {
        key: value
        for key, value in request.POST.items()
        if key in {"city", "stage", "min_beds", "min_baths", "min_price", "max_price"} and value
    }
    redirect_url = f'{reverse("feed")}?{urlencode(raw_filter_params)}' if raw_filter_params else reverse("feed")

    if not collection_form.is_valid() or not filter_form.is_valid():
        messages.error(request, "Choose a collection or create a new one with valid filter values.")
        return redirect(redirect_url)

    filter_params = build_filter_query_from_cleaned_data(filter_form.cleaned_data)
    if not filter_params:
        messages.error(request, "Apply at least one filter before saving a collection.")
        return redirect(redirect_url)

    collection_choice = collection_form.cleaned_data["collection_choice"]
    if collection_choice == "__new__":
        collection, _ = Collection.objects.get_or_create(
            agent=agent,
            name=collection_form.cleaned_data["new_collection_name"],
        )
    else:
        collection = Collection.objects.filter(agent=agent, pk=collection_choice).first()
        if collection is None:
            messages.error(request, "Choose a valid collection before saving this alert.")
            return redirect(redirect_url)

    collection.notifications_enabled = collection_form.cleaned_data["notifications_enabled"]
    collection.save(update_fields=["notifications_enabled"])
    CollectionFilter.objects.update_or_create(
        collection=collection,
        defaults={
            "city": filter_form.cleaned_data.get("city", ""),
            "stage": filter_form.cleaned_data.get("stage", ""),
            "min_beds": filter_form.cleaned_data.get("min_beds"),
            "min_baths": filter_form.cleaned_data.get("min_baths"),
            "min_price": filter_form.cleaned_data.get("min_price"),
            "max_price": filter_form.cleaned_data.get("max_price"),
        },
    )
    messages.success(
        request,
        "Collection alert saved" if collection.notifications_enabled else "Collection saved",
    )

    query = urlencode(filter_params)
    redirect_url = f'{reverse("feed")}?{query}' if query else reverse("feed")
    return redirect(redirect_url)


def toggle_saved_listing(request, listing_id):
    if request.method != "POST":
        return redirect("feed")

    agent = get_current_agent(request)
    redirect_target = request.POST.get("next") or reverse("feed")

    if agent is None:
        messages.error(
            request,
            "We can't save this opportunity yet because no agent account is available. Please add an agent and try again.",
        )
        return redirect(redirect_target)

    listing = Listing.objects.filter(pk=listing_id, is_active=True).first()
    if listing is None:
        messages.error(request, "That opportunity is no longer available.")
        return redirect(redirect_target)

    saved_listing, created = SavedListing.objects.get_or_create(
        agent=agent,
        listing=listing,
    )
    if not created:
        saved_listing.delete()

    return redirect(redirect_target)


def update_notification_preferences(request):
    if request.method != "POST":
        return redirect("account")

    current_agent = get_current_agent(request)
    if current_agent is None:
        messages.error(request, "No active agent account is available.")
        return redirect("landing")

    form = NotificationPreferencesForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter valid notification preferences.")
        return redirect("account")

    current_agent.freshness_reminder_emails = form.cleaned_data["freshness_reminder_emails"]
    current_agent.collection_match_emails = form.cleaned_data["collection_match_emails"]
    current_agent.product_update_emails = form.cleaned_data["product_update_emails"]
    current_agent.save(
        update_fields=[
            "freshness_reminder_emails",
            "collection_match_emails",
            "product_update_emails",
        ]
    )
    messages.success(request, "Notification preferences updated")
    return redirect("account")


def confirm_listing_availability(request, listing_id):
    if request.method != "POST":
        return redirect("feed")

    agent = get_current_agent(request)
    redirect_target = request.POST.get("next") or reverse("feed")

    if agent is None:
        messages.error(
            request,
            "We can't confirm this opportunity yet because no agent account is available. Please add an agent and try again.",
        )
        return redirect(redirect_target)

    listing = Listing.objects.filter(pk=listing_id, agent=agent, is_active=True).first()
    if listing is None:
        messages.error(request, "Only the opportunity owner can confirm availability.")
        return redirect(redirect_target)

    listing.mark_confirmed()
    return redirect(redirect_target)
