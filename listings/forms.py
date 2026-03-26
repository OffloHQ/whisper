import re

from django import forms

from .models import Collection, Listing
from .utils import PRICE_INPUT_ERROR, get_town_area_choices, parse_price_input
from .verification.utils import normalize_state_code


IDENTIFIER_BLOCKING_ERROR = (
    "Please remove exact property identifiers, direct contact details, and links. "
    "Share only high-level opportunity information in Whisper."
)

ADDRESS_LIKE_PATTERNS = [
    re.compile(
        r"\b\d{1,5}(?:-\d{1,5})?\s+(?:(?:north|south|east|west)\s+)?(?:[a-z0-9]+\s+){0,4}"
        r"(?:street|st|avenue|ave|road|rd|drive|dr|lane|ln|boulevard|blvd|place|pl|court|ct|terrace|ter|way|parkway|pkwy)\b\.?",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:apt|apartment|unit|suite|ste)\s*#?\s*[a-z0-9-]+\b", re.IGNORECASE),
    re.compile(r"#\s*\d+[a-z]?\b", re.IGNORECASE),
    re.compile(r"\bmls\s*#?\s*\d{4,}\b", re.IGNORECASE),
    re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE),
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"(?:\+?1[\s.\-]?)?(?:\(?\d{3}\)?[\s.\-]?)\d{3}[\s.\-]?\d{4}\b"),
]


def contains_blocked_identifier(text):
    if not text:
        return False
    return any(pattern.search(text) for pattern in ADDRESS_LIKE_PATTERNS)


class ListingForm(forms.ModelForm):
    city = forms.ChoiceField(label="Town / Area", choices=get_town_area_choices())
    price_min = forms.CharField()
    price_max = forms.CharField()

    class Meta:
        model = Listing
        fields = [
            "city",
            "beds",
            "baths",
            "price_min",
            "price_max",
            "stage",
            "property_type",
            "description",
            "seller_direction_certified",
            "agent_compliance_acknowledged",
            "information_accuracy_certified",
            "private_marketing_certified",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["property_type"].required = False
        self.fields["description"].required = False
        self.fields["seller_direction_certified"].label = (
            "I certify that the seller has directed this sharing approach and that this opportunity is being shared in accordance with that direction."
        )
        self.fields["agent_compliance_acknowledged"].label = (
            "I understand I am responsible for complying with my brokerage, MLS, and local rules."
        )
        self.fields["information_accuracy_certified"].label = (
            "I certify that the information submitted is accurate to the best of my knowledge."
        )
        self.fields["private_marketing_certified"].label = (
            "I certify that the seller has directed that this opportunity be shared privately and not disseminated on the MLS at this time."
        )
        self.fields["price_min"].widget.attrs["inputmode"] = "decimal"
        self.fields["price_max"].widget.attrs["inputmode"] = "decimal"

    def clean_price_min(self):
        return self.clean_price_value("price_min")

    def clean_price_max(self):
        return self.clean_price_value("price_max")

    def clean_price_value(self, field_name):
        try:
            return parse_price_input(self.cleaned_data[field_name])
        except ValueError as exc:
            raise forms.ValidationError(str(exc))

    def clean(self):
        cleaned_data = super().clean()
        stage = cleaned_data.get("stage")

        for field_name in ("property_type", "description"):
            if contains_blocked_identifier(cleaned_data.get(field_name, "")):
                self.add_error(field_name, IDENTIFIER_BLOCKING_ERROR)

        if not cleaned_data.get("seller_direction_certified"):
            self.add_error("seller_direction_certified", "You must certify seller direction before posting.")
        if not cleaned_data.get("agent_compliance_acknowledged"):
            self.add_error("agent_compliance_acknowledged", "You must acknowledge compliance responsibility before posting.")
        if not cleaned_data.get("information_accuracy_certified"):
            self.add_error(
                "information_accuracy_certified",
                "You must certify that the submitted information is accurate before sharing this opportunity.",
            )
        if stage == Listing.Stage.PRIVATE and not cleaned_data.get("private_marketing_certified"):
            self.add_error("private_marketing_certified", "You must certify seller direction for a private listing before posting.")

        return cleaned_data


class FeedFilterForm(forms.Form):
    city = forms.ChoiceField(
        required=False,
        label="Town / Area",
        choices=get_town_area_choices(include_blank=True),
    )
    stage = forms.ChoiceField(
        required=False,
        choices=[("", "Any stage"), *Listing.Stage.choices],
    )
    min_beds = forms.IntegerField(required=False, min_value=0, label="Minimum beds")
    min_baths = forms.DecimalField(required=False, min_value=0, label="Minimum baths", decimal_places=1, max_digits=3)
    min_price = forms.CharField(required=False, label="Minimum price")
    max_price = forms.CharField(required=False, label="Maximum price")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["min_price"].widget.attrs["inputmode"] = "decimal"
        self.fields["max_price"].widget.attrs["inputmode"] = "decimal"
        self.fields["min_price"].widget.attrs["placeholder"] = "850K"
        self.fields["max_price"].widget.attrs["placeholder"] = "2M"

    def clean_min_price(self):
        return self.clean_price_value("min_price")

    def clean_max_price(self):
        return self.clean_price_value("max_price")

    def clean_price_value(self, field_name):
        raw_value = self.cleaned_data.get(field_name)
        if not raw_value:
            return None

        try:
            return parse_price_input(raw_value)
        except ValueError:
            raise forms.ValidationError(PRICE_INPUT_ERROR)


class CollectionForm(forms.Form):
    name = forms.CharField(max_length=255, label="Collection name")


class CollectionAlertSaveForm(FeedFilterForm):
    collection_choice = forms.ChoiceField(required=False, choices=(), label="Collection", widget=forms.RadioSelect)
    new_collection_name = forms.CharField(required=False, max_length=255, label="New collection")
    notifications_enabled = forms.BooleanField(required=False, initial=False, label="Enable collection alert")

    def __init__(self, *args, **kwargs):
        agent = kwargs.pop("agent", None)
        super().__init__(*args, **kwargs)
        choices = []
        if agent is not None:
            choices.extend(
                (str(collection.id), collection.name)
                for collection in Collection.objects.filter(agent=agent).order_by("name", "-created_at")
            )
        choices.append(("__new__", "+ New Collection"))
        self.fields["collection_choice"].choices = choices

    def clean(self):
        cleaned_data = super().clean()
        collection_choice = cleaned_data.get("collection_choice")
        new_collection_name = cleaned_data.get("new_collection_name", "").strip()

        if not collection_choice:
            raise forms.ValidationError("Choose an existing collection or create a new one.")
        if collection_choice == "__new__" and not new_collection_name:
            raise forms.ValidationError("Enter a name for the new collection.")

        cleaned_data["new_collection_name"] = new_collection_name
        return cleaned_data


class CollectionAlertSettingsForm(FeedFilterForm):
    name = forms.CharField(max_length=255, label="Collection name")
    notifications_enabled = forms.BooleanField(required=False, label="Email me when a new opportunity matches")


class NotificationPreferencesForm(forms.Form):
    freshness_reminder_emails = forms.BooleanField(required=False, label="Freshness reminder emails")
    collection_match_emails = forms.BooleanField(required=False, label="Collection match emails")
    product_update_emails = forms.BooleanField(required=False, label="Product update emails")


class AssignSavedListingForm(forms.Form):
    collection_choice = forms.ChoiceField(required=False, choices=(), label="Collection", widget=forms.RadioSelect)
    new_collection_name = forms.CharField(required=False, max_length=255, label="New collection")

    def __init__(self, *args, **kwargs):
        agent = kwargs.pop("agent", None)
        super().__init__(*args, **kwargs)
        choices = []
        if agent is not None:
            choices.extend(
                (str(collection.id), collection.name)
                for collection in Collection.objects.filter(agent=agent).order_by("name", "-created_at")
            )
        choices.append(("__new__", "+ New Collection"))
        self.fields["collection_choice"].choices = choices

    def clean(self):
        cleaned_data = super().clean()
        collection_choice = cleaned_data.get("collection_choice")
        new_collection_name = cleaned_data.get("new_collection_name", "").strip()

        if not collection_choice:
            raise forms.ValidationError("Choose a collection or select + New Collection.")
        if collection_choice == "__new__" and not new_collection_name:
            raise forms.ValidationError("Enter a name for the new collection.")

        cleaned_data["new_collection_name"] = new_collection_name
        return cleaned_data


class AccountDeletionForm(forms.Form):
    confirm_text = forms.CharField(label='Type "DELETE" to confirm')

    def clean_confirm_text(self):
        value = self.cleaned_data["confirm_text"].strip()
        if value != "DELETE":
            raise forms.ValidationError('Type "DELETE" exactly to confirm.')
        return value


class AgentEmailForm(forms.Form):
    email = forms.EmailField(label="Add email")


class AgentPhoneForm(forms.Form):
    phone_number = forms.CharField(label="Phone number", max_length=30)


class RequestAccessForm(forms.Form):
    email = forms.EmailField(label="Email")


class EmailEntryForm(forms.Form):
    email = forms.EmailField(
        label="",
        widget=forms.EmailInput(
            attrs={
                "placeholder": "Enter your email",
                "autocomplete": "email",
            }
        ),
    )


class SignupIdentityForm(forms.Form):
    full_name = forms.CharField(label="Full name", max_length=255)
    state = forms.CharField(label="State", max_length=32)
    license_number = forms.CharField(label="State license number", max_length=100)

    def clean_state(self):
        return normalize_state_code(self.cleaned_data["state"])


class SignupContactForm(forms.Form):
    phone_number = forms.CharField(label="Phone number", max_length=30)
    brokerage = forms.CharField(label="Brokerage", max_length=255, required=False)
    city = forms.CharField(label="City", max_length=120, required=False)


class LegalAcceptanceForm(forms.Form):
    accept_legal = forms.BooleanField(
        required=False,
        label="I agree to the Terms of Use and Privacy Policy.",
    )

    def clean_accept_legal(self):
        accepted = self.cleaned_data.get("accept_legal")
        if not accepted:
            raise forms.ValidationError("You must agree to the Terms of Use and Privacy Policy to continue.")
        return accepted
