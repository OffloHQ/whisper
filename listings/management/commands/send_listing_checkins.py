from django.core.management.base import BaseCommand

from django.utils import timezone

from listings.checkins import (
    deactivate_stale_listings,
    get_freshness_state_label,
    get_listings_requiring_checkin,
    group_listings_by_agent_email,
    send_grouped_listing_checkins,
)


class Command(BaseCommand):
    help = "Send grouped listing check-in reminders to agents."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print which agents and listings would receive check-ins without sending email.",
        )

    def handle(self, *args, **options):
        now = timezone.now()
        if options["dry_run"]:
            due_listings = get_listings_requiring_checkin(now=now, deactivate_stale_on_run=False)
            grouped = group_listings_by_agent_email(due_listings)
            if not grouped:
                self.stdout.write("Dry run: no listing check-in emails would be sent.")
                return

            self.stdout.write("Dry run: listing check-in emails would be sent to:")
            for agent_email, listings in grouped.items():
                self.stdout.write(f"- {agent_email}")
                for listing in listings:
                    self.stdout.write(
                        f"  * {listing.title} | {listing.city} | {listing.get_stage_display()} | "
                        f"{get_freshness_state_label(listing, now=now)}"
                    )
            return

        stale_count = deactivate_stale_listings(now=now)
        sent_count = send_grouped_listing_checkins(now=now, deactivate_stale_on_run=False)
        self.stdout.write(self.style.SUCCESS(f"Sent {sent_count} grouped listing check-in email(s)."))
        if stale_count:
            self.stdout.write(self.style.WARNING(f"Deactivated {stale_count} stale listing(s)."))
