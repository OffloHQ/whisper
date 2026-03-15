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

        total = 0
        for label, queryset in cleanup_targets.items():
            count = queryset.count()
            total += count
            self.stdout.write(f"{label}: {count} {action_label}")
            if not dry_run and count:
                queryset.delete()

        if dry_run:
            self.stdout.write(f"Dry run complete. {total} records would be deleted.")
        else:
            self.stdout.write(self.style.SUCCESS(f"Cleanup complete. Deleted {total} records."))
