from django.contrib import admin
from .models import AccessRequest, AgentUser, Collection, CollectionItem, EmailNotificationLog, InAppNotification, Listing, SavedListing


@admin.register(AgentUser)
class AgentUserAdmin(admin.ModelAdmin):
    list_display = ("id", "email")


@admin.register(AccessRequest)
class AccessRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "email", "status")


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
