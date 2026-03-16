from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from listings.retention import get_cleanup_querysets


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
        cleanup_targets = get_cleanup_querysets(now=now)
        action_label = "would delete" if dry_run else "deleted"

        self.stdout.write("Processing Whisper cleanup categories:")
        total = 0
        for label, queryset in cleanup_targets.items():
            count = queryset.count()
            total += count
            self.stdout.write(f"{label}: {count} {action_label}")
            if not dry_run and count:
                queryset.delete()

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
