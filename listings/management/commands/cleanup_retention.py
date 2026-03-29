from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from listings.email_flows import send_access_request_signup_reminder_email
from listings.retention import get_cleanup_querysets
from listings.retention import get_incomplete_access_request_reminder_querysets


class Command(BaseCommand):
    help = "Delete expired short-lived tokens and stale onboarding records."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be deleted without deleting anything.",
        )

    def handle(self, *args, **options):
        now = timezone.now()
        dry_run = options["dry_run"]
        reminder_targets = get_incomplete_access_request_reminder_querysets(now=now)
        cleanup_targets = get_cleanup_querysets(now=now)
        action_label = "would delete" if dry_run else "deleted"

        self.stdout.write("Processing Whisper incomplete access-request reminders:")
        reminder_specs = [
            (
                "access_requests.incomplete_signup_first_reminder",
                "signup_reminder_sent_at",
                settings.INCOMPLETE_ACCESS_REQUEST_FIRST_REMINDER_DAYS,
            ),
            (
                "access_requests.incomplete_signup_second_reminder",
                "signup_final_reminder_sent_at",
                settings.INCOMPLETE_ACCESS_REQUEST_SECOND_REMINDER_DAYS,
            ),
        ]
        for label, field_name, reminder_day in reminder_specs:
            queryset = reminder_targets[label]
            count = queryset.count()
            if dry_run:
                self.stdout.write(f"{label}: {count} reminder emails would send")
                continue
            sent_count = 0
            failed_count = 0
            for access_request in queryset.iterator():
                try:
                    send_access_request_signup_reminder_email(
                        access_request=access_request,
                        reminder_day=reminder_day,
                    )
                except Exception:
                    failed_count += 1
                    self.stderr.write(
                        self.style.WARNING(f"{label}: failed to send reminder for {access_request.email}")
                    )
                else:
                    setattr(access_request, field_name, now)
                    access_request.save(update_fields=[field_name, "updated_at"])
                    sent_count += 1
            self.stdout.write(f"{label}: sent {sent_count}, failed {failed_count}")

        self.stdout.write("Processing Whisper cleanup categories:")
        total = 0
        for label, queryset in cleanup_targets.items():
            count = queryset.count()
            total += count
            self.stdout.write(f"{label}: {count} {action_label}")
            if not dry_run and count:
                queryset.delete()

        purge_queryset = reminder_targets["access_requests.incomplete_signup_purge"]
        purge_count = purge_queryset.count()
        total += purge_count
        self.stdout.write(f"access_requests.incomplete_signup_purge: {purge_count} {action_label}")
        if not dry_run and purge_count:
            purge_queryset.delete()

        if getattr(settings, "ENABLE_SESSION_RETENTION_CLEANUP", True):
            # Django's clearsessions only deletes expired session rows. It does not
            # support a dry-run preview, so we surface that explicitly here.
            if dry_run:
                self.stdout.write("sessions: cleanup enabled via clearsessions (dry-run cannot preview count)")
            else:
                call_command("clearsessions")
                self.stdout.write("sessions: cleanup executed via clearsessions")
        else:
            self.stdout.write("sessions: cleanup skipped (disabled)")

        if dry_run:
            self.stdout.write(f"Dry run complete. {total} records would be deleted.")
        else:
            self.stdout.write(self.style.SUCCESS(f"Cleanup complete. Deleted {total} records."))
