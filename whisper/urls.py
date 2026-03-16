"""
URL configuration for whisper project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path

from listings import internal_views, views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('intake/login/', internal_views.intake_login, name='intake_login'),
    path('intake/logout/', internal_views.intake_logout, name='intake_logout'),
    path('intake/', internal_views.intake_home, name='intake_home'),
    path('intake/manual-review/', internal_views.intake_manual_review, name='intake_manual_review'),
    path('intake/waitlist/', internal_views.intake_waitlist, name='intake_waitlist'),
    path('intake/request/<int:request_id>/', internal_views.intake_request_detail, name='intake_request_detail'),
    path('intake/request/<int:request_id>/verify/', internal_views.intake_verify_request, name='intake_verify_request'),
    path('intake/request/<int:request_id>/reject/', internal_views.intake_reject_request, name='intake_reject_request'),
    path('', views.landing, name='landing'),
    path('board/', views.feed, name='feed'),
    path('notifications/', views.notifications, name='notifications'),
    path('notifications/<int:notification_id>/open/', views.open_notification, name='open_notification'),
    path('sign-in/<str:token>/', views.consume_auth_access_token, name='consume_auth_access_token'),
    path('sign-in/qr/<str:token>/status/', views.qr_sign_in_status, name='qr_sign_in_status'),
    path('request-access/', views.request_access, name='request_access'),
    path('legal/', views.legal_acceptance, name='legal_acceptance'),
    path('terms/', views.terms_of_use, name='terms_of_use'),
    path('privacy/', views.privacy_policy, name='privacy_policy'),
    path('signup/continue/<str:token>/', views.signup_contact_continue, name='signup_contact_continue'),
    path('signup/contact/', views.signup_contact, name='signup_contact'),
    path('signup/<str:token>/', views.signup_identity, name='signup_identity'),
    path('account/', views.account, name='account'),
    path('account/notifications/', views.update_notification_preferences, name='update_notification_preferences'),
    path('account/logout/', views.logout_account, name='logout_account'),
    path('account/delete/', views.delete_account, name='delete_account'),
    path('account/emails/add/', views.add_agent_email, name='add_agent_email'),
    path('account/contact-visibility/', views.update_contact_visibility, name='update_contact_visibility'),
    path('account/emails/verify/<str:token>/', views.verify_agent_email, name='verify_agent_email'),
    path('account/emails/<int:email_id>/primary/', views.make_primary_agent_email, name='make_primary_agent_email'),
    path('account/emails/<int:email_id>/remove/', views.remove_agent_email, name='remove_agent_email'),
    path('account/phones/add/', views.add_agent_phone, name='add_agent_phone'),
    path('account/phones/<int:phone_id>/update/', views.update_agent_phone, name='update_agent_phone'),
    path('account/phones/<int:phone_id>/delete/', views.delete_agent_phone, name='delete_agent_phone'),
    path('confirm-listing/<str:token>/', views.confirm_listing_from_email, name='confirm_listing_from_email'),
    path('remove-listing/<str:token>/', views.remove_listing_from_email, name='remove_listing_from_email'),
    path('workspace/', views.workspace, name='workspace'),
    path('workspace/collections/<int:collection_id>/', views.workspace_collection_detail, name='workspace_collection_detail'),
    path('workspace/posts/<int:listing_id>/edit/', views.edit_listing, name='edit_listing'),
    path('workspace/posts/<int:listing_id>/remove/', views.remove_listing, name='remove_listing'),
    path('workspace/saved/<int:listing_id>/add-to-collection/', views.add_saved_listing_to_collection, name='add_saved_listing_to_collection'),
    path('post/', views.post_listing, name='post_listing'),
    path('collections/save/', views.save_collection, name='save_collection'),
    path('listings/<int:listing_id>/save-toggle/', views.toggle_saved_listing, name='toggle_saved_listing'),
    path('listings/<int:listing_id>/confirm/', views.confirm_listing_availability, name='confirm_listing_availability'),
]
