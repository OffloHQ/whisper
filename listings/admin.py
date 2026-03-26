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

    @admin.action(description="Activate selected manual-review requests")
    def approve_manual_review_requests(self, request, queryset):
        approved_count, skipped_count, failed_count, _, _ = approve_manual_review_requests(
            access_requests=queryset,
            reviewed_by=request.user,
            request=request,
        )
        self.message_user(
            request,
            f"Approved {approved_count} manual-review request(s). Skipped {skipped_count}. Failed {failed_count}.",
        )

    @admin.action(description="Activate selected waitlist requests")
    def activate_waitlisted_requests(self, request, queryset):
        activated_count, skipped_count, failed_count, _, _ = activate_waitlist_requests(
            access_requests=queryset,
            reviewed_by=request.user,
            request=request,
        )
        self.message_user(
            request,
            f"Activated {activated_count} waitlist request(s). Skipped {skipped_count}. Failed {failed_count}.",
        )

    @admin.action(description="Reject selected manual-review requests")
    def reject_manual_review_requests(self, request, queryset):
        rejected_count, skipped_count, failed_count, _, _ = reject_manual_review_requests(
            access_requests=queryset,
            reviewed_by=request.user,
        )
        self.message_user(
            request,
            f"Rejected {rejected_count} manual-review request(s). Skipped {skipped_count}. Failed {failed_count}.",
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
