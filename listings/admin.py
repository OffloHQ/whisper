from django.contrib import admin

from .intake import (
    activate_waitlist_requests,
    approve_access_request,
    approve_manual_review_requests,
    reject_access_request,
    reject_manual_review_requests,
)
from .models import AccessRequest, AgentUser, Collection, CollectionItem, EmailNotificationLog, InAppNotification, Listing, SavedListing


@admin.register(AgentUser)
class AgentUserAdmin(admin.ModelAdmin):
    list_display = ("id", "email")


@admin.register(AccessRequest)
class AccessRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "email", "status", "queue_type", "decision_status", "reason_code")
    list_filter = ("queue_type", "decision_status", "reason_code")
    actions = ("approve_manual_review_requests", "activate_waitlisted_requests", "reject_manual_review_requests")

    @admin.action(description="Manually verify selected manual-review requests and send next-step email")
    def approve_manual_review_requests(self, request, queryset):
        approved_count, skipped_count, failed_count, _, _ = approve_manual_review_requests(
            access_requests=queryset,
            reviewed_by=request.user,
            request=request,
        )
        self.message_user(
            request,
            f"Processed {approved_count} manual-review verification approval(s). Skipped {skipped_count}. Failed {failed_count}.",
        )

    @admin.action(description="Send waitlist update to selected waitlist requests")
    def activate_waitlisted_requests(self, request, queryset):
        activated_count, skipped_count, failed_count, _, _ = activate_waitlist_requests(
            access_requests=queryset,
            reviewed_by=request.user,
            request=request,
        )
        self.message_user(
            request,
            f"Sent waitlist updates to {activated_count} request(s). Skipped {skipped_count}. Failed {failed_count}.",
        )

    @admin.action(description="Record verification failure for selected manual-review requests")
    def reject_manual_review_requests(self, request, queryset):
        rejected_count, skipped_count, failed_count, _, _ = reject_manual_review_requests(
            access_requests=queryset,
            reviewed_by=request.user,
        )
        self.message_user(
            request,
            f"Recorded {rejected_count} manual-review rejection(s). Skipped {skipped_count}. Failed {failed_count}.",
        )


@admin.register(Listing)
class ListingAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "created_at")


@admin.register(Collection)
class CollectionAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "created_at")


@admin.register(SavedListing)
class SavedListingAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at")


@admin.register(CollectionItem)
class CollectionItemAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at")


@admin.register(EmailNotificationLog)
class EmailNotificationLogAdmin(admin.ModelAdmin):
    list_display = ("id", "sent_at")


@admin.register(InAppNotification)
class InAppNotificationAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at")
