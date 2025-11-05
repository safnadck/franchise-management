from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.models import User, Group, Permission
from django.db import models
from django.http import JsonResponse, HttpResponseForbidden
from .forms import FranchiseForm, BatchForm, FranchiseUserRegistrationForm, BatchFeeManagementForm, StudentFeeManagementForm, InstallmentForm, EditInstallmentForm, PaymentForm, StudentEditForm,StudentDiscountForm, SpecialAccessRegistrationForm, SpecialAccessUserRegistrationForm, RoleForm, EditSpecialAccessUserForm
from .models import Franchise, UserFranchise, Batch, BatchFeeManagement, StudentFeeManagement, Installment, InstallmentTemplate, CourseFee, SpecialAccessUser, Payment
from django.contrib.auth.decorators import login_required, user_passes_test
from collections import defaultdict
from django.db.models import Count, Case, When, Value, IntegerField
from django.urls import reverse
from django.forms import modelformset_factory
from datetime import timedelta, datetime
from django.utils import timezone
from django.db import OperationalError, transaction
from time import sleep
from django.db.models import Q
from decimal import Decimal
from django.core.paginator import Paginator, PageNotAnInteger, EmptyPage
from django.db.models import Sum
from django.db.models.functions import TruncMonth
from django.core.exceptions import PermissionDenied
from django.contrib import messages
import json

from common.djangoapps.student.models import UserProfile
from common.djangoapps.student.models import CourseEnrollment
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview

# ==============================
# PERMISSION & ROLE MANAGEMENT
# ==============================

def get_permission_type_from_group(group_name):
    """
    Map group name to permission type based on keywords
    """
    group_name_lower = group_name.lower()
    if 'franchise' in group_name_lower:
        return 'franchise_management'
    elif 'fee' in group_name_lower:
        return 'fee_management'
    elif 'student' in group_name_lower:
        return 'student_management'
    elif 'report' in group_name_lower:
        return 'reporting'
    else:
        return 'all'  # Default to all if no specific keywords found

def has_permission(user, permission_codename):
    """
    Check if user has specific permission through groups or special access
    """
    if user.is_superuser:
        return True

    # Check if user has special access (required for non-superusers to access the system)
    try:
        special_access = SpecialAccessUser.objects.get(user=user)
    except SpecialAccessUser.DoesNotExist:
        return False

    # Check group permissions (Django's built-in permissions)
    if user.has_perm(f'application.{permission_codename}'):
        return True

    return False

def permission_required(permission_codename):
    """
    Decorator to check specific permissions
    """
    def decorator(view_func):
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('login')
            
            if has_permission(request.user, permission_codename):
                return view_func(request, *args, **kwargs)
            
            return render(request, 'application/access_denied.html', {
                'message': f"You don't have permission to access this page. Required permission: {permission_codename}"
            }, status=403)
        return _wrapped_view
    return decorator

def role_required(role_name):
    """
    Decorator to check if user has specific role (group)
    """
    def decorator(view_func):
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('login')
            
            if request.user.is_superuser or request.user.groups.filter(name=role_name).exists():
                return view_func(request, *args, **kwargs)
            
            return render(request, 'application/access_denied.html', {
                'message': f"Access denied. Required role: {role_name}"
            }, status=403)
        return _wrapped_view
    return decorator

def special_access_required(required_permission=None):
    """
    Allows access to superusers or users who have the required special access permission
    """
    def decorator(view_func):
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('login')

            # Superusers always allowed
            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)

            # Check if user has special access
            try:
                special_access = SpecialAccessUser.objects.get(user=request.user)
                # If no permission_type field exists, allow access for backward compatibility
                if not hasattr(special_access, 'permission_type'):
                    return view_func(request, *args, **kwargs)

                if required_permission:
                    if special_access.permission_type in ['all', required_permission]:
                        return view_func(request, *args, **kwargs)
                else:
                    # No specific permission required, just having special access is enough
                    return view_func(request, *args, **kwargs)

            except SpecialAccessUser.DoesNotExist:
                pass

            # Otherwise deny access
            return render(request, 'application/access_denied.html', {
                'message': f"Special access required: {required_permission or 'any'}"
            }, status=403)
        return _wrapped_view
    return decorator

def get_allowed_franchises(user):
    """
    Get the list of franchises the user is allowed to access
    """
    if user.is_superuser:
        return Franchise.objects.all()

    try:
        special_access = SpecialAccessUser.objects.get(user=user)
        if special_access.allowed_franchises.exists():
            return special_access.allowed_franchises.all()
        else:
            # If no specific franchises allowed, allow all
            return Franchise.objects.all()
    except SpecialAccessUser.DoesNotExist:
        return Franchise.objects.none()

def get_allowed_batches(user):
    """
    Get the list of batches the user is allowed to access
    """
    if user.is_superuser:
        return Batch.objects.all()

    try:
        special_access = SpecialAccessUser.objects.get(user=user)
        if special_access.allowed_batches.exists():
            return special_access.allowed_batches.all()
        else:
            # If no specific batches allowed, allow all
            return Batch.objects.all()
    except SpecialAccessUser.DoesNotExist:
        return Batch.objects.none()

def superuser_required(view_func):
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not request.user.is_superuser:
            return render(request, 'application/access_denied.html', {
                'message': "Superuser access required"
            }, status=403)
        return view_func(request, *args, **kwargs)
    return _wrapped_view

def superuser_or_special_required(view_func):
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            if request.is_ajax():
                return JsonResponse({'success': False, 'error': 'Authentication required'}, status=401)
            return redirect('login')
        if not (request.user.is_superuser or SpecialAccessUser.objects.filter(user=request.user).exists()):
            if request.is_ajax():
                return JsonResponse({'success': False, 'error': 'Superuser or special access required'}, status=403)
            return render(request, 'application/access_denied.html', {
                'message': "Superuser or special access required"
            }, status=403)
        return view_func(request, *args, **kwargs)
    return _wrapped_view


VIEW_PERMISSIONS = {
    'homepage': 'view_dashboard',
    'fee_report': 'view_reports',
    'franchise_fees_report': 'view_reports',
    'monthly_fees_report': 'view_reports',
    'combined_fees_report': 'view_reports',
    'course_fee_list': 'change_coursefee',  # Use actual model permission
    'fee_reminders': 'view_reports',
    'inactive_users': 'view_reports',
    'franchise_list': 'view_franchise',
    'franchise_register': 'add_franchise',
    'franchise_edit': 'change_franchise',
    'franchise_report': 'view_franchise',
    'batch_create': 'add_batch',
    'batch_students': 'view_userfranchise',  # Use appropriate model
    'student_detail': 'view_userfranchise',
    'edit_student_details': 'change_userfranchise',
    'user_register': 'add_userfranchise',
    'batch_user_register': 'add_userfranchise',
    'enroll_existing_user': 'add_userfranchise',
    'batch_fee_management': 'change_batchfeemanagement',
    'student_fee_management': 'change_studentfeemanagement',
    'edit_installment_setup': 'change_installment',
    'receipt_search': 'process_payment',
    'receipt_detail': 'process_payment',
    'special_access_register': 'add_specialaccessuser',
    'roles': 'auth.change_group',  # Django's group permission
    'student_counts': 'view_reports',
    'special_user_dashboard': 'view_dashboard',
    'student_profile': 'view_profile',
    'enroll_existing_user_general': 'add_userfranchise',
}
# ==============================
# VIEWS WITH PERMISSION CHECKS
# ==============================

@login_required
def homepage(request):
    # Check permission using the mapping
    if not has_permission(request.user, VIEW_PERMISSIONS['homepage']):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to access the dashboard"
        }, status=403)

    # Get only allowed franchises and batches
    allowed_franchises = get_allowed_franchises(request.user)
    allowed_batches = get_allowed_batches(request.user)

    # Check for redirection: if user has add_userfranchise permission and exactly one allowed franchise
    # But don't redirect special access users - they should see the homepage
    if (not SpecialAccessUser.objects.filter(user=request.user).exists() and
        has_permission(request.user, 'add_userfranchise') and allowed_franchises.count() == 1):
        franchise = allowed_franchises.first()
        return redirect('application:franchise_report', pk=franchise.pk)

    # Count only allowed franchises
    total_franchises = allowed_franchises.count()

    # Count students only from allowed franchises and batches
    student_queryset = UserFranchise.objects.filter(franchise__in=allowed_franchises)
    if allowed_batches.exists():
        student_queryset = student_queryset.filter(batch__in=allowed_batches)
    total_students = student_queryset.values('user').distinct().count()

    # For courses, you might want to filter by allowed franchises too
    # This depends on your business logic
    total_courses = CourseOverview.objects.count()  # Or filter if needed

    return render(request, 'application/homepage.html', {
        'total_franchises': total_franchises,
        'total_students': total_students,
        'total_courses': total_courses,
        'allowed_franchises': allowed_franchises,  # Optional: pass for debugging
    })

@login_required
def fee_report(request):
    if not has_permission(request.user, VIEW_PERMISSIONS['fee_report']):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to view fee reports"
        }, status=403)

    # ... rest of your fee_report function code ...
    franchise_id = request.GET.get('franchise_id')
    batch_id = request.GET.get('batch_id')
    allowed_franchises = get_allowed_franchises(request.user)
    allowed_batches = get_allowed_batches(request.user)
    all_franchises = allowed_franchises
    today = timezone.now().date()

    # Global totals (always full for allowed franchises and batches)
    installment_queryset = Installment.objects.filter(
        student_fee_management__user_franchise__franchise__in=allowed_franchises
    )
    if allowed_batches.exists():
        installment_queryset = installment_queryset.filter(
            student_fee_management__user_franchise__batch__in=allowed_batches
        )
    total_fees = installment_queryset.aggregate(total=Sum('amount'))['total'] or 0
    total_received = installment_queryset.aggregate(total=Sum('payed_amount'))['total'] or 0
    total_pending = total_fees - total_received
    overdue_installments = installment_queryset.filter(due_date__lt=today).exclude(status='paid')
    total_overdue = sum(inst.amount - inst.payed_amount for inst in overdue_installments) or 0

    if batch_id:
        # Filter by batch and its franchise
        try:
            batch = Batch.objects.get(id=batch_id)
            if batch not in allowed_batches:
                franchises = Franchise.objects.none()
            else:
                franchise = batch.franchise
                franchises = allowed_franchises.filter(id=franchise.id).prefetch_related(
                    'batches__userfranchise_set__fee_management__installments'
                )
        except Batch.DoesNotExist:
            franchises = Franchise.objects.none()
    elif franchise_id:
        franchises = allowed_franchises.filter(id=franchise_id).prefetch_related(
            'batches__userfranchise_set__fee_management__installments'
        )
    else:
        franchises = allowed_franchises.prefetch_related(
            'batches__userfranchise_set__fee_management__installments'
        ).all()

    # Breakdown data
    franchise_data = []
    for franchise in franchises:
        franchise_received = 0
        franchise_pending = 0
        franchise_overdue = 0
        batches_data = []
        batches = franchise.batches.all()
        if batch_id:
            batches = batches.filter(id=batch_id)
        # Filter batches by allowed_batches if specified
        if allowed_batches.exists():
            batches = batches.filter(id__in=allowed_batches)
        for batch in batches:
            batch_received = 0
            batch_pending = 0
            batch_overdue = 0
            for user_franchise in batch.userfranchise_set.all():
                try:
                    student_fee = user_franchise.fee_management
                    installments = student_fee.installments.all()
                    batch_received += sum(inst.payed_amount for inst in installments)
                    batch_pending += sum(inst.amount - inst.payed_amount for inst in installments)
                    batch_overdue += sum(inst.amount - inst.payed_amount for inst in installments.filter(due_date__lt=today).exclude(status='paid'))
                except StudentFeeManagement.DoesNotExist:
                    continue
            batches_data.append({
                'batch': batch,
                'received': batch_received,
                'pending': batch_pending,
                'overdue': batch_overdue,
            })
            franchise_received += batch_received
            franchise_pending += batch_pending
            franchise_overdue += batch_overdue
        franchise_data.append({
            'franchise': franchise,
            'batches': batches_data,
            'received': franchise_received,
            'pending': franchise_pending,
            'overdue': franchise_overdue,
        })

    # Monthly fees due and collected
    monthly_due = Installment.objects.annotate(month=TruncMonth('due_date')).values('month').annotate(due=Sum('amount')).order_by('month')
    monthly_collected = Installment.objects.filter(status='paid').annotate(month=TruncMonth('payment_date')).values('month').annotate(collected=Sum('payed_amount')).order_by('month')

    # Combine into a dict for easy access
    monthly_data = {}
    for item in monthly_due:
        monthly_data[item['month']] = {'due': item['due'], 'collected': 0}
    for item in monthly_collected:
        if item['month'] in monthly_data:
            monthly_data[item['month']]['collected'] = item['collected']
        else:
            monthly_data[item['month']] = {'due': 0, 'collected': item['collected']}

    # Convert to list sorted by month
    monthly_fees = [{'month': k, 'due': v['due'], 'collected': v['collected'], 'total': v['due'] + v['collected']} for k, v in sorted(monthly_data.items())]

    return render(request, 'application/fee_report.html', {
        'total_fees': total_fees,
        'total_pending': total_pending,
        'total_overdue': total_overdue,
        'total_received': total_received,
        'franchise_data': franchise_data,
        'all_franchises': all_franchises,
        'selected_franchise_id': franchise_id,
        'selected_batch_id': batch_id,
        'monthly_fees': monthly_fees,
    })

# Continue with other views following the same pattern...
# For brevity, I'll show the pattern for a few more views

@login_required
def franchise_list(request):
    if not has_permission(request.user, VIEW_PERMISSIONS['franchise_list']):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to view franchises"
        }, status=403)

    franchises = get_allowed_franchises(request.user)
    search_query = request.GET.get('search', '').strip()

    if search_query:
        # Franchises that match the search query
        matching_franchises = franchises.filter(
            Q(name__icontains=search_query) |
            Q(location__icontains=search_query) |
            Q(coordinator__icontains=search_query) |
            Q(contact_no__icontains=search_query) |
            Q(email__icontains=search_query)
        )
        # Franchises that do not match
        non_matching_franchises = franchises.exclude(
            Q(name__icontains=search_query) |
            Q(location__icontains=search_query) |
            Q(coordinator__icontains=search_query) |
            Q(contact_no__icontains=search_query) |
            Q(email__icontains=search_query)
        )
        # Combine: matching first, then non-matching
        franchises = list(matching_franchises) + list(non_matching_franchises)
    else:
        franchises = list(franchises)

    return render(request, 'application/franchise_management.html', {
        'franchises': franchises,
        'search_query': search_query
    })

@login_required
def franchise_register(request):
    if not has_permission(request.user, VIEW_PERMISSIONS['franchise_register']):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to add franchises"
        }, status=403)
    
    if request.method == "POST":
        form = FranchiseForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('application:franchise_list')
    else:
        form = FranchiseForm()
    
    return render(request, 'application/franchise_register.html', {'form': form})

@login_required
def user_register(request):
    if not has_permission(request.user, VIEW_PERMISSIONS['user_register']):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to register users"
        }, status=403)

    if request.method == "POST":
        form = FranchiseUserRegistrationForm(request.POST)
        if form.is_valid():
            franchise_id = request.POST.get('franchise')
            batch_id = request.POST.get('batch')
            try:
                franchise = Franchise.objects.get(pk=franchise_id)
                batch = Batch.objects.get(pk=batch_id)
                if batch.franchise != franchise:
                    form.add_error(None, 'Selected batch does not belong to the selected franchise.')
                else:
                    # Check if franchise is allowed for special user
                    allowed_franchises = get_allowed_franchises(request.user)
                    if franchise not in allowed_franchises:
                        form.add_error(None, 'You do not have permission to register users for this franchise.')
                    else:
                        user = form.save(franchise=franchise, batch=batch, commit=True)
                        CourseEnrollment.enroll(user, batch.course.id)

                        # 📨 Send welcome + enrollment emails
                        try:
                            send_welcome_email(user)
                            send_enrollment_email(user, batch.course.display_name)
                            messages.success(request, f"User {user.username} registered and welcome mail sent successfully.")
                        except Exception as e:
                            messages.warning(request, f"User registered successfully, but failed to send email: {e}")

                        return redirect('application:homepage')
            except (Franchise.DoesNotExist, Batch.DoesNotExist, ValueError):
                form.add_error(None, 'Invalid franchise or batch selected.')
    else:
        form = FranchiseUserRegistrationForm()

    franchises = get_allowed_franchises(request.user)

    return render(request, 'application/user_register.html', {
        'form': form,
        'franchises': franchises,
    })

@login_required
def receipt_search(request):
    if not has_permission(request.user, VIEW_PERMISSIONS['receipt_search']):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to process payments"
        }, status=403)
    
    search_query = request.GET.get('search_query', '').strip()
    user_franchises = []

    if search_query:
        user_franchises = UserFranchise.objects.select_related('user', 'batch').filter(
            Q(registration_number__icontains=search_query) |
            Q(user__email__icontains=search_query) |
            Q(user__first_name__icontains=search_query) |
            Q(user__last_name__icontains=search_query) |
            Q(user__username__icontains=search_query)
        )

        user_profiles = UserProfile.objects.filter(phone_number__icontains=search_query)
        user_ids_from_profile = [up.user_id for up in user_profiles]
        user_franchises = user_franchises | UserFranchise.objects.filter(user_id__in=user_ids_from_profile)
        user_franchises = user_franchises.distinct()

    return render(request, 'application/receipt_search.html', {
        'search_query': search_query,
        'user_franchises': user_franchises,
    })

# ==============================
# ROLE MANAGEMENT VIEWS
# ==============================

@login_required
@superuser_or_special_required
def roles(request):
    if not has_permission(request.user, 'auth.change_group'):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to manage roles"
        }, status=403)
        
    if request.method == 'POST':
        form = RoleForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Role created successfully!')
            return redirect('application:roles')
    else:
        form = RoleForm()

    # Get all groups and permissions
    groups = Group.objects.all().prefetch_related('permissions')
    # Define custom order for models
    custom_order = [
        'specialaccessuser',
        'franchise',
        'batch',
        'batchfeemanagement',
        'userfranchise',
        'studentfeemanagement',
        'installment',
        'installmenttemplate',
        'coursefee',
        'payment',
    ]
    # Create a case expression for ordering
    order_case = Case(
        *[When(content_type__model=model, then=Value(i)) for i, model in enumerate(custom_order)],
        default=Value(len(custom_order)),
        output_field=IntegerField()
    )
    permissions = Permission.objects.filter(content_type__app_label='application').annotate(
        custom_order=order_case
    ).order_by('custom_order', 'content_type__model', 'codename')

    return render(request, 'application/roles.html', {
        'form': form,
        'groups': groups,
        'permissions': permissions,
    })

@login_required
@superuser_or_special_required
def special_access_register(request):
    if not has_permission(request.user, 'manage_users'):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to manage special access"
        }, status=403)

    if request.method == 'POST':
        form = SpecialAccessUserRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save(commit=True)
            # Determine permission_type based on selected group
            selected_group = form.cleaned_data['group']
            permission_type = get_permission_type_from_group(selected_group.name)
            # Automatically grant special access to newly registered user
            special_access = SpecialAccessUser.objects.create(
                user=user,
                granted_by=request.user,
                permission_type=permission_type
            )
            # Set allowed franchises and batches
            allowed_franchises = form.cleaned_data.get('allowed_franchises')
            allowed_batches = form.cleaned_data.get('allowed_batches')
            if allowed_franchises:
                special_access.allowed_franchises.set(allowed_franchises)
            if allowed_batches:
                special_access.allowed_batches.set(allowed_batches)
            messages.success(request, f'User {user.username} registered with special access ({permission_type}).')
            return redirect('application:special_access_register')
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")

    form = SpecialAccessUserRegistrationForm()
    special_users = SpecialAccessUser.objects.select_related('user', 'granted_by').order_by('-granted_at')
    # Get batch-franchise map
    batch_franchise_map = {}
    for batch in Batch.objects.all().select_related('franchise'):
        if batch.franchise:
            batch_franchise_map[str(batch.id)] = str(batch.franchise.id)
    return render(request, 'application/special_access_register.html', {
        'form': form,
        'special_users': special_users,
        'batch_franchise_map': json.dumps(batch_franchise_map),
    })


@login_required
@superuser_or_special_required
def edit_special_access_user(request, user_id):
    if not has_permission(request.user, 'manage_users'):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to manage special access"
        }, status=403)

    special_access_user = get_object_or_404(SpecialAccessUser, user_id=user_id)

    if request.method == 'POST':
        form = EditSpecialAccessUserForm(request.POST, special_access_user=special_access_user)
        if form.is_valid():
            # Update allowed franchises and batches
            allowed_franchises = form.cleaned_data.get('allowed_franchises')
            allowed_batches = form.cleaned_data.get('allowed_batches')
            special_access_user.allowed_franchises.set(allowed_franchises)
            special_access_user.allowed_batches.set(allowed_batches)
            messages.success(request, f'Access permissions for {special_access_user.user.username} updated successfully.')
            return redirect('application:special_access_register')
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")

    else:
        form = EditSpecialAccessUserForm(special_access_user=special_access_user)

    # Get batch-franchise map
    batch_franchise_map = form.batch_franchise_map

    return render(request, 'application/edit_special_access_user.html', {
        'form': form,
        'special_access_user': special_access_user,
        'batch_franchise_map': json.dumps(batch_franchise_map),
    })

# ==============================
# USER PROFILE & DASHBOARD
# ==============================

@login_required
def special_user_dashboard(request):
    if not has_permission(request.user, 'view_dashboard'):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to access the dashboard"
        }, status=403)
    
    user = request.user
    # ... rest of your special_user_dashboard code ...

@login_required
def student_profile(request):
    # Users can always access their own profile
    user = request.user

    # Get user profile for phone number
    try:
        user_profile = UserProfile.objects.get(user=user)
        phone_number = user_profile.phone_number
    except UserProfile.DoesNotExist:
        phone_number = None

    # Get all UserFranchise for the user
    user_franchises = UserFranchise.objects.filter(user=user).select_related('franchise', 'batch', 'batch__course')

    # Collect enrolled courses
    enrolled_courses = []
    enrollments = CourseEnrollment.objects.filter(user=user, is_active=True).select_related('course')
    for enrollment in enrollments:
        enrolled_courses.append({
            'course': enrollment.course,
            'enrollment_date': enrollment.created.date(),
        })

    # Collect installment data per user_franchise
    user_franchise_data = []
    for uf in user_franchises:
        installments = []
        try:
            student_fee = StudentFeeManagement.objects.get(user_franchise=uf)
            installments = Installment.objects.filter(student_fee_management=student_fee).order_by('due_date')
        except StudentFeeManagement.DoesNotExist:
            pass

        user_franchise_data.append({
            'user_franchise': uf,
            'installments': installments,
        })

    context = {
        'user': user,
        'phone_number': phone_number,
        'enrolled_courses': enrolled_courses,
        'user_franchise_data': user_franchise_data,
    }

    return render(request, 'application/student_profile.html', context)

# ==============================
# UTILITY FUNCTIONS
# ==============================

@login_required
def get_batches(request, franchise_id):
    if not has_permission(request.user, 'view_franchise'):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    allowed_batches = get_allowed_batches(request.user)
    batches = Batch.objects.filter(franchise_id=franchise_id, id__in=allowed_batches.values('id')).values('id', 'batch_no')
    return JsonResponse({'batches': list(batches)})

@login_required
def get_course_fee(request, course_id):
    if not has_permission(request.user, 'view_franchise'):
        return JsonResponse({'error': 'Permission denied'}, status=403)
    
    course = get_object_or_404(CourseOverview, id=course_id)
    fee_obj, created = CourseFee.objects.get_or_create(course=course, defaults={'fee': 0})
    return JsonResponse({'fee': float(fee_obj.fee)})


# ==============================
# REMAINING VIEWS WITH PERMISSION CHECKS
# ==============================

@login_required
def franchise_fees_report(request):
    if not has_permission(request.user, VIEW_PERMISSIONS['franchise_fees_report']):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to view franchise fee reports"
        }, status=403)

    franchise_id = request.GET.get('franchise_id')
    batch_id = request.GET.get('batch_id')
    if franchise_id == '' or franchise_id == 'None':
        franchise_id = None
    if batch_id == '' or batch_id == 'None':
        batch_id = None
    allowed_franchises = get_allowed_franchises(request.user)
    allowed_batches = get_allowed_batches(request.user)
    all_franchises = allowed_franchises
    today = timezone.now().date()

    # Global totals (always full for allowed franchises and batches)
    installment_queryset = Installment.objects.filter(
        student_fee_management__user_franchise__franchise__in=allowed_franchises
    )
    if allowed_batches.exists():
        installment_queryset = installment_queryset.filter(
            student_fee_management__user_franchise__batch__in=allowed_batches
        )
    total_fees = installment_queryset.aggregate(total=Sum('amount'))['total'] or 0
    total_received = installment_queryset.aggregate(total=Sum('payed_amount'))['total'] or 0
    total_pending = total_fees - total_received
    overdue_installments = installment_queryset.filter(due_date__lt=today).exclude(status='paid')
    total_overdue = sum(inst.amount - inst.payed_amount for inst in overdue_installments) or 0

    # Filtered totals for stats (when franchise/batch selected)
    filtered_total_fees = total_fees
    filtered_total_received = total_received
    filtered_total_pending = total_pending
    filtered_total_overdue = total_overdue

    if batch_id:
        # Calculate filtered totals for the selected batch
        batch_installments = Installment.objects.filter(
            student_fee_management__user_franchise__batch__id=batch_id
        )
        if allowed_batches.exists() and int(batch_id) not in allowed_batches.values_list('id', flat=True):
            batch_installments = Installment.objects.none()
        filtered_total_fees = batch_installments.aggregate(total=Sum('amount'))['total'] or 0
        filtered_total_received = batch_installments.aggregate(total=Sum('payed_amount'))['total'] or 0
        filtered_total_pending = filtered_total_fees - filtered_total_received
        overdue_batch_installments = batch_installments.filter(due_date__lt=today).exclude(status='paid')
        filtered_total_overdue = sum(inst.amount - inst.payed_amount for inst in overdue_batch_installments) or 0
    elif franchise_id:
        # Calculate filtered totals for the selected franchise
        franchise_installments = Installment.objects.filter(
            student_fee_management__user_franchise__franchise__id=franchise_id
        )
        if allowed_batches.exists():
            franchise_installments = franchise_installments.filter(
                student_fee_management__user_franchise__batch__in=allowed_batches
            )
        filtered_total_fees = franchise_installments.aggregate(total=Sum('amount'))['total'] or 0
        filtered_total_received = franchise_installments.aggregate(total=Sum('payed_amount'))['total'] or 0
        filtered_total_pending = filtered_total_fees - filtered_total_received
        overdue_franchise_installments = franchise_installments.filter(due_date__lt=today).exclude(status='paid')
        filtered_total_overdue = sum(inst.amount - inst.payed_amount for inst in overdue_franchise_installments) or 0

    if batch_id:
        # Filter by batch and its franchise
        try:
            batch = Batch.objects.get(id=batch_id)
            if batch not in allowed_batches:
                franchises = Franchise.objects.none()
            else:
                franchise = batch.franchise
                franchises = Franchise.objects.filter(id=franchise.id).prefetch_related(
                    'batches__userfranchise_set__fee_management__installments'
                )
        except Batch.DoesNotExist:
            franchises = Franchise.objects.none()
    elif franchise_id:
        franchises = Franchise.objects.filter(id=franchise_id).prefetch_related(
            'batches__userfranchise_set__fee_management__installments'
        )
    else:
        franchises = allowed_franchises.prefetch_related(
            'batches__userfranchise_set__fee_management__installments'
        ).all()

    # Breakdown data (always full for table)
    franchise_data = []
    for franchise in franchises:
        franchise_received = 0
        franchise_pending = 0
        franchise_overdue = 0
        batches_data = []
        batches = franchise.batches.all()
        if batch_id:
            batches = batches.filter(id=batch_id)
        for batch in batches:
            batch_received = 0
            batch_pending = 0
            batch_overdue = 0
            for user_franchise in batch.userfranchise_set.all():
                try:
                    student_fee = user_franchise.fee_management
                    installments = student_fee.installments.all()
                    batch_received += sum(inst.payed_amount for inst in installments)
                    batch_pending += sum(inst.amount - inst.payed_amount for inst in installments)
                    batch_overdue += sum(inst.amount - inst.payed_amount for inst in installments.filter(due_date__lt=today).exclude(status='paid'))
                except StudentFeeManagement.DoesNotExist:
                    continue
            batches_data.append({
                'batch': batch,
                'received': batch_received,
                'pending': batch_pending,
                'overdue': batch_overdue,
            })
            franchise_received += batch_received
            franchise_pending += batch_pending
            franchise_overdue += batch_overdue
        franchise_data.append({
            'franchise': franchise,
            'batches': batches_data,
            'received': franchise_received,
            'pending': franchise_pending,
            'overdue': franchise_overdue,
        })

    # Prepare student details list for the table
    students_dict = {}
    if batch_id:
        user_franchises = UserFranchise.objects.filter(batch_id=batch_id).select_related('user')
    elif franchise_id:
        user_franchises = UserFranchise.objects.filter(franchise_id=franchise_id).select_related('user')
    else:
        user_franchises = UserFranchise.objects.all().select_related('user')

    for uf in user_franchises:
        user_id = uf.user.id
        try:
            student_fee = uf.fee_management
            installments = student_fee.installments.all()
            total = sum(inst.amount for inst in installments)
            received = sum(inst.payed_amount for inst in installments)
            pending = total - received
            overdue = sum(inst.amount - inst.payed_amount for inst in installments.filter(due_date__lt=today).exclude(status='paid'))
        except StudentFeeManagement.DoesNotExist:
            total = 0
            received = 0
            pending = 0
            overdue = 0

        if user_id not in students_dict:
            try:
                profile = UserProfile.objects.get(user=uf.user)
                phone_number = profile.phone_number
            except UserProfile.DoesNotExist:
                phone_number = ''

            students_dict[user_id] = {
                'name': uf.user.get_full_name(),
                'username': uf.user.username,
                'phone_number': phone_number,
                'email': uf.user.email,
                'total_fees': 0,
                'received_fees': 0,
                'pending_fees': 0,
                'overdue_fees': 0,
                'user_franchise_id': uf.id,
            }

        students_dict[user_id]['total_fees'] += total
        students_dict[user_id]['received_fees'] += received
        students_dict[user_id]['pending_fees'] += pending
        students_dict[user_id]['overdue_fees'] += overdue

    students = list(students_dict.values())

    # Paginate students
    paginator = Paginator(students, 20)
    page = request.GET.get('page')
    try:
        students_page = paginator.page(page)
    except PageNotAnInteger:
        students_page = paginator.page(1)
    except EmptyPage:
        students_page = paginator.page(paginator.num_pages)

    return render(request, 'application/franchise_fees_report.html', {
        'franchise_data': franchise_data,
        'total_fees': filtered_total_fees,
        'total_pending': filtered_total_pending,
        'total_overdue': filtered_total_overdue,
        'total_received': filtered_total_received,
        'all_franchises': all_franchises,
        'selected_franchise_id': franchise_id,
        'selected_batch_id': batch_id,
        'students_page': students_page,
    })

@login_required
def monthly_fees_report(request):
    if not has_permission(request.user, VIEW_PERMISSIONS['monthly_fees_report']):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to view monthly fee reports"
        }, status=403)

    month = request.GET.get('month')
    year = request.GET.get('year')

    today = timezone.now().date()
    all_franchises = get_allowed_franchises(request.user)

    selected_month = None
    if month and year:
        try:
            selected_month = datetime(year=int(year), month=int(month), day=1).date()
        except ValueError:
            selected_month = None

    MONTH_CHOICES = [
        (1, 'January'), (2, 'February'), (3, 'March'), (4, 'April'),
        (5, 'May'), (6, 'June'), (7, 'July'), (8, 'August'),
        (9, 'September'), (10, 'October'), (11, 'November'), (12, 'December')
    ]

    current_year = today.year
    YEAR_CHOICES = [y for y in range(current_year - 5, current_year + 6)]

    total_fees = Installment.objects.aggregate(total=Sum('amount'))['total'] or 0
    total_received = Installment.objects.aggregate(total=Sum('payed_amount'))['total'] or 0
    total_pending = total_fees - total_received
    overdue_installments = Installment.objects.filter(due_date__lt=today).exclude(status='paid')
    total_overdue = sum(inst.amount - inst.payed_amount for inst in overdue_installments) or 0

    if selected_month:
        filtered_installments = Installment.objects.filter(
            due_date__year=selected_month.year,
            due_date__month=selected_month.month
        )
        filtered_total_fees = filtered_installments.aggregate(total=Sum('amount'))['total'] or 0
        filtered_total_received = filtered_installments.aggregate(total=Sum('payed_amount'))['total'] or 0
        filtered_total_pending = filtered_total_fees - filtered_total_received
        overdue_filtered_installments = filtered_installments.filter(due_date__lt=today).exclude(status='paid')
        filtered_total_overdue = sum(inst.amount - inst.payed_amount for inst in overdue_filtered_installments) or 0
    else:
        filtered_installments = Installment.objects.all()
        filtered_total_fees = total_fees
        filtered_total_received = total_received
        filtered_total_pending = total_pending
        filtered_total_overdue = total_overdue

    students_dict = {}
    user_franchises_queryset = UserFranchise.objects.select_related('user')

    for uf in user_franchises_queryset:
        user_id = uf.user.id
        try:
            student_fee = uf.fee_management
            installments = student_fee.installments.all()
            if selected_month:
                installments = installments.filter(
                    due_date__year=selected_month.year,
                    due_date__month=selected_month.month
                )
            total = sum(inst.amount for inst in installments)
            received = sum(inst.payed_amount for inst in installments)
            pending = total - received
            overdue = sum(inst.amount - inst.payed_amount for inst in installments.filter(due_date__lt=today).exclude(status='paid'))
        except StudentFeeManagement.DoesNotExist:
            total = received = pending = overdue = 0

        if user_id not in students_dict:
            try:
                profile = UserProfile.objects.get(user=uf.user)
                phone_number = profile.phone_number
            except UserProfile.DoesNotExist:
                phone_number = ''

            students_dict[user_id] = {
                'name': uf.user.get_full_name(),
                'username': uf.user.username,
                'phone_number': phone_number,
                'email': uf.user.email,
                'total_fees': 0,
                'received_fees': 0,
                'pending_fees': 0,
                'overdue_fees': 0,
                'user_franchise_id': uf.id,
            }

        students_dict[user_id]['total_fees'] += total
        students_dict[user_id]['received_fees'] += received
        students_dict[user_id]['pending_fees'] += pending
        students_dict[user_id]['overdue_fees'] += overdue

    students = []
    for student in students_dict.values():
        if selected_month and (student['total_fees'] > 0 or student['received_fees'] > 0 or student['pending_fees'] > 0 or student['overdue_fees'] > 0):
            students.append(student)
        elif not selected_month:
            students.append(student)

    paginator = Paginator(students, 20)
    page = request.GET.get('page')
    try:
        students_page = paginator.page(page)
    except PageNotAnInteger:
        students_page = paginator.page(1)
    except EmptyPage:
        students_page = paginator.page(paginator.num_pages)

    franchise_data = []
    franchises = Franchise.objects.prefetch_related(
        'batches__userfranchise_set__fee_management__installments'
    ).all()

    for franchise in franchises:
        franchise_received = franchise_pending = franchise_overdue = 0
        batches_data = []
        for batch in franchise.batches.all():
            batch_received = batch_pending = batch_overdue = 0
            for uf in batch.userfranchise_set.all():
                try:
                    student_fee = uf.fee_management
                    installments = student_fee.installments.all()
                    if selected_month:
                        installments = installments.filter(
                            due_date__year=selected_month.year,
                            due_date__month=selected_month.month
                        )
                    batch_received += sum(inst.payed_amount for inst in installments)
                    batch_pending += sum(inst.amount - inst.payed_amount for inst in installments)
                    batch_overdue += sum(inst.amount - inst.payed_amount for inst in installments.filter(due_date__lt=today).exclude(status='paid'))
                except StudentFeeManagement.DoesNotExist:
                    continue
            batches_data.append({
                'batch': batch,
                'received': batch_received,
                'pending': batch_pending,
                'overdue': batch_overdue,
            })
            franchise_received += batch_received
            franchise_pending += batch_pending
            franchise_overdue += batch_overdue
        franchise_data.append({
            'franchise': franchise,
            'batches': batches_data,
            'received': franchise_received,
            'pending': franchise_pending,
            'overdue': franchise_overdue,
        })

    return render(request, 'application/monthly_fees_report.html', {
        'all_franchises': all_franchises,
        'months': MONTH_CHOICES,
        'years': YEAR_CHOICES,
        'selected_month': int(month) if month else None,
        'selected_year': int(year) if year else None,
        'total_fees': filtered_total_fees,
        'total_received': filtered_total_received,
        'total_pending': filtered_total_pending,
        'total_overdue': filtered_total_overdue,
        'students_page': students_page,
        'franchise_data': franchise_data,
    })

@login_required
def course_fee_list(request):
    if not has_permission(request.user, VIEW_PERMISSIONS['course_fee_list']):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to manage course fees"
        }, status=403)
    
    courses = CourseOverview.objects.all()
    course_fees = []
    for course in courses:
        fee_obj, created = CourseFee.objects.get_or_create(course=course, defaults={'fee': 0})
        course_fees.append((course, fee_obj))

    if request.method == 'POST':
        for course, fee_obj in course_fees:
            fee_value = request.POST.get(f'fee_{course.id}')
            if fee_value:
                try:
                    fee_obj.fee = float(fee_value)
                    fee_obj.save()
                except ValueError:
                    pass
        return redirect('application:homepage')

    return render(request, 'application/course_fee_list.html', {
        'course_fees': course_fees,
    })

@login_required
def fee_reminders(request):
    if not has_permission(request.user, VIEW_PERMISSIONS['fee_reminders']):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to view fee reminders"
        }, status=403)

    allowed_franchises = get_allowed_franchises(request.user)
    allowed_batches = get_allowed_batches(request.user)

    if request.method == 'POST':
        installment_id = request.POST.get('installment_id')
        if installment_id:
            try:
                installment = Installment.objects.select_related(
                    'student_fee_management__user_franchise__user',
                    'student_fee_management__user_franchise__batch'
                ).get(id=installment_id)
                user = installment.student_fee_management.user_franchise.user
                batch = installment.student_fee_management.user_franchise.batch
                course_id = batch.course.id if batch and batch.course else None
                if course_id:
                    if CourseEnrollment.is_enrolled(user, course_id):
                        CourseEnrollment.unenroll(user, course_id)
            except Installment.DoesNotExist:
                pass
        return redirect('application:fee_reminders')

    upcoming_franchise_id = request.GET.get('upcoming_franchise_id')
    upcoming_batch_id = request.GET.get('upcoming_batch_id')
    overdue_franchise_id = request.GET.get('overdue_franchise_id')
    overdue_batch_id = request.GET.get('overdue_batch_id')

    today = timezone.now().date()
    three_days_later = today + timedelta(days=3)

    upcoming_installments = Installment.objects.filter(
        due_date__gte=today,
        due_date__lte=three_days_later,
        status='pending'
    ).select_related('student_fee_management__user_franchise__user', 'student_fee_management__user_franchise__batch', 'student_fee_management__user_franchise__batch__franchise')

    # Filter by allowed franchises and batches
    upcoming_installments = upcoming_installments.filter(
        student_fee_management__user_franchise__franchise__in=allowed_franchises
    )
    if allowed_batches.exists():
        upcoming_installments = upcoming_installments.filter(
            student_fee_management__user_franchise__batch__in=allowed_batches
        )

    if upcoming_franchise_id:
        if upcoming_franchise_id not in [str(f.id) for f in allowed_franchises]:
            upcoming_installments = Installment.objects.none()
        else:
            upcoming_installments = upcoming_installments.filter(student_fee_management__user_franchise__franchise_id=upcoming_franchise_id)
    if upcoming_batch_id:
        if upcoming_batch_id not in [str(b.id) for b in allowed_batches]:
            upcoming_installments = Installment.objects.none()
        else:
            upcoming_installments = upcoming_installments.filter(student_fee_management__user_franchise__batch_id=upcoming_batch_id)

    overdue_installments = Installment.objects.filter(
        due_date__lt=today
    ).exclude(status='paid').select_related('student_fee_management__user_franchise__user', 'student_fee_management__user_franchise__batch', 'student_fee_management__user_franchise__batch__franchise')

    # Filter by allowed franchises and batches
    overdue_installments = overdue_installments.filter(
        student_fee_management__user_franchise__franchise__in=allowed_franchises
    )
    if allowed_batches.exists():
        overdue_installments = overdue_installments.filter(
            student_fee_management__user_franchise__batch__in=allowed_batches
        )

    if overdue_franchise_id:
        if overdue_franchise_id not in [str(f.id) for f in allowed_franchises]:
            overdue_installments = Installment.objects.none()
        else:
            overdue_installments = overdue_installments.filter(student_fee_management__user_franchise__franchise_id=overdue_franchise_id)
    if overdue_batch_id:
        if overdue_batch_id not in [str(b.id) for b in allowed_batches]:
            overdue_installments = Installment.objects.none()
        else:
            overdue_installments = overdue_installments.filter(student_fee_management__user_franchise__batch_id=overdue_batch_id)

    overdue_data = []
    for installment in overdue_installments:
        user = installment.student_fee_management.user_franchise.user
        batch = installment.student_fee_management.user_franchise.batch
        course_id = batch.course.id if batch and batch.course else None
        is_enrolled = False
        if course_id:
            is_enrolled = CourseEnrollment.is_enrolled(user, course_id)
        overdue_data.append({
            'installment': installment,
            'is_enrolled': is_enrolled
        })

    return render(request, 'application/fee_reminders.html', {
        'upcoming_installments': upcoming_installments,
        'overdue_data': overdue_data,
        'all_franchises': allowed_franchises,
        'upcoming_franchise_id': upcoming_franchise_id,
        'upcoming_batch_id': upcoming_batch_id,
        'overdue_franchise_id': overdue_franchise_id,
        'overdue_batch_id': overdue_batch_id,
    })

@login_required
def inactive_users(request):
    if not has_permission(request.user, VIEW_PERMISSIONS['inactive_users']):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to view inactive users"
        }, status=403)

    days_min = request.GET.get('days_min', '').strip()
    franchise_id = request.GET.get('franchise_id', '').strip()
    batch_id = request.GET.get('batch_id', '').strip()

    if not days_min:
        days_min = '2'

    allowed_franchises = get_allowed_franchises(request.user)
    allowed_batches = get_allowed_batches(request.user)

    two_days_ago = timezone.now() - timedelta(days=2)
    inactive_users = User.objects.filter(
        models.Q(last_login__isnull=True) | models.Q(last_login__lt=two_days_ago)
    ).filter(userfranchise__isnull=False).distinct().order_by('last_login')

    # Filter by allowed franchises and batches
    inactive_users = inactive_users.filter(userfranchise__franchise__in=allowed_franchises)
    if allowed_batches.exists():
        inactive_users = inactive_users.filter(userfranchise__batch__in=allowed_batches)

    if franchise_id:
        if franchise_id not in [str(f.id) for f in allowed_franchises]:
            inactive_users = User.objects.none()
        else:
            inactive_users = inactive_users.filter(userfranchise__franchise_id=franchise_id)

    if batch_id:
        if batch_id not in [str(b.id) for b in allowed_batches]:
            inactive_users = User.objects.none()
        else:
            inactive_users = inactive_users.filter(userfranchise__batch_id=batch_id)

    paginator = Paginator(inactive_users, 20)
    page = request.GET.get('page')

    try:
        users_page = paginator.page(page)
    except PageNotAnInteger:
        users_page = paginator.page(1)
    except EmptyPage:
        users_page = paginator.page(paginator.num_pages)

    user_data = []
    now = timezone.now()
    for user in users_page:
        if user.last_login:
            days_inactive = (now - user.last_login).days
        else:
            days_inactive = None

        try:
            profile = UserProfile.objects.get(user=user)
            phone_number = profile.phone_number
        except UserProfile.DoesNotExist:
            phone_number = None

        user_franchise = UserFranchise.objects.filter(user=user).first()
        batch = user_franchise.batch if user_franchise else None
        franchise = user_franchise.franchise if user_franchise else None

        user_data.append({
            'user': user,
            'days_inactive': days_inactive,
            'phone_number': phone_number,
            'batch': batch,
            'franchise': franchise,
        })

    if days_min:
        try:
            days_min_int = int(days_min)
            user_data = [d for d in user_data if d['days_inactive'] is None or (d['days_inactive'] is not None and d['days_inactive'] >= days_min_int)]
        except ValueError:
            pass

    batches = Batch.objects.filter(franchise_id=franchise_id, id__in=allowed_batches.values('id')) if franchise_id else Batch.objects.none()

    return render(request, 'application/inactive_users.html', {
        'user_data': user_data,
        'two_days_ago': two_days_ago,
        'users_page': users_page,
        'all_franchises': allowed_franchises,
        'batches': batches,
        'current_days_min': days_min,
        'current_franchise_id': franchise_id,
        'current_batch_id': batch_id,
    })

@login_required
def franchise_edit(request, pk):
    if not has_permission(request.user, VIEW_PERMISSIONS['franchise_edit']):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to edit franchises"
        }, status=403)
    
    franchise = get_object_or_404(Franchise, pk=pk)
    
    if request.method == "POST":
        form = FranchiseForm(request.POST, instance=franchise)
        if form.is_valid():
            form.save()
            return redirect('application:franchise_list')
    else:
        form = FranchiseForm(instance=franchise)
    
    return render(request, 'application/franchise_edit.html', {'form': form, 'franchise': franchise})

@login_required
def franchise_report(request, pk):
    if not has_permission(request.user, VIEW_PERMISSIONS['franchise_report']):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to view franchise reports"
        }, status=403)

    franchise = get_object_or_404(Franchise, pk=pk)

    # Check if user has access to this specific franchise
    allowed_franchises = get_allowed_franchises(request.user)
    if franchise not in allowed_franchises:
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to access this franchise"
        }, status=403)

    student_ids = list(
        UserFranchise.objects.filter(franchise=franchise).values_list('user_id', flat=True)
    )
    enrollments = CourseEnrollment.objects.filter(
        user_id__in=student_ids,
        is_active=True
    )

    course_counts = (
        enrollments.values('course_id')
        .annotate(student_count=Count('user_id', distinct=True))
    )
    course_student_map = {row['course_id']: row['student_count'] for row in course_counts}
    courses = list(CourseOverview.objects.filter(id__in=course_student_map.keys()))

    for course in courses:
        course.student_count = course_student_map.get(course.id, 0)

    users = list(User.objects.filter(id__in=student_ids).order_by('username'))

    # Get only allowed batches for this franchise
    allowed_batches = get_allowed_batches(request.user)
    batches = Batch.objects.filter(franchise=franchise, id__in=allowed_batches.values('id')).select_related('course')

    search_query = request.GET.get('search', '').strip()

    if search_query:
        # Batches that match the search query
        matching_batches = batches.filter(
            Q(batch_no__icontains=search_query) |
            Q(course__display_name__icontains=search_query) |
            Q(fees__icontains=search_query)
        )
        # Batches that do not match
        non_matching_batches = batches.exclude(
            Q(batch_no__icontains=search_query) |
            Q(course__display_name__icontains=search_query) |
            Q(fees__icontains=search_query)
        )
        # Combine: matching first, then non-matching
        batches = list(matching_batches) + list(non_matching_batches)
    else:
        batches = list(batches)

    return render(request, 'application/franchise_report.html', {
        'franchise': franchise,
        'courses': courses,
        'users': users,
        'batches': batches,
        'search_query': search_query
    })

@login_required
def batch_create(request, pk):
    if not has_permission(request.user, VIEW_PERMISSIONS['batch_create']):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to create batches"
        }, status=403)
    
    franchise = get_object_or_404(Franchise, pk=pk)

    if request.method == "POST":
        form = BatchForm(request.POST)
        if form.is_valid():
            batch = form.save(commit=False)
            batch.franchise = franchise
            course_fee, created = CourseFee.objects.get_or_create(course=batch.course, defaults={'fee': 0})
            batch.fees = course_fee.fee
            batch.save()

            discount = form.cleaned_data.get('discount') or 0
            BatchFeeManagement.objects.create(batch=batch, discount=discount)

            return redirect('application:franchise_report', pk=franchise.pk)
    else:
        form = BatchForm()

    return render(request, 'application/batch_create.html', {
        'form': form,
        'franchise': franchise,
    })

@login_required
def batch_students(request, franchise_pk, batch_pk):
    if not has_permission(request.user, VIEW_PERMISSIONS['batch_students']):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to view batch students"
        }, status=403)

    franchise = get_object_or_404(Franchise, pk=franchise_pk)
    batch = get_object_or_404(Batch, pk=batch_pk, franchise=franchise)

    user_franchises = UserFranchise.objects.filter(franchise=franchise, batch=batch).select_related('user')
    users = [uf.user for uf in user_franchises]

    search_query = request.GET.get('search', '').strip()

    if search_query:
        # Get user profiles for phone numbers
        user_profiles = UserProfile.objects.filter(user__in=users).select_related('user')
        profile_dict = {up.user_id: up.phone_number for up in user_profiles}

        # Separate matching and non-matching users
        matching_users = []
        non_matching_users = []

        for user in users:
            phone = profile_dict.get(user.id, '')
            full_name = user.get_full_name()
            if (search_query.lower() in full_name.lower() or
                search_query.lower() in user.username.lower() or
                search_query.lower() in user.email.lower() or
                search_query.lower() in phone.lower()):
                matching_users.append(user)
            else:
                non_matching_users.append(user)

        # Combine: matching first, then non-matching
        users = matching_users + non_matching_users
    else:
        users = list(users)

    fees_management_set = BatchFeeManagement.objects.filter(batch=batch).exists() and InstallmentTemplate.objects.filter(batch_fee_management__batch=batch).exists()

    return render(request, 'application/batch_students.html', {
        'franchise': franchise,
        'batch': batch,
        'users': users,
        'fees_management_set': fees_management_set,
        'search_query': search_query
    })

@login_required
def student_detail(request, franchise_pk, batch_pk, user_pk):
    if not has_permission(request.user, VIEW_PERMISSIONS['student_detail']):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to view student details"
        }, status=403)
    
    franchise = get_object_or_404(Franchise, pk=franchise_pk)
    batch = get_object_or_404(Batch, pk=batch_pk, franchise=franchise)
    user = get_object_or_404(User, pk=user_pk)

    user_franchise = get_object_or_404(UserFranchise, user=user, franchise=franchise, batch=batch)

    fee_management = get_object_or_404(BatchFeeManagement, batch=batch)
    student_fee, created = StudentFeeManagement.objects.get_or_create(
        user_franchise=user_franchise,
        defaults={'batch_fee_management': fee_management, 'discount': fee_management.discount}
    )

    enrollment = CourseEnrollment.objects.get(user=user, course_id=batch.course.id)
    registration_date = enrollment.created.date()

    if not Installment.objects.filter(student_fee_management=student_fee).exists():
        templates = InstallmentTemplate.objects.filter(batch_fee_management=fee_management).order_by('id')
        cumulative_days = 0
        for template in templates:
            cumulative_days += template.repayment_period_days
            due_date = registration_date + timedelta(days=cumulative_days)

            Installment.objects.create(
                student_fee_management=student_fee,
                due_date=due_date,
                amount=template.amount,
                repayment_period_days=template.repayment_period_days
            )

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'enroll':
            if not CourseEnrollment.is_enrolled(user, batch.course.id):
                CourseEnrollment.enroll(user, batch.course.id)
        elif action == 'unenroll':
            if CourseEnrollment.is_enrolled(user, batch.course.id):
                CourseEnrollment.unenroll(user, batch.course.id)
        return redirect('application:student_detail', franchise_pk=franchise.pk, batch_pk=batch.pk, user_pk=user.pk)

    existing_installments = Installment.objects.filter(student_fee_management=student_fee).order_by('due_date')
    installments = [{'installment': inst} for inst in existing_installments]

    is_enrolled = CourseEnrollment.is_enrolled(user, batch.course.id)
    show_fee_management_button = has_permission(request.user, VIEW_PERMISSIONS['student_fee_management'])
    show_edit_button = has_permission(request.user, VIEW_PERMISSIONS['edit_student_details'])
    show_reports_button = has_permission(request.user, VIEW_PERMISSIONS['homepage'])
    show_franchise_button = has_permission(request.user, VIEW_PERMISSIONS['franchise_list'])
    show_receipt_button = has_permission(request.user, VIEW_PERMISSIONS['receipt_search'])

    return render(request, 'application/student_detail.html', {
        'franchise': franchise,
        'batch': batch,
        'user': user,
        'user_franchise': user_franchise,
        'fee_management': fee_management,
        'student_fee': student_fee,
        'installments': installments,
        'is_enrolled': is_enrolled,
        'show_fee_management_button': show_fee_management_button,
        'show_edit_button': show_edit_button,
        'show_reports_button': show_reports_button,
        'show_franchise_button': show_franchise_button,
        'show_receipt_button': show_receipt_button,
    })

@login_required
def edit_student_details(request, franchise_pk, batch_pk, user_pk):
    if not has_permission(request.user, VIEW_PERMISSIONS['edit_student_details']):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to edit student details"
        }, status=403)
    
    franchise = get_object_or_404(Franchise, pk=franchise_pk)
    batch = get_object_or_404(Batch, pk=batch_pk, franchise=franchise)
    user = get_object_or_404(User, pk=user_pk)

    if request.method == "POST":
        form = StudentEditForm(request.POST, instance=user)
        if form.is_valid():
            form.save()
            return redirect('application:student_detail', franchise_pk=franchise.pk, batch_pk=batch.pk, user_pk=user.pk)
    else:
        form = StudentEditForm(instance=user)

    show_reports_button = has_permission(request.user, VIEW_PERMISSIONS['homepage'])
    show_franchise_button = has_permission(request.user, VIEW_PERMISSIONS['franchise_list'])
    show_receipt_button = has_permission(request.user, VIEW_PERMISSIONS['receipt_search'])    

    return render(request, 'application/edit_student_details.html', {
        'form': form,
        'franchise': franchise,
        'batch': batch,
        'user': user,
        'show_reports_button': show_reports_button,
        'show_franchise_button': show_franchise_button,
        'show_receipt_button': show_receipt_button,
    })

from django.contrib import messages
from django.core.mail import send_mail
from django.conf import settings
from .utils import send_welcome_email, send_enrollment_email  # make sure you have this file as explained earlier

@login_required
def batch_user_register(request, franchise_pk, batch_pk):
    if not has_permission(request.user, VIEW_PERMISSIONS['batch_user_register']):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to register batch users"
        }, status=403)
    
    franchise = get_object_or_404(Franchise, pk=franchise_pk)
    batch = get_object_or_404(Batch, pk=batch_pk, franchise=franchise)

    if request.method == "POST":
        form = FranchiseUserRegistrationForm(request.POST)
        if form.is_valid():
            # Create and enroll user
            user = form.save(franchise=franchise, batch=batch, commit=True)
            CourseEnrollment.enroll(user, batch.course.id)

            # Create fee management for the student
            user_franchise = UserFranchise.objects.get(user=user, franchise=franchise, batch=batch)
            fee_management = get_object_or_404(BatchFeeManagement, batch=batch)
            student_fee = StudentFeeManagement.objects.create(
                user_franchise=user_franchise,
                batch_fee_management=fee_management,
                discount=fee_management.discount
            )

            # Create installment schedule
            enrollment = CourseEnrollment.objects.get(user=user, course_id=batch.course.id)
            registration_date = enrollment.created.date()
            templates = InstallmentTemplate.objects.filter(batch_fee_management=fee_management).order_by('id')
            cumulative_days = 0
            for template in templates:
                cumulative_days += template.repayment_period_days
                due_date = registration_date + timedelta(days=cumulative_days)
                Installment.objects.create(
                    student_fee_management=student_fee,
                    due_date=due_date,
                    amount=template.amount,
                    repayment_period_days=template.repayment_period_days
                )

            # 📨 Send welcome + enrollment emails with proper error handling
            try:
                send_welcome_email(user)
                send_enrollment_email(user, batch.course.display_name)
                messages.success(request, f"User {user.username} registered and welcome mail sent successfully.")
            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Email sending failed for {user.email}: {str(e)}")
                messages.warning(request, f"User registered successfully, but failed to send email: {str(e)}")

            return redirect('application:batch_students', franchise_pk=franchise.pk, batch_pk=batch.pk)
    else:
        form = FranchiseUserRegistrationForm()

    return render(request, 'application/user_register_course.html', {
        'form': form,
        'franchise': franchise,
        'batch': batch,
    })

@login_required
@superuser_required
def test_email_config(request):
    """Test email configuration - for superusers only"""
    try:
        from django.core.mail import send_mail
        send_mail(
            'Test Email from EzfinTutor',
            'This is a test email to verify your email configuration.',
            settings.DEFAULT_FROM_EMAIL,
            [request.user.email],
            fail_silently=False
        )
        messages.success(request, f"Test email sent successfully to {request.user.email}")
    except Exception as e:
        messages.error(request, f"Failed to send test email: {str(e)}")
    
    return redirect('application:homepage')


@login_required
def enroll_existing_user(request, franchise_pk, batch_pk):
    if not has_permission(request.user, VIEW_PERMISSIONS['enroll_existing_user']):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to enroll existing users"
        }, status=403)
    
    franchise = get_object_or_404(Franchise, pk=franchise_pk)
    batch = get_object_or_404(Batch, pk=batch_pk, franchise=franchise)

    if request.method == "POST":
        user_ids = request.POST.getlist('user_ids')
        if user_ids:
            enrolled_users = []
            already_enrolled = []
            for user_id in user_ids:
                user = get_object_or_404(User, pk=user_id)
                if UserFranchise.objects.filter(user=user, franchise=franchise, batch=batch).exists():
                    already_enrolled.append(user.get_full_name())
                    continue

                user_franchise = UserFranchise.objects.create(user=user, franchise=franchise, batch=batch, registration_number=user.username)

                fee_management = get_object_or_404(BatchFeeManagement, batch=batch)
                student_fee = StudentFeeManagement.objects.create(
                    user_franchise=user_franchise,
                    batch_fee_management=fee_management,
                    discount=fee_management.discount
                )

                CourseEnrollment.enroll(user, batch.course.id)

                enrollment = CourseEnrollment.objects.get(user=user, course_id=batch.course.id)
                registration_date = enrollment.created.date()
                templates = InstallmentTemplate.objects.filter(batch_fee_management=fee_management).order_by('id')
                cumulative_days = 0
                for template in templates:
                    cumulative_days += template.repayment_period_days
                    due_date = registration_date + timedelta(days=cumulative_days)
                    Installment.objects.create(
                        student_fee_management=student_fee,
                        due_date=due_date,
                        amount=template.amount,
                        repayment_period_days=template.repayment_period_days
                    )

                enrolled_users.append(user.get_full_name())

            if enrolled_users:
                messages.success(request, f"Users {', '.join(enrolled_users)} enrolled successfully in {batch.batch_no}.")
            if already_enrolled:
                messages.warning(request, f"Users {', '.join(already_enrolled)} are already enrolled in this batch.")
            return redirect('application:batch_students', franchise_pk=franchise.pk, batch_pk=batch.pk)

    search_query = request.GET.get('search_query', '').strip()
    users = []
    if search_query:
        from common.djangoapps.student.models import UserProfile
        profiles = UserProfile.objects.filter(phone_number__icontains=search_query)
        user_ids_from_profile = [p.user_id for p in profiles]

        users_by_fields = User.objects.filter(
            Q(email__icontains=search_query) |
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query) |
            Q(username__icontains=search_query)
        ).filter(userfranchise__franchise=franchise).exclude(userfranchise__batch=batch)

        users_by_phone = User.objects.filter(id__in=user_ids_from_profile).filter(userfranchise__franchise=franchise).exclude(userfranchise__batch=batch)

        users = (users_by_fields | users_by_phone).distinct()[:20]

    return render(request, 'application/enroll_existing_user.html', {
        'franchise': franchise,
        'batch': batch,
        'search_query': search_query,
        'users': users,
    })

@login_required
def batch_fee_management(request, franchise_pk, batch_pk):
    if not has_permission(request.user, VIEW_PERMISSIONS['batch_fee_management']):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to manage batch fees"
        }, status=403)
    
    franchise = get_object_or_404(Franchise, pk=franchise_pk)
    batch = get_object_or_404(Batch, pk=batch_pk, franchise=franchise)

    fee_management, created = BatchFeeManagement.objects.get_or_create(batch=batch)

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "save_discount":
            form = BatchFeeManagementForm(request.POST, instance=fee_management)
            if form.is_valid():
                form.save()
            return redirect('application:batch_fee_management', franchise_pk=franchise.pk, batch_pk=batch.pk)

        elif action == "save_installments":
            InstallmentTemplate.objects.filter(batch_fee_management=fee_management).delete()

            installment_count = 0
            while f'installment_amount_{installment_count + 1}' in request.POST:
                installment_count += 1
                amount = request.POST.get(f'installment_amount_{installment_count}')
                period = request.POST.get(f'repayment_period_{installment_count}')
                if amount and period:
                    InstallmentTemplate.objects.create(
                        batch_fee_management=fee_management,
                        amount=amount,
                        repayment_period_days=period
                    )
            return redirect('application:batch_students', franchise_pk=franchise.pk, batch_pk=batch.pk)

    else:
        form = BatchFeeManagementForm(instance=fee_management)

    installments = InstallmentTemplate.objects.filter(batch_fee_management=fee_management)

    return render(request, 'application/batch_fee_management.html', {
        'form': form,
        'franchise': franchise,
        'batch': batch,
        'fee_management': fee_management,
        'installments': installments,
    })

@login_required
def student_fee_management(request, franchise_pk, batch_pk, user_pk):
    if not has_permission(request.user, VIEW_PERMISSIONS['student_fee_management']):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to manage student fees"
        }, status=403)
    
    franchise = get_object_or_404(Franchise, pk=franchise_pk)
    batch = get_object_or_404(Batch, pk=batch_pk, franchise=franchise)
    user = get_object_or_404(User, pk=user_pk)

    fee_management = get_object_or_404(BatchFeeManagement, batch=batch)
    user_franchise = get_object_or_404(UserFranchise, user=user, franchise=franchise, batch=batch)

    student_fee, created = StudentFeeManagement.objects.get_or_create(
        user_franchise=user_franchise,
        defaults={'batch_fee_management': fee_management, 'discount': fee_management.discount}
    )

    enrollment = CourseEnrollment.objects.get(user=user, course_id=batch.course.id)
    registration_date = enrollment.created.date()

    if request.method == "POST":
        existing_installments = Installment.objects.filter(student_fee_management=student_fee).order_by('due_date')
        error_message = None
        last_paid_index = -1
        for i, installment in enumerate(existing_installments):
            status_key = f'status_{installment.id}'
            payed_amount_key = f'payed_amount_{installment.id}'
            if status_key in request.POST and payed_amount_key in request.POST:
                new_status = request.POST[status_key]
                try:
                    new_payed_amount = float(request.POST[payed_amount_key])
                except ValueError:
                    error_message = "Invalid payed amount."
                    break

                if new_status not in ['pending', 'paid', 'overdue']:
                    error_message = "Invalid status value."
                    break

                if new_payed_amount < 0:
                    error_message = "Payed amount must be greater than or equal to 0."
                    break

                if new_status == 'paid' and new_payed_amount <= 0:
                    error_message = "Payed amount must be greater than zero to mark as paid."
                    break

                if installment.status == 'paid' and new_status != 'paid':
                    error_message = "Paid installments cannot be changed."
                    break

                if new_status == 'paid':
                    if i > 0 and existing_installments[i-1].status != 'paid':
                        error_message = "Payments must be marked in order."
                        break
                    last_paid_index = i

        if error_message:
            messages.error(request, error_message)
        else:
            for i, installment in enumerate(existing_installments):
                status_key = f'status_{installment.id}'
                payed_amount_key = f'payed_amount_{installment.id}'
                if status_key in request.POST and payed_amount_key in request.POST:
                    new_status = request.POST[status_key]
                    try:
                        new_payed_amount = float(request.POST[payed_amount_key])
                    except ValueError:
                        new_payed_amount = 0

                    if new_status in ['pending', 'paid', 'overdue']:
                        if installment.status != 'paid':
                            installment.status = new_status
                            installment.payed_amount = new_payed_amount
                            if new_status == 'paid' and not installment.payment_date:
                                installment.payment_date = timezone.now().date()
                            elif new_status != 'paid':
                                installment.payment_date = None
                            installment.save()

            total_paid = sum(inst.payed_amount for inst in Installment.objects.filter(student_fee_management=student_fee))
            student_fee.remaining_amount = fee_management.remaining_amount - total_paid
            student_fee.save()

        return redirect('application:student_fee_management', franchise_pk=franchise.pk, batch_pk=batch.pk, user_pk=user.pk)

    existing_installments = Installment.objects.filter(student_fee_management=student_fee).order_by('due_date')
    installments = [{'installment': installment, 'repayment_period_days': installment.repayment_period_days} for installment in existing_installments]

    total_paid = sum(installment.payed_amount for installment in existing_installments)
    total_pending = sum(installment.amount - installment.payed_amount for installment in existing_installments)
    show_reports_button = has_permission(request.user, VIEW_PERMISSIONS['homepage'])
    show_franchise_button = has_permission(request.user, VIEW_PERMISSIONS['franchise_list'])
    show_receipt_button = has_permission(request.user, VIEW_PERMISSIONS['receipt_search'])

    return render(request, 'application/student_fee_management.html', {
        'franchise': franchise,
        'batch': batch,
        'user': user,
        'fee_management': fee_management,
        'student_fee': student_fee,
        'installments': installments,
        'total_paid': total_paid,
        'total_pending': total_pending,
        'registration_date': registration_date,
        'show_reports_button': show_reports_button,
        'show_franchise_button': show_franchise_button,
        'show_receipt_button': show_receipt_button,
    })

@login_required
def edit_installment_setup(request, franchise_pk, batch_pk, user_pk):
    if not has_permission(request.user, VIEW_PERMISSIONS['edit_installment_setup']):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to edit installment setup"
        }, status=403)
    
    franchise = get_object_or_404(Franchise, pk=franchise_pk)
    batch = get_object_or_404(Batch, pk=batch_pk, franchise=franchise)
    user = get_object_or_404(User, pk=user_pk)

    fee_management = get_object_or_404(BatchFeeManagement, batch=batch)
    user_franchise = get_object_or_404(UserFranchise, user=user, franchise=franchise, batch=batch)
    student_fee = get_object_or_404(StudentFeeManagement, user_franchise=user_franchise)

    enrollment = CourseEnrollment.objects.get(user=user, course_id=batch.course.id)
    registration_date = enrollment.created.date()

    EditInstallmentFormSet = modelformset_factory(
        Installment,
        form=EditInstallmentForm,
        extra=0,
        can_delete=True,
        fields=['amount', 'repayment_period_days']
    )

    discount_form = StudentDiscountForm()

    if request.method == "POST":
        action = request.POST.get('action')
        if action == 'save_discount':
            discount_form = StudentDiscountForm(request.POST)
            if discount_form.is_valid():
                additional_discount = discount_form.cleaned_data.get('additional_discount') or 0
                total_discount = fee_management.discount + additional_discount
                student_fee.discount = total_discount
                student_fee.save()
                messages.success(request, 'Discount updated successfully!')
                return redirect('application:edit_installment_setup', franchise_pk=franchise.pk, batch_pk=batch.pk, user_pk=user.pk)
        else:
            formset = EditInstallmentFormSet(
                request.POST,
                queryset=Installment.objects.filter(student_fee_management=student_fee)
            )

            if formset.is_valid():
                try:
                    with transaction.atomic():
                        instances = formset.save(commit=False)

                        for obj in formset.deleted_objects:
                            obj.delete()

                        for instance in instances:
                            if not instance.pk:
                                instance.student_fee_management = student_fee
                                instance.status = 'pending'
                                instance.due_date = timezone.now().date()
                            instance.save()

                        all_installments = Installment.objects.filter(
                            student_fee_management=student_fee
                        ).order_by('id')

                        cumulative_days = 0
                        for installment in all_installments:
                            cumulative_days += installment.repayment_period_days
                            installment.due_date = registration_date + timedelta(days=cumulative_days)
                            installment.save()

                        messages.success(request, 'Installments updated successfully!')
                        return redirect('application:student_fee_management',
                                      franchise_pk=franchise.pk,
                                      batch_pk=batch.pk,
                                      user_pk=user.pk)

                except Exception as e:
                    messages.error(request, f'Error updating installments: {str(e)}')
            else:
                messages.error(request, 'Please correct the errors below.')

    else:
        formset = EditInstallmentFormSet(
            queryset=Installment.objects.filter(student_fee_management=student_fee)
        )

    current_installments = Installment.objects.filter(student_fee_management=student_fee)
    total_installment_amount = sum(inst.amount for inst in current_installments)
    amount_to_add = student_fee.remaining_amount - total_installment_amount
    amount_to_add_absolute = abs(amount_to_add)
    show_reports_button = has_permission(request.user, VIEW_PERMISSIONS['homepage'])
    show_franchise_button = has_permission(request.user, VIEW_PERMISSIONS['franchise_list'])
    show_receipt_button = has_permission(request.user, VIEW_PERMISSIONS['receipt_search'])

    return render(request, 'application/edit_installment_setup.html', {
        'franchise': franchise,
        'batch': batch,
        'user': user,
        'formset': formset,
        'discount_form': discount_form,
        'student_fee': student_fee,
        'fee_management': fee_management,
        'enrollment': enrollment,
        'total_installment_amount': total_installment_amount,
        'amount_to_add': amount_to_add,
        'amount_to_add_absolute': amount_to_add_absolute,
        'batch_discount': fee_management.discount,
        'additional_discount': student_fee.discount - fee_management.discount if student_fee.discount > fee_management.discount else 0,
        'total_discount': student_fee.discount,
        'show_reports_button': show_reports_button,
        'show_franchise_button': show_franchise_button,
        'show_receipt_button': show_receipt_button,
    })

@login_required
def print_installment_invoice(request, franchise_pk, batch_pk, user_pk, installment_pk):
    if not has_permission(request.user, 'process_payment'):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to print invoices"
        }, status=403)
    
    installment = get_object_or_404(
        Installment.objects.select_related(
            'student_fee_management__user_franchise__user',
            'student_fee_management__batch_fee_management__batch__franchise'
        ),
        pk=installment_pk,
        status='paid'
    )

    student_fee = installment.student_fee_management
    user_franchise = student_fee.user_franchise
    user = user_franchise.user
    batch = student_fee.batch_fee_management.batch
    franchise = batch.franchise
    fee_management = student_fee.batch_fee_management

    all_installments = Installment.objects.filter(student_fee_management=student_fee)
    total_paid = sum(inst.payed_amount for inst in all_installments)
    installment_balance = installment.amount - installment.payed_amount

    return render(request, 'application/print_installment_invoice.html', {
        'franchise': franchise,
        'batch': batch,
        'user': user,
        'fee_management': fee_management,
        'installment': installment,
        'total_paid': total_paid,
        'installment_balance': installment_balance,
    })

@login_required
def receipt_detail(request, franchise_id):
    if not has_permission(request.user, VIEW_PERMISSIONS['receipt_detail']):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to access receipt details"
        }, status=403)
    
    user_franchise = get_object_or_404(UserFranchise, id=franchise_id)
    user = user_franchise.user
    
    try:
        user_profile = UserProfile.objects.get(user=user)
    except UserProfile.DoesNotExist:
        user_profile = None

    all_user_franchises = UserFranchise.objects.filter(user=user).select_related('batch', 'franchise')

    if request.method == 'POST':
        action = request.POST.get('action')
        uf_id = request.POST.get('user_franchise_id')
        if uf_id and action in ['enroll', 'unenroll']:
            uf = get_object_or_404(UserFranchise, id=uf_id)
            batch = uf.batch
            course_id = batch.course.id if batch and batch.course else None
            if action == 'enroll':
                if course_id and not CourseEnrollment.is_enrolled(user, course_id):
                    CourseEnrollment.enroll(user, course_id)
            elif action == 'unenroll':
                if course_id and CourseEnrollment.is_enrolled(user, course_id):
                    CourseEnrollment.unenroll(user, course_id)
            return redirect('application:receipt_detail', franchise_id=franchise_id)

        payment_amount_str = request.POST.get('payment_amount', '').strip()
        uf_id = request.POST.get('user_franchise_id')
        
        try:
            payment_amount = Decimal(payment_amount_str)
            if payment_amount <= 0:
                raise ValueError
        except ValueError:
            messages.error(request, "Please enter a valid positive payment amount.")
            return redirect('application:receipt_detail', franchise_id=franchise_id)

        if not uf_id:
            messages.error(request, "Invalid user franchise.")
            return redirect('application:receipt_detail', franchise_id=franchise_id)

        uf = get_object_or_404(UserFranchise, id=uf_id)

        try:
            student_fee = StudentFeeManagement.objects.get(user_franchise=uf)
        except StudentFeeManagement.DoesNotExist:
            messages.error(request, "Student fee management record not found.")
            return redirect('application:receipt_detail', franchise_id=franchise_id)

        pending_installments = Installment.objects.filter(
            student_fee_management=student_fee,
            status__in=['pending', 'overdue']
        ).order_by('due_date')

        remaining_payment = payment_amount
        affected_installments = []
        
        for installment in pending_installments:
            if remaining_payment <= 0:
                break
            due = installment.amount - installment.payed_amount
            if due > 0:
                add_payment = min(remaining_payment, due)
                installment.payed_amount += add_payment
                remaining_payment -= add_payment
                
                if installment.payed_amount >= installment.amount:
                    installment.status = 'paid'
                    if not installment.payment_date:
                        installment.payment_date = timezone.now().date()
                
                installment.save()
                affected_installments.append(installment.id)

        total_paid = sum(inst.payed_amount for inst in Installment.objects.filter(student_fee_management=student_fee))
        student_fee.remaining_amount = student_fee.batch_fee_management.remaining_amount - total_paid
        student_fee.save()

        request.session['payment_just_made'] = True
        request.session['last_payment_amount'] = float(payment_amount)
        request.session['affected_installments'] = affected_installments
        request.session['payment_date'] = timezone.now().date().isoformat()
        request.session['payment_user_franchise_id'] = uf_id

        messages.success(request, f"Payment of ₹{payment_amount} applied successfully.")
        return redirect('application:receipt_detail', franchise_id=franchise_id)

    user_franchise_data = []
    for uf in all_user_franchises:
        installments = []
        is_enrolled = False
        try:
            student_fee = StudentFeeManagement.objects.get(user_franchise=uf)
            installments = Installment.objects.filter(student_fee_management=student_fee).order_by('due_date')

            for installment in installments:
                if installment.status == 'paid':
                    installment.remaining_amount = 0
                else:
                    installment.remaining_amount = float(installment.amount) - float(installment.payed_amount)

        except StudentFeeManagement.DoesNotExist:
            pass

        batch = uf.batch
        course_id = batch.course.id if batch and batch.course else None
        if course_id:
            is_enrolled = CourseEnrollment.is_enrolled(user, course_id)

        user_franchise_data.append({
            'user_franchise': uf,
            'installments': installments,
            'is_enrolled': is_enrolled,
        })

    payment_just_made = request.session.get('payment_just_made', False)
    last_payment_amount = request.session.get('last_payment_amount', 0)
    payment_user_franchise_id = request.session.get('payment_user_franchise_id')
    show_reports_button = has_permission(request.user, VIEW_PERMISSIONS['homepage'])
    show_franchise_button = has_permission(request.user, VIEW_PERMISSIONS['franchise_list'])
    show_receipt_button = has_permission(request.user, VIEW_PERMISSIONS['receipt_search']) 

    return render(request, 'application/receipt_detail.html', {
        'user': user,
        'user_profile': user_profile,
        'user_franchise_data': user_franchise_data,
        'payment_just_made': payment_just_made,
        'last_payment_amount': last_payment_amount,
        'payment_user_franchise_id': payment_user_franchise_id,
        'franchise_id': franchise_id, 
        "registration_number": user_franchise.registration_number,
        'show_reports_button': show_reports_button,
        'show_franchise_button': show_franchise_button,
        'show_receipt_button': show_receipt_button,
    })

@login_required
def clear_payment_session(request, franchise_id):
    if not has_permission(request.user, 'process_payment'):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to clear payment sessions"
        }, status=403)
    
    if 'payment_just_made' in request.session:
        del request.session['payment_just_made']
    if 'last_payment_amount' in request.session:
        del request.session['last_payment_amount']
    if 'affected_installments' in request.session:
        del request.session['affected_installments']
    if 'payment_date' in request.session:
        del request.session['payment_date']
    if 'payment_user_franchise_id' in request.session:
        del request.session['payment_user_franchise_id']
    
    return redirect('application:receipt_detail', franchise_id=franchise_id)

@login_required
def receipt_search_api(request):
    if not has_permission(request.user, 'process_payment'):
        return JsonResponse({'error': 'Permission denied'}, status=403)
    
    query = request.GET.get('q', '').strip()
    results = []

    if query:
        user_franchises = UserFranchise.objects.select_related('user', 'batch').filter(
            Q(registration_number__icontains=query) |
            Q(user__email__icontains=query) |
            Q(user__first_name__icontains=query) |
            Q(user__last_name__icontains=query) |
            Q(user__username__icontains=query)
        )

        user_profiles = UserProfile.objects.filter(phone_number__icontains=query)
        user_ids_from_profile = [up.user_id for up in user_profiles]
        user_franchises = user_franchises | UserFranchise.objects.filter(user_id__in=user_ids_from_profile)
        user_franchises = user_franchises.distinct()[:15]

        profiles = {
            p.user_id: p.phone_number
            for p in UserProfile.objects.filter(user__in=[uf.user for uf in user_franchises])
        }

        for uf in user_franchises:
            results.append({
                "id": uf.id,
                "registration_number": uf.registration_number,
                "name": uf.user.get_full_name(),
                "email": uf.user.email,
                "phone": profiles.get(uf.user_id, "N/A"),
                "batch": uf.batch.batch_no if uf.batch else "",
                "detail_url": reverse("application:receipt_detail", args=[uf.id]),
            })

    return JsonResponse({"results": results})

@login_required
def print_receipt_detail(request, franchise_id):
    if not has_permission(request.user, 'process_payment'):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to print receipts"
        }, status=403)
    
    user_franchise = get_object_or_404(UserFranchise, id=franchise_id)
    try:
        user_profile = UserProfile.objects.get(user=user_franchise.user)
    except UserProfile.DoesNotExist:
        user_profile = None

    installments = []
    total_paid = 0
    total_pending = 0
    total_amount = 0
    last_payment_date = None

    try:
        student_fee = StudentFeeManagement.objects.get(user_franchise=user_franchise)
        installments = Installment.objects.filter(student_fee_management=student_fee).order_by('due_date')

        for installment in installments:
            total_amount += installment.amount
            if installment.status == 'paid':
                total_paid += installment.payed_amount
                if installment.payment_date and (not last_payment_date or installment.payment_date > last_payment_date):
                    last_payment_date = installment.payment_date
            else:
                total_pending += (installment.amount - installment.payed_amount)

    except StudentFeeManagement.DoesNotExist:
        pass

    return render(request, 'application/print_receipt_detail.html', {
        'user_franchise': user_franchise,
        'user_profile': user_profile,
        'installments': installments,
        'total_paid': total_paid,
        'total_pending': total_pending,
        'total_amount': total_amount,
        'last_payment_date': last_payment_date,
    })

@login_required
def print_payment_detail(request, franchise_id):
    if not has_permission(request.user, 'process_payment'):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to print payment details"
        }, status=403)
    
    payment_franchise_id = request.session.get('payment_user_franchise_id')
    if payment_franchise_id:
        user_franchise = get_object_or_404(UserFranchise, id=payment_franchise_id)
    else:
        user_franchise = get_object_or_404(UserFranchise, id=franchise_id)
        
    try:
        user_profile = UserProfile.objects.get(user=user_franchise.user)
    except UserProfile.DoesNotExist:
        user_profile = None

    last_payment_amount = request.session.get('last_payment_amount', 0)
    affected_installment_ids = request.session.get('affected_installments', [])
    payment_date_str = request.session.get('payment_date')

    payment_date = timezone.now().date()
    if payment_date_str:
        try:
            payment_date = datetime.fromisoformat(payment_date_str).date()
        except:
            pass

    try:
        student_fee = StudentFeeManagement.objects.get(user_franchise=user_franchise)
        installments = Installment.objects.filter(student_fee_management=student_fee).order_by('due_date')
        recent_payments = Installment.objects.filter(
            id__in=affected_installment_ids,
            student_fee_management=student_fee
        ).order_by('due_date')
    except StudentFeeManagement.DoesNotExist:
        installments = []
        recent_payments = []

    request.session.pop('payment_just_made', None)
    request.session.pop('last_payment_amount', None)
    request.session.pop('affected_installments', None)
    request.session.pop('payment_date', None)

    return render(request, 'application/print_payment_detail.html', {
        'user_franchise': user_franchise,
        'user_profile': user_profile,
        'last_payment_amount': last_payment_amount,
        'payment_date': payment_date,
        'installments': installments,
        'recent_payments': recent_payments,
    })

@login_required
def combined_fees_report(request):
    if not has_permission(request.user, VIEW_PERMISSIONS['combined_fees_report']):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to view combined fee reports"
        }, status=403)

    franchise_id = request.GET.get('franchise_id')
    batch_id = request.GET.get('batch_id')
    month = request.GET.get('month')
    year = request.GET.get('year')

    if franchise_id in ('', 'None'):
        franchise_id = None
    if batch_id in ('', 'None'):
        batch_id = None

    today = timezone.now().date()
    all_franchises = get_allowed_franchises(request.user)

    selected_month = None
    if month and year:
        try:
            selected_month = datetime(year=int(year), month=int(month), day=1).date()
        except ValueError:
            selected_month = None

    MONTH_CHOICES = [
        (1, 'January'), (2, 'February'), (3, 'March'), (4, 'April'),
        (5, 'May'), (6, 'June'), (7, 'July'), (8, 'August'),
        (9, 'September'), (10, 'October'), (11, 'November'), (12, 'December')
    ]

    current_year = today.year
    YEAR_CHOICES = [y for y in range(current_year - 5, current_year + 6)]

    installments_queryset = Installment.objects.all()

    if franchise_id:
        installments_queryset = installments_queryset.filter(
            student_fee_management__user_franchise__franchise_id=franchise_id
        )

    if batch_id:
        installments_queryset = installments_queryset.filter(
            student_fee_management__user_franchise__batch_id=batch_id
        )

    if selected_month:
        installments_queryset = installments_queryset.filter(
            due_date__year=selected_month.year,
            due_date__month=selected_month.month
        )

    filtered_total_fees = installments_queryset.aggregate(total=Sum('amount'))['total'] or 0
    filtered_total_received = installments_queryset.aggregate(total=Sum('payed_amount'))['total'] or 0
    filtered_total_pending = filtered_total_fees - filtered_total_received
    overdue_installments = installments_queryset.filter(due_date__lt=today).exclude(status='paid')
    filtered_total_overdue = sum(inst.amount - inst.payed_amount for inst in overdue_installments) or 0

    franchises_queryset = Franchise.objects.prefetch_related(
        'batches__userfranchise_set__fee_management__installments'
    )
    if franchise_id:
        franchises_queryset = franchises_queryset.filter(id=franchise_id)

    franchise_data = []
    for franchise in franchises_queryset:
        franchise_received = franchise_pending = franchise_overdue = 0
        batches_data = []

        batches = franchise.batches.all()
        if batch_id:
            batches = batches.filter(id=batch_id)

        for batch in batches:
            batch_received = batch_pending = batch_overdue = 0
            for uf in batch.userfranchise_set.all():
                try:
                    student_fee = uf.fee_management
                    installments = student_fee.installments.all()
                    if selected_month:
                        installments = installments.filter(
                            due_date__year=selected_month.year,
                            due_date__month=selected_month.month
                        )
                    batch_received += sum(inst.payed_amount for inst in installments)
                    batch_pending += sum(inst.amount - inst.payed_amount for inst in installments)
                    batch_overdue += sum(inst.amount - inst.payed_amount for inst in installments.filter(due_date__lt=today).exclude(status='paid'))
                except StudentFeeManagement.DoesNotExist:
                    continue

            batches_data.append({
                'batch': batch,
                'received': batch_received,
                'pending': batch_pending,
                'overdue': batch_overdue,
            })
            franchise_received += batch_received
            franchise_pending += batch_pending
            franchise_overdue += batch_overdue

        franchise_data.append({
            'franchise': franchise,
            'batches': batches_data,
            'received': franchise_received,
            'pending': franchise_pending,
            'overdue': franchise_overdue,
        })

    students_dict = {}
    user_franchises_queryset = UserFranchise.objects.select_related('user')

    if franchise_id:
        user_franchises_queryset = user_franchises_queryset.filter(franchise_id=franchise_id)
    if batch_id:
        user_franchises_queryset = user_franchises_queryset.filter(batch_id=batch_id)

    for uf in user_franchises_queryset:
        user_id = uf.user.id
        try:
            student_fee = uf.fee_management
            installments = student_fee.installments.all()
            if selected_month:
                installments = installments.filter(
                    due_date__year=selected_month.year,
                    due_date__month=selected_month.month
                )
            total = sum(inst.amount for inst in installments)
            received = sum(inst.payed_amount for inst in installments)
            pending = total - received
            overdue = sum(inst.amount - inst.payed_amount for inst in installments.filter(due_date__lt=today).exclude(status='paid'))
        except StudentFeeManagement.DoesNotExist:
            total = received = pending = overdue = 0

        if user_id not in students_dict:
            try:
                profile = UserProfile.objects.get(user=uf.user)
                phone_number = profile.phone_number
            except UserProfile.DoesNotExist:
                phone_number = ''

            students_dict[user_id] = {
                'name': uf.user.get_full_name(),
                'username': uf.user.username,
                'phone_number': phone_number,
                'email': uf.user.email,
                'total_fees': 0,
                'received_fees': 0,
                'pending_fees': 0,
                'overdue_fees': 0,
                'user_franchise_id': uf.id,
            }

        students_dict[user_id]['total_fees'] += total
        students_dict[user_id]['received_fees'] += received
        students_dict[user_id]['pending_fees'] += pending
        students_dict[user_id]['overdue_fees'] += overdue

    students = list(students_dict.values())
    students = [s for s in students if s['total_fees'] > 0]

    paginator = Paginator(students, 20)
    page = request.GET.get('page')
    try:
        students_page = paginator.page(page)
    except PageNotAnInteger:
        students_page = paginator.page(1)
    except EmptyPage:
        students_page = paginator.page(paginator.num_pages)

    return render(request, 'application/combined_fees_report.html', {
        'franchise_data': franchise_data,
        'total_fees': filtered_total_fees,
        'total_received': filtered_total_received,
        'total_pending': filtered_total_pending,
        'total_overdue': filtered_total_overdue,
        'all_franchises': all_franchises,
        'selected_franchise_id': franchise_id,
        'selected_batch_id': batch_id,
        'selected_month': selected_month,
        'selected_year': int(year) if year else None,
        'months': MONTH_CHOICES,
        'years': YEAR_CHOICES,
        'students_page': students_page,
    })

@login_required
def get_batches_for_franchises(request):
    if not has_permission(request.user, 'view_franchise'):
        return JsonResponse({'error': 'Permission denied'}, status=403)
    
    franchise_ids = request.GET.getlist('franchise_ids[]')
    try:
        franchise_ids = [int(id) for id in franchise_ids]
    except ValueError:
        return JsonResponse({'error': 'Invalid franchise IDs'}, status=400)
    batches = Batch.objects.filter(franchise_id__in=franchise_ids).values('id', 'batch_no', 'franchise__name')
    return JsonResponse({'batches': list(batches)})

@login_required
def student_counts(request):
    if not has_permission(request.user, VIEW_PERMISSIONS['student_counts']):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to view student counts"
        }, status=403)

    franchises = get_allowed_franchises(request.user).prefetch_related('batches__userfranchise_set')
    franchise_data = []

    for franchise in franchises:
        batches = franchise.batches.all()
        batch_data = []

        for batch in batches:
            student_count = batch.userfranchise_set.count()
            batch_data.append({
                'batch_no': batch.batch_no,
                'student_count': student_count,
            })

        total_students = franchise.userfranchise_set.values('user').distinct().count()

        franchise_data.append({
            'name': franchise.name,
            'batches': batch_data,
            'total_students': total_students,
        })

    return render(request, 'application/student_counts.html', {
        'franchise_data': franchise_data,
    })

@login_required
def special_user_dashboard(request):
    if not has_permission(request.user, VIEW_PERMISSIONS['special_user_dashboard']):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to access the special user dashboard"
        }, status=403)

    user = request.user

    # For special access users, use allowed franchises/batches from SpecialAccessUser
    try:
        special_access = SpecialAccessUser.objects.get(user=user)
        assigned_franchises = special_access.allowed_franchises.all()
        assigned_batches = special_access.allowed_batches.all()
    except SpecialAccessUser.DoesNotExist:
        # Fallback to UserFranchise if not special access
        user_franchises = UserFranchise.objects.filter(user=user).select_related('franchise', 'batch')
        assigned_franchises = set(uf.franchise for uf in user_franchises if uf.franchise)
        assigned_batches = set(uf.batch for uf in user_franchises if uf.batch)

    if assigned_franchises:
        franchises = assigned_franchises
        batches = Batch.objects.filter(franchise__in=assigned_franchises)
        user_franchises_all = UserFranchise.objects.filter(franchise__in=assigned_franchises).select_related('user', 'batch')
    else:
        franchises = Franchise.objects.none()
        batches = Batch.objects.none()
        user_franchises_all = UserFranchise.objects.none()

    total_franchises = franchises.count()
    total_batches = batches.count()
    total_students = user_franchises_all.values('user').distinct().count()

    total_fees = 0
    total_received = 0
    total_pending = 0
    total_overdue = 0

    today = timezone.now().date()
    for franchise in franchises:
        franchise_installments = Installment.objects.filter(
            student_fee_management__user_franchise__franchise=franchise
        )
        total_fees += franchise_installments.aggregate(Sum('amount'))['amount__sum'] or 0
        total_received += franchise_installments.aggregate(Sum('payed_amount'))['payed_amount__sum'] or 0
        total_pending += sum(inst.amount - inst.payed_amount for inst in franchise_installments)
        overdue_installments = franchise_installments.filter(due_date__lt=today).exclude(status='paid')
        total_overdue += sum(inst.amount - inst.payed_amount for inst in overdue_installments)

    recent_payments = Payment.objects.filter(
        installment__student_fee_management__user_franchise__franchise__in=assigned_franchises
    ).select_related(
        'installment__student_fee_management__user_franchise__user',
        'installment__student_fee_management__user_franchise__batch'
    ).order_by('-payment_date')[:10]

    upcoming_dues = Installment.objects.filter(
        student_fee_management__user_franchise__franchise__in=assigned_franchises,
        due_date__gte=today,
        due_date__lte=today + timedelta(days=7),
        status__in=['pending', 'overdue']
    ).select_related(
        'student_fee_management__user_franchise__user',
        'student_fee_management__user_franchise__batch'
    ).order_by('due_date')[:10]

    context = {
        'total_franchises': total_franchises,
        'total_batches': total_batches,
        'total_students': total_students,
        'total_fees': total_fees,
        'total_received': total_received,
        'total_pending': total_pending,
        'total_overdue': total_overdue,
        'franchises': franchises,
        'batches': batches,
        'recent_payments': recent_payments,
        'upcoming_dues': upcoming_dues,
        'assigned_franchises': assigned_franchises,
        'assigned_batches': assigned_batches,
    }

    return render(request, 'application/special_user_dashboard.html', context)

@login_required
def enroll_existing_user_general(request):
    if not has_permission(request.user, 'add_student'):
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to enroll users"
        }, status=403)

    franchises = Franchise.objects.all()
    user_search_results = []
    search_query = request.GET.get('search_query', '')

    if search_query:
        from common.djangoapps.student.models import UserProfile
        profiles = UserProfile.objects.filter(phone_number__icontains=search_query)
        user_ids_from_profile = [p.user_id for p in profiles]

        users_by_fields = User.objects.filter(
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query) |
            Q(email__icontains=search_query) |
            Q(username__icontains=search_query)
        )

        users_by_phone = User.objects.filter(id__in=user_ids_from_profile)

        all_users = (users_by_fields | users_by_phone).distinct()[:10]
        user_search_results = all_users

    if request.method == 'POST':
        user_ids = request.POST.getlist('user_ids')
        franchise_id = request.POST.get('franchise')
        batch_id = request.POST.get('batch')

        if not user_ids:
            messages.error(request, 'Please select at least one user.')
        elif franchise_id and batch_id:
            try:
                franchise = Franchise.objects.get(id=franchise_id)
                batch = Batch.objects.get(id=batch_id)

                if batch.franchise != franchise:
                    messages.error(request, 'Selected batch does not belong to the selected franchise.')
                else:
                    enrolled_users = []
                    already_enrolled = []
                    for user_id in user_ids:
                        user = User.objects.get(id=user_id)
                        if UserFranchise.objects.filter(user=user, franchise=franchise, batch=batch).exists():
                            already_enrolled.append(user.get_full_name())
                        else:
                            user_franchise = UserFranchise.objects.create(
                                user=user,
                                franchise=franchise,
                                batch=batch,
                                registration_number=user.username
                            )

                            fee_management = BatchFeeManagement.objects.get(batch=batch)
                            student_fee = StudentFeeManagement.objects.create(
                                user_franchise=user_franchise,
                                batch_fee_management=fee_management,
                                discount=fee_management.discount
                            )

                            CourseEnrollment.enroll(user, batch.course.id)

                            enrollment = CourseEnrollment.objects.get(user=user, course_id=batch.course.id)
                            registration_date = enrollment.created.date()
                            templates = InstallmentTemplate.objects.filter(batch_fee_management=fee_management).order_by('id')
                            cumulative_days = 0
                            for template in templates:
                                cumulative_days += template.repayment_period_days
                                due_date = registration_date + timedelta(days=cumulative_days)
                                Installment.objects.create(
                                    student_fee_management=student_fee,
                                    due_date=due_date,
                                    amount=template.amount,
                                    repayment_period_days=template.repayment_period_days,
                                    status='pending'
                                )

                            enrolled_users.append(user.get_full_name())

                    if enrolled_users:
                        messages.success(request, f'Successfully enrolled {", ".join(enrolled_users)} in {batch.batch_no}.')
                    if already_enrolled:
                        messages.warning(request, f'Users {", ".join(already_enrolled)} are already enrolled in this batch.')

                    if enrolled_users:
                        return redirect('application:enroll_existing_user_general')

            except Exception as e:
                messages.error(request, f'Error enrolling users: {str(e)}')

        return render(request, 'application/enroll_existing_user_general.html', {
            'franchises': franchises,
            'user_search_results': user_search_results,
            'search_query': search_query,
        })

    return render(request, 'application/enroll_existing_user_general.html', {
        'franchises': franchises,
        'user_search_results': user_search_results,
        'search_query': search_query,
    })

@login_required
@superuser_or_special_required
def edit_role(request, group_id):
    if not has_permission(request.user, 'auth.change_group'):
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': 'Permission denied'}, status=403)
        return render(request, 'application/access_denied.html', {
            'message': "You don't have permission to edit roles"
        }, status=403)

    group = get_object_or_404(Group, id=group_id)

    if request.method == 'POST':
        form = RoleForm(request.POST, instance=group)
        if form.is_valid():
            form.save()
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'success': True})
            messages.success(request, 'Role updated successfully!')
            return redirect('application:roles')
        else:
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'errors': form.errors})
            messages.error(request, 'Please correct the errors below.')
    else:
        form = RoleForm(instance=group)

    # Define custom permission order
    custom_order = [
        'specialaccessuser',
        'franchise',
        'batch',
        'batchfeemanagement',
        'userfranchise',
        'studentfeemanagement',
        'installment',
        'installmenttemplate',
        'coursefee',
        'payment',
    ]

    # Order permissions by custom order
    order_case = Case(
        *[When(content_type__model=model, then=Value(i)) for i, model in enumerate(custom_order)],
        default=Value(len(custom_order)),
        output_field=IntegerField()
    )

    permissions = Permission.objects.filter(
        content_type__app_label='application'
    ).annotate(custom_order=order_case).order_by('custom_order', 'content_type__model', 'codename')

    # Pre-select permissions already assigned to this group
    form.fields['permissions'].initial = group.permissions.all()

    return render(request, 'application/edit_role.html', {
        'form': form,
        'group': group,
        'permissions': permissions,
    })

@login_required
@superuser_or_special_required
def delete_role(request, group_id):
    if not has_permission(request.user, 'auth.change_group'):
        return JsonResponse({'success': False, 'error': 'Permission denied'})
    
    if request.method == 'POST':
        group = get_object_or_404(Group, id=group_id)
        group.delete()
        return JsonResponse({'success': True})
    return JsonResponse({'success': False})

@login_required
def get_batch_franchise(request, batch_id):
    """Get franchise ID for a batch"""
    if not has_permission(request.user, 'view_franchise'):
        return JsonResponse({'error': 'Permission denied'}, status=403)
    
    try:
        batch = Batch.objects.get(id=batch_id)
        return JsonResponse({'franchise_id': str(batch.franchise.id)})
    except Batch.DoesNotExist:
        return JsonResponse({'error': 'Batch not found'}, status=404)