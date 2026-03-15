from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from listings.collection_alerts import get_collections_requiring_match_alert_for_listing, send_collection_match_alerts_for_listing
from listings.models import Listing


class Command(BaseCommand):
    help = "Send collection match alert emails and in-app notifications for recent active listings."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be sent without sending anything.",
        )
        parser.add_argument(
            "--days-back",
            type=int,
            default=getattr(settings, "COLLECTION_ALERT_FALLBACK_LOOKBACK_DAYS", 7),
            help="Look back this many days for active listings.",
        )

    def handle(self, *args, **options):
        now = timezone.now()
        lookback_start = now - timedelta(days=options["days_back"])
        listings = (
            Listing.objects.filter(
                is_active=True,
                status=Listing.Status.ACTIVE,
                created_at__gte=lookback_start,
            )
            .select_related("agent")
            .order_by("-created_at", "-pk")
        )
        total = 0
        if options["dry_run"]:
            for listing in listings:
                collections = get_collections_requiring_match_alert_for_listing(listing)
                if not collections:
                    continue
                total += len({collection.agent_id for collection in collections})
                self.stdout.write(f"{listing.title} ({listing.city})")
                for collection in collections:
                    self.stdout.write(f"  - {collection.agent.email} via {collection.name}")
            if total == 0:
                self.stdout.write("Dry run: no collection alerts would be sent.")
            else:
                self.stdout.write(f"Dry run complete. {total} grouped alert email(s) would be sent.")
            return

        for listing in listings:
            total += send_collection_match_alerts_for_listing(listing)
        self.stdout.write(self.style.SUCCESS(f"Sent {total} collection alert email(s)."))
