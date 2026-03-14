from django.contrib import admin
from django.contrib import messages

from .intake import approve_access_request, reject_access_request
from .models import AccessRequest, AgentUser, Collection, CollectionFilter, CollectionItem, Listing, SavedListing


@admin.register(AgentUser)
class AgentUserAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "state", "license_number", "signup_status", "is_verified", "created_at")
    list_filter = ("signup_status", "is_verified", "state", "created_at")
    search_fields = ("name", "email", "license_number", "state")


@admin.register(AccessRequest)
class AccessRequestAdmin(admin.ModelAdmin):
    list_display = (
        "email",
        "full_name",
        "state",
        "county",
        "borough",
        "license_number",
        "queue_type",
        "decision_status",
        "reason_code",
        "verification_status",
        "verification_provider",
        "verification_attempted_at",
        "reviewed_at",
        "approval_email_sent_at",
        "rejection_email_sent_at",
        "waitlist_email_sent_at",
        "last_notification_type",
        "last_notification_sent_at",
    )
    list_filter = (
        "status",
        "queue_type",
        "decision_status",
        "reason_code",
        "verification_status",
        "verification_provider",
        "requires_manual_review",
        "state",
    )
    search_fields = (
        "email",
        "full_name",
        "license_number",
        "state",
        "county",
        "borough",
        "market_area",
        "verification_reason",
    )
    readonly_fields = (
        "verification_attempted_at",
        "verified_at",
        "reviewed_at",
        "reviewed_by",
        "approval_email_sent_at",
        "rejection_email_sent_at",
        "waitlist_email_sent_at",
        "last_notification_type",
        "last_notification_sent_at",
    )
    actions = ("approve_manual_review_requests", "reject_manual_review_requests")

    @admin.action(description="Verify selected manual review requests")
    def approve_manual_review_requests(self, request, queryset):
        approved_count = 0
        for access_request in queryset:
            if access_request.queue_type != AccessRequest.QueueType.MANUAL_REVIEW:
                continue
            _, _, notification_sent = approve_access_request(
                access_request=access_request,
                reviewed_by=request.user,
                request=request,
                decision_reason=access_request.verification_reason,
            )
            approved_count += 1
            self.message_user(
                request,
                (
                    f"Approved {access_request.email}. Continuation link sent."
                    if notification_sent
                    else f"Approved {access_request.email}, but notification email could not be sent."
                ),
                level=messages.SUCCESS if notification_sent else messages.WARNING,
            )
        if approved_count == 0:
            self.message_user(request, "No manual review records were approved.", level=messages.WARNING)

    @admin.action(description="Reject selected manual review requests")
    def reject_manual_review_requests(self, request, queryset):
        rejected_count = 0
        for access_request in queryset:
            if access_request.queue_type != AccessRequest.QueueType.MANUAL_REVIEW:
                continue
            notification_sent = reject_access_request(
                access_request=access_request,
                reviewed_by=request.user,
                decision_reason=access_request.verification_reason,
            )
            rejected_count += 1
            if not notification_sent:
                self.message_user(
                    request,
                    f"Rejected {access_request.email}, but notification email could not be sent.",
                    level=messages.WARNING,
                )
        if rejected_count:
            self.message_user(request, f"Rejected {rejected_count} manual review request(s).", level=messages.SUCCESS)
        else:
            self.message_user(request, "No manual review records were rejected.", level=messages.WARNING)


@admin.register(Listing)
class ListingAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "agent",
        "city",
        "property_type",
        "stage",
        "price_min",
        "price_max",
        "is_active",
        "created_at",
    )
    list_filter = ("stage", "property_type", "city", "is_active", "created_at")
    search_fields = ("title", "city", "description", "agent__name", "agent__email")


class CollectionFilterInline(admin.StackedInline):
    model = CollectionFilter
    extra = 0


class CollectionItemInline(admin.TabularInline):
    model = CollectionItem
    extra = 0


@admin.register(Collection)
class CollectionAdmin(admin.ModelAdmin):
    list_display = ("name", "agent", "created_at")
    list_filter = ("created_at", "agent")
    search_fields = ("name", "agent__name", "agent__email")
    inlines = [CollectionFilterInline, CollectionItemInline]


@admin.register(SavedListing)
class SavedListingAdmin(admin.ModelAdmin):
    list_display = ("agent", "listing", "created_at")
    list_filter = ("created_at", "agent")
    search_fields = ("agent__name", "agent__email", "listing__title", "listing__city")


@admin.register(CollectionItem)
class CollectionItemAdmin(admin.ModelAdmin):
    list_display = ("collection", "listing", "created_at")
    list_filter = ("created_at", "collection")
    search_fields = ("collection__name", "listing__title", "listing__city")
