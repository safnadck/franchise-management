from django import forms
from django.contrib.auth.models import User, Group, Permission
from common.djangoapps.student.models import UserProfile, CourseEnrollment
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from .models import Franchise, UserFranchise, Batch, BatchFeeManagement, StudentFeeManagement, Installment, Payment, InstallmentTemplate, CourseFee


class RoleForm(forms.ModelForm):
    permissions = forms.ModelMultipleChoiceField(
        queryset=Permission.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label='Permissions'
    )

    class Meta:
        model = Group
        fields = ['name', 'permissions']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Enter role name', 'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Filter only application permissions
        self.fields['permissions'].queryset = Permission.objects.filter(
            content_type__app_label='application'
        ).order_by('content_type__model', 'codename')

    def save(self, commit=True):
        group = super().save(commit=False)
        if commit:
            group.save()
            group.permissions.set(self.cleaned_data['permissions'])
        return group

class FranchiseForm(forms.ModelForm):
    class Meta:
        model = Franchise
        fields = ['name', 'coordinator', 'contact_no', 'email', 'location', 'registration_date']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Name'}),
            'coordinator': forms.TextInput(attrs={'placeholder': 'coordinator'}),
            'contact_no': forms.TextInput(attrs={'placeholder': 'Contact '}),
            'email': forms.EmailInput(attrs={'placeholder': 'Email ID'}),
            'location': forms.TextInput(attrs={'placeholder': 'Location'}),
            'registration_date': forms.DateInput(attrs={'type': 'text', 'placeholder': 'Reg Date', 'onfocus': "(this.type='date')", 'onblur': "(this.type='text')"}),
        }


class FranchiseUserRegistrationForm(forms.ModelForm):
    full_name = forms.CharField(max_length=100, label='Full Name', required=True)
    email = forms.EmailField(label='Email', required=True)
    phone = forms.CharField(max_length=20, label='Phone', required=True)
    password = forms.CharField(widget=forms.PasswordInput, label='Password')
    mailing_address = forms.CharField(max_length=255, label='Mailing Address', required=True)

    class Meta:
        model = User
        fields = ['username']

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("Email already exists")
        return email

    def save(self, franchise=None, batch=None, commit=True):
        user = super().save(commit=False)
        name_parts = self.cleaned_data['full_name'].split(' ', 1)
        user.first_name = name_parts[0]
        user.last_name = name_parts[1] if len(name_parts) > 1 else ''
        user.email = self.cleaned_data['email']
        user.set_password(self.cleaned_data['password'])

        if commit:
            user.save()

            profile, created = UserProfile.objects.get_or_create(user=user)
            profile.name = self.cleaned_data['full_name']
            profile.phone_number = self.cleaned_data['phone']
            profile.mailing_address = self.cleaned_data['mailing_address']
            profile.save()

            if franchise:
                from .models import UserFranchise
                user_franchise = UserFranchise(user=user, franchise=franchise)
                if batch:
                    user_franchise.batch = batch
                user_franchise.save()
                # Set username to registration_number after it's generated
                user.username = user_franchise.registration_number
                user.save()

        return user

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set a default username that will be overridden
        if not self.instance.pk:
            self.fields['username'].initial = 'temp_username'

        # Filter batches based on selected franchises dynamically
        # This will be handled by JavaScript in the template


class BatchForm(forms.ModelForm):
    discount = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=False,
        widget=forms.NumberInput(attrs={'placeholder': 'Discount Amount', 'step': '0.01', 'min': '0'})
    )

    class Meta:
        model = Batch
        fields = ['batch_no', 'course']
        widgets = {
            'batch_no': forms.TextInput(attrs={'placeholder': 'Batch Number'}),
            'course': forms.Select(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['course'].label_from_instance = lambda obj: obj.display_name or str(obj.id)


class InstallmentTemplateForm(forms.Form):
    amount = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'step': '0.01', 'placeholder': 'Amount'})
    )
    repayment_period_days = forms.IntegerField(
        widget=forms.NumberInput(attrs={'placeholder': 'Repayment Period (days)'})
    )


class BatchFeeManagementForm(forms.ModelForm):
    class Meta:
        model = BatchFeeManagement
        fields = ['discount']
        widgets = {
            'discount': forms.NumberInput(attrs={'placeholder': 'Discount Amount'}),
        }


class StudentFeeManagementForm(forms.ModelForm):
    class Meta:
        model = StudentFeeManagement
        fields = ['remaining_amount']


class StudentDiscountForm(forms.Form):
    additional_discount = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=False,
        widget=forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'placeholder': 'Additional Discount Amount'}),
        label='Additional Discount Amount (₹)'
    )


class InstallmentForm(forms.ModelForm):
    class Meta:
        model = Installment
        fields = ['amount', 'payed_amount', 'due_date', 'status', 'repayment_period_days']
        widgets = {
            'due_date': forms.DateInput(attrs={'type': 'date'}),
            'repayment_period_days': forms.NumberInput(attrs={'min': '0'}),
            'status': forms.Select(choices=Installment.STATUS_CHOICES),
            'payed_amount': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
        }


class EditInstallmentForm(forms.ModelForm):
    class Meta:
        model = Installment
        fields = ['amount', 'payed_amount', 'repayment_period_days']
        widgets = {
            'amount': forms.NumberInput(attrs={'step': '0.01', 'min': '0.01', 'required': 'required'}),
            'payed_amount': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'required': 'required'}),
            'repayment_period_days': forms.NumberInput(attrs={'min': '0', 'required': 'required'}),
        }

    def clean_amount(self):
        amount = self.cleaned_data.get('amount')
        if amount is not None and amount <= 0:
            raise forms.ValidationError("Amount must be greater than 0")

        # Check if amount is less than already paid amount
        payed_amount = self.cleaned_data.get('payed_amount') or self.instance.payed_amount
        if payed_amount and amount < payed_amount:
            raise forms.ValidationError(f"Amount cannot be less than the paid amount of ₹{payed_amount}")

        return amount

    def clean_repayment_period_days(self):
        days = self.cleaned_data.get('repayment_period_days')
        if days is not None and days < 0:  # allow 0
            raise forms.ValidationError("Repayment period cannot be negative")
        return days

class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ['payment_date', 'amount']
        widgets = {
            'payment_date': forms.DateInput(attrs={'type': 'date'}),
        }


class StudentEditForm(forms.ModelForm):
    phone_number = forms.CharField(max_length=20, label='Phone Number', required=False)
    mailing_address = forms.CharField(max_length=255, label='Mailing Address', required=False)
    new_password = forms.CharField(widget=forms.PasswordInput(attrs={'id': 'new_password'}), required=False, label='New Password')


    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email']
        widgets = {
            'username': forms.TextInput(attrs={'readonly': 'readonly', 'title': 'Username is automatically set to registration number'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and hasattr(self.instance, 'profile'):
            self.fields['phone_number'].initial = self.instance.profile.phone_number
            self.fields['mailing_address'].initial = self.instance.profile.mailing_address

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            profile, created = UserProfile.objects.get_or_create(user=user)
            profile.phone_number = self.cleaned_data.get('phone_number')
            profile.mailing_address = self.cleaned_data.get('mailing_address')
            profile.save()
            new_password = self.cleaned_data.get('new_password')
            if new_password:
                user.set_password(new_password)
                user.save()
        return user


class CourseFeeForm(forms.ModelForm):
    class Meta:
        model = CourseFee
        fields = ['fee']
        widgets = {
            'fee': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'placeholder': 'Fee Amount'}),
        }


class UserSearchForm(forms.Form):
    search_query = forms.CharField(
        max_length=100,
        required=False,
        label='Search',
        widget=forms.TextInput(attrs={'placeholder': 'Enter registration number, email, phone, or name'})
    )


class SpecialAccessRegistrationForm(forms.Form):
    user = forms.ModelChoiceField(
        queryset=User.objects.all(),
        label='Select User',
        widget=forms.Select(attrs={'class': 'form-control'})
    )


class SpecialAccessUserRegistrationForm(forms.ModelForm):
    full_name = forms.CharField(max_length=100, label='Full Name', required=True)
    email = forms.EmailField(label='Email', required=True)
    phone = forms.CharField(max_length=20, label='Phone', required=True)
    password = forms.CharField(widget=forms.PasswordInput, label='Password')
    mailing_address = forms.CharField(max_length=255, label='Mailing Address', required=True)
    group = forms.ModelChoiceField(queryset=Group.objects.all(), label='Group (Role)', required=True)
    allowed_franchises = forms.ModelMultipleChoiceField(
        queryset=Franchise.objects.all(),
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-control'}),
        required=False,
        label='Allowed Franchises'
    )
    allowed_batches = forms.ModelMultipleChoiceField(
        queryset=Batch.objects.all(),
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-control'}),
        required=False,
        label='Allowed Batches'
    )

    class Meta:
        model = User
        fields = ['username']

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("Email already exists")
        return email

    def clean_username(self):
        username = self.cleaned_data.get('username')
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("Username already exists")
        return username

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Store franchise-batch relationship for template
        self.batch_franchise_map = {}
        for batch in Batch.objects.all().select_related('franchise'):
            self.batch_franchise_map[str(batch.id)] = str(batch.franchise.id)

    def save(self, commit=True):
        user = super().save(commit=False)
        name_parts = self.cleaned_data['full_name'].split(' ', 1)
        user.first_name = name_parts[0]
        user.last_name = name_parts[1] if len(name_parts) > 1 else ''
        user.email = self.cleaned_data['email']
        user.set_password(self.cleaned_data['password'])

        if commit:
            user.save()
            user.groups.add(self.cleaned_data['group'])

            profile, created = UserProfile.objects.get_or_create(user=user)
            profile.name = self.cleaned_data['full_name']
            profile.phone_number = self.cleaned_data['phone']
            profile.mailing_address = self.cleaned_data['mailing_address']
            profile.save()

        return user


class EditSpecialAccessUserForm(forms.Form):
    allowed_franchises = forms.ModelMultipleChoiceField(
        queryset=Franchise.objects.all(),
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-control'}),
        required=False,
        label='Allowed Franchises'
    )
    allowed_batches = forms.ModelMultipleChoiceField(
        queryset=Batch.objects.all(),
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-control'}),
        required=False,
        label='Allowed Batches'
    )

    def __init__(self, *args, **kwargs):
        self.special_access_user = kwargs.pop('special_access_user', None)
        super().__init__(*args, **kwargs)

        if self.special_access_user:
            self.fields['allowed_franchises'].initial = self.special_access_user.allowed_franchises.all()
            self.fields['allowed_batches'].initial = self.special_access_user.allowed_batches.all()

        # Store franchise-batch relationship for template
        self.batch_franchise_map = {}
        for batch in Batch.objects.all().select_related('franchise'):
            self.batch_franchise_map[str(batch.id)] = str(batch.franchise.id)
