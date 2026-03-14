from django import forms

from .models import Collection, Listing
from .utils import PRICE_INPUT_ERROR, get_town_area_choices, parse_price_input


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
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["property_type"].required = False
        self.fields["description"].required = False
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
        return self.cleaned_data["state"].strip().upper()


class SignupContactForm(forms.Form):
    phone_number = forms.CharField(label="Phone number", max_length=30)
    brokerage = forms.CharField(label="Brokerage", max_length=255, required=False)
    city = forms.CharField(label="City", max_length=120, required=False)
