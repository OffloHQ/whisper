from django.contrib import admin
from .models import AccessRequest, AgentUser, Collection, CollectionFilter, CollectionItem, EmailNotificationLog, InAppNotification, Listing, SavedListing


@admin.register(AgentUser)
class AgentUserAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "email",
        "state",
        "license_number",
        "signup_status",
        "is_verified",
        "freshness_reminder_emails",
        "collection_match_emails",
        "created_at",
    )
    list_filter = ("signup_status", "is_verified", "state", "freshness_reminder_emails", "collection_match_emails", "created_at")
    search_fields = ("name", "email", "license_number", "state")


@admin.register(AccessRequest)
class AccessRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "email", "status")


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
    list_display = ("name", "agent", "notifications_enabled", "created_at")
    list_filter = ("created_at", "agent", "notifications_enabled")
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


@admin.register(EmailNotificationLog)
class EmailNotificationLogAdmin(admin.ModelAdmin):
    list_display = ("notification_type", "recipient_email", "agent", "collection", "listing", "sent_at")
    list_filter = ("notification_type", "sent_at")
    search_fields = ("recipient_email", "agent__email", "collection__name", "listing__title")


@admin.register(InAppNotification)
class InAppNotificationAdmin(admin.ModelAdmin):
    list_display = ("notification_type", "agent", "collection", "listing", "is_read", "created_at")
    list_filter = ("notification_type", "is_read", "created_at")
    search_fields = ("agent__email", "collection__name", "listing__title", "title", "body")
