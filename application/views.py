from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.models import User
from django.db import models
from django.http import JsonResponse, HttpResponseForbidden
from .forms import FranchiseForm, BatchForm, FranchiseUserRegistrationForm, BatchFeeManagementForm, StudentFeeManagementForm, InstallmentForm, EditInstallmentForm, PaymentForm, StudentEditForm,StudentDiscountForm, SpecialAccessRegistrationForm, SpecialAccessUserRegistrationForm
from .models import Franchise, UserFranchise, Batch, BatchFeeManagement, StudentFeeManagement, Installment, InstallmentTemplate, CourseFee, SpecialAccessUser
from django.contrib.auth.decorators import login_required, user_passes_test
from collections import defaultdict
from django.db.models import Count
from django.urls import reverse
from django.forms import modelformset_factory
from datetime import timedelta
from django.utils import timezone
from django.db import OperationalError, transaction
from time import sleep
from django.db.models import Q
from decimal import Decimal
from django.core.paginator import Paginator, PageNotAnInteger, EmptyPage
from django.db.models import Sum
from django.db.models.functions import TruncMonth
from django.core.exceptions import PermissionDenied



from common.djangoapps.student.models import UserProfile

from common.djangoapps.student.models import CourseEnrollment
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview


def superuser_required(view_func):
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')  # Redirect to login page for unauthenticated users
        if not request.user.is_superuser:
            return render(request, 'application/access_denied.html', status=403)  # Custom access denied page
        return view_func(request, *args, **kwargs)
    return _wrapped_view

def superuser_or_amal_required(view_func):
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')  # Redirect to login page for unauthenticated users
        if not (request.user.is_superuser or SpecialAccessUser.objects.filter(user=request.user).exists()):
            return render(request, 'application/access_denied.html', status=403)  # Custom access denied page
        return view_func(request, *args, **kwargs)
    return _wrapped_view



@login_required
@superuser_or_amal_required
def homepage(request):
    total_franchises = Franchise.objects.count()
    total_students = UserFranchise.objects.values('user').distinct().count()
    total_courses = CourseOverview.objects.count()

    return render(request, 'application/homepage.html', {
        'total_franchises': total_franchises,
        'total_students': total_students,
        'total_courses': total_courses
    })

@login_required
@superuser_required
def fee_report(request):
    franchise_id = request.GET.get('franchise_id')
    batch_id = request.GET.get('batch_id')
    all_franchises = Franchise.objects.all()
    today = timezone.now().date()

    # Global totals (always full)
    total_fees = Installment.objects.aggregate(total=Sum('amount'))['total'] or 0
    total_received = Installment.objects.aggregate(total=Sum('payed_amount'))['total'] or 0
    total_pending = total_fees - total_received
    overdue_installments = Installment.objects.filter(due_date__lt=today).exclude(status='paid')
    total_overdue = sum(inst.amount - inst.payed_amount for inst in overdue_installments) or 0

    if batch_id:
        # Filter by batch and its franchise
        try:
            batch = Batch.objects.get(id=batch_id)
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
        franchises = Franchise.objects.prefetch_related(
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


@login_required
@superuser_required
def franchise_fees_report(request):
    franchise_id = request.GET.get('franchise_id')
    batch_id = request.GET.get('batch_id')
    if franchise_id == '' or franchise_id == 'None':
        franchise_id = None
    if batch_id == '' or batch_id == 'None':
        batch_id = None
    all_franchises = Franchise.objects.all()
    today = timezone.now().date()

    # Global totals (always full)
    total_fees = Installment.objects.aggregate(total=Sum('amount'))['total'] or 0
    total_received = Installment.objects.aggregate(total=Sum('payed_amount'))['total'] or 0
    total_pending = total_fees - total_received
    overdue_installments = Installment.objects.filter(due_date__lt=today).exclude(status='paid')
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
        filtered_total_fees = franchise_installments.aggregate(total=Sum('amount'))['total'] or 0
        filtered_total_received = franchise_installments.aggregate(total=Sum('payed_amount'))['total'] or 0
        filtered_total_pending = filtered_total_fees - filtered_total_received
        overdue_franchise_installments = franchise_installments.filter(due_date__lt=today).exclude(status='paid')
        filtered_total_overdue = sum(inst.amount - inst.payed_amount for inst in overdue_franchise_installments) or 0

    if batch_id:
        # Filter by batch and its franchise
        try:
            batch = Batch.objects.get(id=batch_id)
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
        franchises = Franchise.objects.prefetch_related(
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

    from common.djangoapps.student.models import UserProfile

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
    paginator = Paginator(students, 20)  # 20 students per page
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
@superuser_required
def monthly_fees_report(request):
    month = request.GET.get('month')  
    year = request.GET.get('year')    

    today = timezone.now().date()
    all_franchises = Franchise.objects.all()

    selected_month = None
    if month and year:
        try:
            selected_month = datetime(year=int(year), month=int(month), day=1).date()
        except ValueError:
            selected_month = None

    # -------------------------
    # Prepare month & year choices
    # -------------------------
    MONTH_CHOICES = [
        (1, 'January'), (2, 'February'), (3, 'March'), (4, 'April'),
        (5, 'May'), (6, 'June'), (7, 'July'), (8, 'August'),
        (9, 'September'), (10, 'October'), (11, 'November'), (12, 'December')
    ]

    current_year = today.year
    YEAR_CHOICES = [y for y in range(current_year - 5, current_year + 6)]  # last 10 + next 5 years

    # -------------------------
    # Calculate totals
    # -------------------------
    total_fees = Installment.objects.aggregate(total=Sum('amount'))['total'] or 0
    total_received = Installment.objects.aggregate(total=Sum('payed_amount'))['total'] or 0
    total_pending = total_fees - total_received
    overdue_installments = Installment.objects.filter(due_date__lt=today).exclude(status='paid')
    total_overdue = sum(inst.amount - inst.payed_amount for inst in overdue_installments) or 0

    # Filter installments by selected month/year if provided
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

    # -------------------------
    # Prepare student table
    # -------------------------
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
        # Only include students with fees if month selected
        if selected_month and (student['total_fees'] > 0 or student['received_fees'] > 0 or student['pending_fees'] > 0 or student['overdue_fees'] > 0):
            students.append(student)
        elif not selected_month:
            students.append(student)

    # Paginate students
    paginator = Paginator(students, 20)
    page = request.GET.get('page')
    try:
        students_page = paginator.page(page)
    except PageNotAnInteger:
        students_page = paginator.page(1)
    except EmptyPage:
        students_page = paginator.page(paginator.num_pages)

    # -------------------------
    # Prepare franchise summary (optional)
    # -------------------------
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
@superuser_required
def course_fee_list(request):
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
@superuser_required
def fee_reminders(request):
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

    today = timezone.now().date()
    three_days_later = today + timedelta(days=3)

    upcoming_installments = Installment.objects.filter(
        due_date__gte=today,
        due_date__lte=three_days_later,
        status='pending'
    ).select_related('student_fee_management__user_franchise__user', 'student_fee_management__user_franchise__batch')

    overdue_installments = Installment.objects.filter(
        due_date__lt=today
    ).exclude(status='paid').select_related('student_fee_management__user_franchise__user', 'student_fee_management__user_franchise__batch')

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
    })


@login_required
@superuser_required
def inactive_users(request):
    # Get filter parameters
    days_min = request.GET.get('days_min', '').strip()
    franchise_id = request.GET.get('franchise_id', '').strip()
    batch_id = request.GET.get('batch_id', '').strip()

    # Set default days_min to '2' if not provided
    if not days_min:
        days_min = '2'

    # Get users who haven't logged in for the last 2 days
    two_days_ago = timezone.now() - timedelta(days=2)
    inactive_users = User.objects.filter(
        models.Q(last_login__isnull=True) | models.Q(last_login__lt=two_days_ago)
    ).filter(userfranchise__isnull=False).distinct().order_by('last_login')

    # Apply franchise filter
    if franchise_id:
        inactive_users = inactive_users.filter(userfranchise__franchise_id=franchise_id)

    # Apply batch filter
    if batch_id:
        inactive_users = inactive_users.filter(userfranchise__batch_id=batch_id)

    # Add pagination
    paginator = Paginator(inactive_users, 20)  # 20 users per page
    page = request.GET.get('page')

    try:
        users_page = paginator.page(page)
    except PageNotAnInteger:
        users_page = paginator.page(1)
    except EmptyPage:
        users_page = paginator.page(paginator.num_pages)

    # Calculate days since last login for each user
    user_data = []
    now = timezone.now()
    for user in users_page:
        if user.last_login:
            days_inactive = (now - user.last_login).days
        else:
            days_inactive = None  # Never logged in

        # Get phone number from UserProfile
        try:
            profile = UserProfile.objects.get(user=user)
            phone_number = profile.phone_number
        except UserProfile.DoesNotExist:
            phone_number = None

        # Get batch and franchise from UserFranchise
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

    # Apply days filter
    if days_min:
        try:
            days_min_int = int(days_min)
            user_data = [d for d in user_data if d['days_inactive'] is None or (d['days_inactive'] is not None and d['days_inactive'] >= days_min_int)]
        except ValueError:
            pass

    # Get options for filters
    all_franchises = Franchise.objects.all()
    batches = Batch.objects.filter(franchise_id=franchise_id) if franchise_id else Batch.objects.none()

    return render(request, 'application/inactive_users.html', {
        'user_data': user_data,
        'two_days_ago': two_days_ago,
        'users_page': users_page,  # For pagination info
        'all_franchises': all_franchises,
        'batches': batches,
        'current_days_min': days_min,
        'current_franchise_id': franchise_id,
        'current_batch_id': batch_id,
    })


@login_required
@superuser_required
def franchise_list(request):
    franchises = Franchise.objects.all()
    return render(request, 'application/franchise_management.html', {'franchises': franchises})


@login_required
@superuser_required
def franchise_register(request):
    if request.method == "POST":
        form = FranchiseForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('application:franchise_list')
    else:
        form = FranchiseForm()
    
    return render(request, 'application/franchise_register.html', {'form': form})


@login_required
@superuser_required
def franchise_edit(request, pk):
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
@superuser_required
def franchise_report(request, pk):
    franchise = get_object_or_404(Franchise, pk=pk)

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

    batches = Batch.objects.filter(franchise=franchise).select_related('course')

    return render(request, 'application/franchise_report.html', {
        'franchise': franchise,
        'courses': courses,
        'users': users,
        'batches': batches,
    })


@login_required
@superuser_required
def batch_create(request, pk):
    franchise = get_object_or_404(Franchise, pk=pk)

    if request.method == "POST":
        form = BatchForm(request.POST)
        if form.is_valid():
            batch = form.save(commit=False)
            batch.franchise = franchise
            # Set fees from course fee
            course_fee, created = CourseFee.objects.get_or_create(course=batch.course, defaults={'fee': 0})
            batch.fees = course_fee.fee
            batch.save()

            # Create BatchFeeManagement automatically with discount from form
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
@superuser_required
def batch_students(request, franchise_pk, batch_pk):
    franchise = get_object_or_404(Franchise, pk=franchise_pk)
    batch = get_object_or_404(Batch, pk=batch_pk, franchise=franchise)

    user_franchises = UserFranchise.objects.filter(franchise=franchise, batch=batch).select_related('user')
    users = [uf.user for uf in user_franchises]

    return render(request, 'application/batch_students.html', {
        'franchise': franchise,
        'batch': batch,
        'users': users,
    })


@login_required
@superuser_required
def student_detail(request, franchise_pk, batch_pk, user_pk):
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

    return render(request, 'application/student_detail.html', {
        'franchise': franchise,
        'batch': batch,
        'user': user,
        'user_franchise': user_franchise,
        'fee_management': fee_management,
        'student_fee': student_fee,
        'installments': installments,
        'is_enrolled': is_enrolled,
    })


@login_required
@superuser_required
def edit_student_details(request, franchise_pk, batch_pk, user_pk):
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

    return render(request, 'application/edit_student_details.html', {
        'form': form,
        'franchise': franchise,
        'batch': batch,
        'user': user,
    })

@login_required
@superuser_or_amal_required

def user_register(request):
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
                    user = form.save(franchise=franchise, batch=batch, commit=True)
                    CourseEnrollment.enroll(user, batch.course.id)
                    return redirect('application:homepage')  # Or to a success page
            except (Franchise.DoesNotExist, Batch.DoesNotExist, ValueError):
                form.add_error(None, 'Invalid franchise or batch selected.')
    else:
        form = FranchiseUserRegistrationForm()

    franchises = Franchise.objects.all()

    return render(request, 'application/user_register.html', {
        'form': form,
        'franchises': franchises,
    })


@login_required
@superuser_or_amal_required
def get_batches(request, franchise_id):
    batches = Batch.objects.filter(franchise_id=franchise_id).values('id', 'batch_no')
    return JsonResponse({'batches': list(batches)})


@login_required
@superuser_required
def batch_user_register(request, franchise_pk, batch_pk):
    franchise = get_object_or_404(Franchise, pk=franchise_pk)
    batch = get_object_or_404(Batch, pk=batch_pk, franchise=franchise)

    if request.method == "POST":
        form = FranchiseUserRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save(franchise=franchise, batch=batch, commit=True)
            CourseEnrollment.enroll(user, batch.course.id)

            # Create StudentFeeManagement and Installments
            user_franchise = UserFranchise.objects.get(user=user, franchise=franchise, batch=batch)
            fee_management = get_object_or_404(BatchFeeManagement, batch=batch)
            student_fee = StudentFeeManagement.objects.create(
                user_franchise=user_franchise,
                batch_fee_management=fee_management,
                discount=fee_management.discount
            )
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
def enroll_existing_user(request, franchise_pk, batch_pk):
    franchise = get_object_or_404(Franchise, pk=franchise_pk)
    batch = get_object_or_404(Batch, pk=batch_pk, franchise=franchise)

    if request.method == "POST":
        user_ids = request.POST.getlist('user_ids')
        if user_ids:
            enrolled_users = []
            already_enrolled = []
            for user_id in user_ids:
                user = get_object_or_404(User, pk=user_id)
                # Check if already enrolled in this batch
                if UserFranchise.objects.filter(user=user, franchise=franchise, batch=batch).exists():
                    already_enrolled.append(user.get_full_name())
                    continue

                # Create UserFranchise
                user_franchise = UserFranchise.objects.create(user=user, franchise=franchise, batch=batch, registration_number=user.username)

                # Create StudentFeeManagement
                fee_management = get_object_or_404(BatchFeeManagement, batch=batch)
                student_fee = StudentFeeManagement.objects.create(
                    user_franchise=user_franchise,
                    batch_fee_management=fee_management,
                    discount=fee_management.discount
                )

                # Enroll in course
                CourseEnrollment.enroll(user, batch.course.id)

                # Create Installments based on templates
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

    # Handle search
    search_query = request.GET.get('search_query', '').strip()
    users = []
    if search_query:
        # To avoid MySQL error with LIMIT & IN subquery, fetch user ids separately
        from common.djangoapps.student.models import UserProfile
        profiles = UserProfile.objects.filter(phone_number__icontains=search_query)
        user_ids_from_profile = [p.user_id for p in profiles]

        users_by_fields = User.objects.filter(
            Q(email__icontains=search_query) |
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query) |
            Q(username__icontains=search_query)
        ).exclude(userfranchise__batch=batch)

        users_by_phone = User.objects.filter(id__in=user_ids_from_profile).exclude(userfranchise__batch=batch)

        # Combine querysets and apply distinct and limit
        users = (users_by_fields | users_by_phone).distinct()[:20]

    return render(request, 'application/enroll_existing_user.html', {
        'franchise': franchise,
        'batch': batch,
        'search_query': search_query,
        'users': users,
    })


@login_required
@superuser_required
def batch_fee_management(request, franchise_pk, batch_pk):
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
@superuser_required
def student_fee_management(request, franchise_pk, batch_pk, user_pk):
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
        # Validate that payments are marked in order and paid installments cannot be changed
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

                # New validation: if status is paid, payed amount must be > 0
                if new_status == 'paid' and new_payed_amount <= 0:
                    error_message = "Payed amount must be greater than zero to mark as paid."
                    break

                # If installment is already paid, status cannot be changed
                if installment.status == 'paid' and new_status != 'paid':
                    error_message = "Paid installments cannot be changed."
                    break

                # Enforce order: can only mark this installment as paid if all previous are paid
                if new_status == 'paid':
                    if i > 0 and existing_installments[i-1].status != 'paid':
                        error_message = "Payments must be marked in order."
                        break
                    last_paid_index = i

        if error_message:
            from django.contrib import messages
            messages.error(request, error_message)
        else:
            # Save changes if no errors
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
                        if installment.status != 'paid':  # Only update if not already paid
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
    })




from django.contrib import messages
from django.forms import modelformset_factory

@login_required
@superuser_required
def edit_installment_setup(request, franchise_pk, batch_pk, user_pk):
    franchise = get_object_or_404(Franchise, pk=franchise_pk)
    batch = get_object_or_404(Batch, pk=batch_pk, franchise=franchise)
    user = get_object_or_404(User, pk=user_pk)

    fee_management = get_object_or_404(BatchFeeManagement, batch=batch)
    user_franchise = get_object_or_404(UserFranchise, user=user, franchise=franchise, batch=batch)
    student_fee = get_object_or_404(StudentFeeManagement, user_franchise=user_franchise)

    # Get registration date
    enrollment = CourseEnrollment.objects.get(user=user, course_id=batch.course.id)
    registration_date = enrollment.created.date()

    # Define the formset - only include editable fields
    EditInstallmentFormSet = modelformset_factory(
        Installment,
        form=EditInstallmentForm,
        extra=0,
        can_delete=True,
        fields=['amount', 'repayment_period_days']  
    )

    discount_form = StudentDiscountForm(instance=student_fee)

    if request.method == "POST":
        action = request.POST.get('action')
        if action == 'save_discount':
            discount_form = StudentDiscountForm(request.POST, instance=student_fee)
            if discount_form.is_valid():
                discount_form.save()
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

                        # Process deleted instances
                        for obj in formset.deleted_objects:
                            obj.delete()

                        # First pass: Save all instances with temporary due_date
                        for instance in instances:
                            if not instance.pk:  # New instance
                                instance.student_fee_management = student_fee
                                instance.status = 'pending'
                                # Set a temporary due_date to avoid null constraint
                                instance.due_date = timezone.now().date()
                            instance.save()

                        # Now recalculate due dates for all installments properly
                        all_installments = Installment.objects.filter(
                            student_fee_management=student_fee
                        ).order_by('id')

                        cumulative_days = 0
                        for installment in all_installments:
                            cumulative_days += installment.repayment_period_days
                            installment.due_date = registration_date + timedelta(days=cumulative_days)
                            installment.save()

                        # Calculate total installment amount
                        total_installments = sum(
                            inst.amount for inst in Installment.objects.filter(
                                student_fee_management=student_fee
                            )
                        )

                        # Calculate amount to be added to match remaining amount
                        amount_to_add = student_fee.remaining_amount - total_installments

                        messages.success(request, f'Installments updated successfully! Amount to add: ₹{amount_to_add:.2f}')
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

    # Calculate current totals for display
    current_installments = Installment.objects.filter(student_fee_management=student_fee)
    total_installment_amount = sum(inst.amount for inst in current_installments)
    amount_to_add = student_fee.remaining_amount - total_installment_amount
    amount_to_add_absolute = abs(amount_to_add)  # Calculate absolute value for template

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
    })


@login_required
@superuser_required
def print_installment_invoice(request, franchise_pk, batch_pk, user_pk, installment_pk):
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

    # Calculate totals
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
@superuser_required
def receipt_search(request):
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

        from common.djangoapps.student.models import UserProfile
        user_profiles = UserProfile.objects.filter(phone_number__icontains=search_query)
        user_ids_from_profile = [up.user_id for up in user_profiles]
        user_franchises = user_franchises | UserFranchise.objects.filter(user_id__in=user_ids_from_profile)
        user_franchises = user_franchises.distinct()

    return render(request, 'application/receipt_search.html', {
        'search_query': search_query,
        'user_franchises': user_franchises,
    })

@login_required
@superuser_required
def get_course_fee(request, course_id):
    from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
    course = get_object_or_404(CourseOverview, id=course_id)
    fee_obj, created = CourseFee.objects.get_or_create(course=course, defaults={'fee': 0})
    return JsonResponse({'fee': float(fee_obj.fee)})

@login_required
@superuser_required
def receipt_detail(request, franchise_id):
    user_franchise = get_object_or_404(UserFranchise, id=franchise_id)
    user = user_franchise.user
    
    from common.djangoapps.student.models import UserProfile
    try:
        user_profile = UserProfile.objects.get(user=user)
    except UserProfile.DoesNotExist:
        user_profile = None

    # Get all UserFranchise for the user
    all_user_franchises = UserFranchise.objects.filter(user=user).select_related('batch', 'franchise')

    if request.method == 'POST':
        # Handle enrollment actions first
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

        # Handle payment processing
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

        # Process payment
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

        # Update student fee management
        total_paid = sum(inst.payed_amount for inst in Installment.objects.filter(student_fee_management=student_fee))
        student_fee.remaining_amount = student_fee.batch_fee_management.remaining_amount - total_paid
        student_fee.save()

        # Set session data for print functionality
        request.session['payment_just_made'] = True
        request.session['last_payment_amount'] = float(payment_amount)
        request.session['affected_installments'] = affected_installments
        request.session['payment_date'] = timezone.now().date().isoformat()
        request.session['payment_user_franchise_id'] = uf_id  # Track which franchise was paid

        messages.success(request, f"Payment of ₹{payment_amount} applied successfully.")
        return redirect('application:receipt_detail', franchise_id=franchise_id)

    # Prepare data for each user_franchise (AFTER processing POST)
    user_franchise_data = []
    for uf in all_user_franchises:
        installments = []
        is_enrolled = False
        try:
            student_fee = StudentFeeManagement.objects.get(user_franchise=uf)
            installments = Installment.objects.filter(student_fee_management=student_fee).order_by('due_date')

            # Calculate remaining amount for each installment
            for installment in installments:
                if installment.status == 'paid':
                    installment.remaining_amount = 0
                else:
                    installment.remaining_amount = float(installment.amount) - float(installment.payed_amount)

        except StudentFeeManagement.DoesNotExist:
            pass

        # Check enrollment status
        batch = uf.batch
        course_id = batch.course.id if batch and batch.course else None
        if course_id:
            is_enrolled = CourseEnrollment.is_enrolled(user, course_id)

        user_franchise_data.append({
            'user_franchise': uf,
            'installments': installments,
            'is_enrolled': is_enrolled,
        })

    # Check if payment was just made (for enabling print button)
    payment_just_made = request.session.get('payment_just_made', False)
    last_payment_amount = request.session.get('last_payment_amount', 0)
    payment_user_franchise_id = request.session.get('payment_user_franchise_id')

    # Clear session data after rendering (but keep for print functionality)
    # We'll clear it when user navigates away or after print

    return render(request, 'application/receipt_detail.html', {
        'user': user,
        'user_profile': user_profile,
        'user_franchise_data': user_franchise_data,
        'payment_just_made': payment_just_made,
        'last_payment_amount': last_payment_amount,
        'payment_user_franchise_id': payment_user_franchise_id,
        'franchise_id': franchise_id, 
         "registration_number": uf.registration_number, # Add this for context
        
    })

@login_required
@superuser_required
def clear_payment_session(request, franchise_id):
    """Clear payment session data"""
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
@superuser_required
def receipt_search_api(request):
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

        # also search by phone number
        user_profiles = UserProfile.objects.filter(phone_number__icontains=query)
        user_ids_from_profile = [up.user_id for up in user_profiles]
        user_franchises = user_franchises | UserFranchise.objects.filter(user_id__in=user_ids_from_profile)
        user_franchises = user_franchises.distinct()[:15]

        # ✅ preload all phone numbers into a dictionary
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
                "phone": profiles.get(uf.user_id, "N/A"),  # fallback to "N/A"
                "batch": uf.batch.batch_no if uf.batch else "",
                "detail_url": reverse("application:receipt_detail", args=[uf.id]),
            })

    return JsonResponse({"results": results})


@login_required
@superuser_required
def print_receipt_detail(request, franchise_id):
    user_franchise = get_object_or_404(UserFranchise, id=franchise_id)
    from common.djangoapps.student.models import UserProfile

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

        # Calculate totals
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
@superuser_required
def print_payment_detail(request, franchise_id):
    """Print only the payment details for recent payments"""
    # Use the correct user_franchise from session if payment was made to a specific course
    payment_franchise_id = request.session.get('payment_user_franchise_id')
    if payment_franchise_id:
        user_franchise = get_object_or_404(UserFranchise, id=payment_franchise_id)
    else:
        user_franchise = get_object_or_404(UserFranchise, id=franchise_id)
    from common.djangoapps.student.models import UserProfile

    try:
        user_profile = UserProfile.objects.get(user=user_franchise.user)
    except UserProfile.DoesNotExist:
        user_profile = None

    # Get recent payment information from session
    last_payment_amount = request.session.get('last_payment_amount', 0)
    affected_installment_ids = request.session.get('affected_installments', [])
    payment_date_str = request.session.get('payment_date')

    # Convert payment_date back to date object
    payment_date = timezone.now().date()
    if payment_date_str:
        try:
            from datetime import datetime
            payment_date = datetime.fromisoformat(payment_date_str).date()
        except:
            pass

    # Get student fee information
    try:
        student_fee = StudentFeeManagement.objects.get(user_franchise=user_franchise)
        installments = Installment.objects.filter(student_fee_management=student_fee).order_by('due_date')

        # Get the specific installments that were affected by the recent payment
        recent_payments = Installment.objects.filter(
            id__in=affected_installment_ids,
            student_fee_management=student_fee
        ).order_by('due_date')

    except StudentFeeManagement.DoesNotExist:
        installments = []
        recent_payments = []

    # Clear session data after printing to prevent reuse
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

from datetime import datetime  

@login_required
@superuser_required
def combined_fees_report(request):
    # -------------------------
    # Get filter parameters
    # -------------------------
    franchise_id = request.GET.get('franchise_id')
    batch_id = request.GET.get('batch_id')
    month = request.GET.get('month')   # 1-12
    year = request.GET.get('year')     # e.g., 2025

    if franchise_id in ('', 'None'):
        franchise_id = None
    if batch_id in ('', 'None'):
        batch_id = None

    today = timezone.now().date()
    all_franchises = Franchise.objects.all()

    # Convert month/year to a date object for filtering
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

    # -------------------------
    # Calculate totals
    # -------------------------
    filtered_total_fees = installments_queryset.aggregate(total=Sum('amount'))['total'] or 0
    filtered_total_received = installments_queryset.aggregate(total=Sum('payed_amount'))['total'] or 0
    filtered_total_pending = filtered_total_fees - filtered_total_received
    overdue_installments = installments_queryset.filter(due_date__lt=today).exclude(status='paid')
    filtered_total_overdue = sum(inst.amount - inst.payed_amount for inst in overdue_installments) or 0

    # -------------------------
    # Franchise breakdown
    # -------------------------
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

    # -------------------------
    # Prepare student table
    # -------------------------
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

    # Filter out students with zero total fees
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
@superuser_required
def special_access_register(request):
    if request.method == 'POST':
        if 'grant_access' in request.POST:
            form = SpecialAccessRegistrationForm(request.POST)
            if form.is_valid():
                user = form.cleaned_data['user']
                if not SpecialAccessUser.objects.filter(user=user).exists():
                    SpecialAccessUser.objects.create(user=user, granted_by=request.user)
                    messages.success(request, f'Special access granted to {user.username}.')
                else:
                    messages.warning(request, f'{user.username} already has special access.')
                return redirect('application:special_access_register')
        elif 'register_user' in request.POST:
            # Handle user registration
            form_data = SpecialAccessUserRegistrationForm(request.POST)
            if form_data.is_valid():
                user = form_data.save(commit=True)
                # Automatically grant special access to newly registered user
                SpecialAccessUser.objects.create(user=user, granted_by=request.user)
                messages.success(request, f'User {user.username} registered and granted special access.')
                return redirect('application:special_access_register')
            else:
                messages.error(request, 'Error registering user. Please check the form.')
                return redirect('application:special_access_register')

    form = SpecialAccessRegistrationForm()
    special_users = SpecialAccessUser.objects.select_related('user', 'granted_by').order_by('-granted_at')
    return render(request, 'application/special_access_register.html', {
        'form': form,
        'special_users': special_users,
    })


@login_required
@superuser_required
def student_counts(request):
    franchises = Franchise.objects.prefetch_related('batches__userfranchise_set').all()
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
@superuser_or_amal_required
def enroll_existing_user_general(request):
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

        # Re-render with current search if error
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
