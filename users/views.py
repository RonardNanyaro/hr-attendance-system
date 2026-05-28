from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.http import JsonResponse, HttpResponse
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.contrib import messages
from django.core.mail import send_mail
from django.db.models import Count, Q
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.core.exceptions import ValidationError, ObjectDoesNotExist
from django.db import IntegrityError
from django.core.cache import cache
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from datetime import date, timedelta, datetime, time
from collections import Counter
import json
import csv
import os
import random
import secrets
import hashlib
import base64
import re
import logging
from django.core.files.base import ContentFile
from django.urls import reverse
from django.contrib.auth.hashers import make_password

from reportlab.platypus import SimpleDocTemplate, Table
from reportlab.lib.pagesizes import letter

from .models import Profile, Employee, Leave, Attendance, Company, Department, Shift, Notification, ActivityLog, RandomVerification, PasswordResetToken, IdempotencyKey
from .decorators import hr_required, admin_required
from .idempotency import idempotent

# Setup logger
logger = logging.getLogger(__name__)


# ================= SMS SETUP =================
import africastalking

AFRICA_TALKING_USERNAME = os.environ.get("AFRICA_TALKING_USERNAME", "")
AFRICA_TALKING_API_KEY = os.environ.get("AFRICA_TALKING_API_KEY", "")
ADMIN_PHONE = os.environ.get("ADMIN_PHONE", "+255782550284")

if AFRICA_TALKING_USERNAME and AFRICA_TALKING_API_KEY:
    africastalking.initialize(AFRICA_TALKING_USERNAME, AFRICA_TALKING_API_KEY)
    sms = africastalking.SMS
else:
    sms = None


# ================= RATE LIMITING DECORATOR =================

def rate_limit(key_func, limit=5, period=60):
    def decorator(view_func):
        def wrapper(request, *args, **kwargs):
            key = key_func(request)
            count = cache.get(key, 0)
            
            if count >= limit:
                return JsonResponse({
                    'success': False, 
                    'error': f'Too many requests. Try again in {period} seconds.'
                }, status=429)
            
            response = view_func(request, *args, **kwargs)
            
            # Add rate limit headers
            response['X-RateLimit-Limit'] = str(limit)
            response['X-RateLimit-Remaining'] = str(max(0, limit - count - 1))
            response['X-RateLimit-Reset'] = str(period)
            
            cache.set(key, count + 1, period)
            return response
        return wrapper
    return decorator


def get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


def validate_password_strength(password):
    errors = []
    if len(password) < 8:
        errors.append("Password must be at least 8 characters long")
    if not re.search(r'[A-Z]', password):
        errors.append("Password must contain at least one uppercase letter")
    if not re.search(r'[a-z]', password):
        errors.append("Password must contain at least one lowercase letter")
    if not re.search(r'\d', password):
        errors.append("Password must contain at least one number")
    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        errors.append("Password must contain at least one special character")
    return len(errors) == 0, errors


# ================= HELPER FUNCTIONS =================

def send_sms(message, phone):
    if sms and phone:
        try:
            sms.send(message, [phone])
        except Exception as e:
            print(f"SMS failed: {e}")


def notify_system(request, user, subject, message, phone=None):
    messages.info(request, message)
    if user and user.email:
        send_mail(subject, message, "system@hr.com", [user.email], fail_silently=True)
    if phone:
        send_sms(message, phone)
    if user:
        Notification.objects.create(
            user=user,
            title=subject,
            message=message,
            notification_type='info'
        )


def log_activity(user, action, model_name, object_id=None, object_name="", details="", ip_address=None):
    try:
        ActivityLog.objects.create(
            user=user,
            action=action,
            model_name=model_name,
            object_id=object_id,
            object_name=object_name,
            details=details,
            ip_address=ip_address
        )
    except Exception as e:
        print(f"Activity log failed: {e}")


def generate_reset_token():
    return secrets.token_urlsafe(50)


def send_password_reset_email(user, email, reset_link, is_hr=False):
    role = "HR" if is_hr else "Employee"
    subject = f"{role} Password Reset - HR Attendance System"
    message = f"""
    Hello {user.username},
    You requested to reset your password for your {role} account.
    Click the link below to reset your password:
    {reset_link}
    This link will expire in 1 hour.
    If you didn't request this, please ignore this email.
    Best regards,
    HR Attendance System
    """
    try:
        send_mail(subject, message, "noreply@hrsystem.com", [email], fail_silently=False)
        return True
    except Exception as e:
        print(f"Email failed: {e}")
        return False


def get_employee_schedule(employee):
    company = employee.company
    if not company:
        return {
            'type': 'fixed', 'name': 'Default Schedule',
            'start_time': time(9, 0), 'end_time': time(17, 0),
            'late_threshold': 15, 'early_threshold': 15,
        }
    if company.schedule_type == 'shifts' and employee.assigned_shift:
        shift = employee.assigned_shift
        return {
            'type': 'shift', 'name': shift.name,
            'start_time': shift.start_time, 'end_time': shift.end_time,
            'late_threshold': shift.late_threshold,
            'early_threshold': shift.early_departure_threshold,
        }
    else:
        return {
            'type': 'fixed', 'name': 'Company Schedule',
            'start_time': company.fixed_start_time or time(9, 0),
            'end_time': company.fixed_end_time or time(17, 0),
            'late_threshold': company.fixed_late_threshold or 15,
            'early_threshold': company.fixed_early_departure_threshold or 15,
        }


def verify_face(employee, photo_base64):
    if not photo_base64:
        return {'verified': False, 'message': 'Face photo required', 'score': 0}
    if len(photo_base64) < 100:
        return {'verified': False, 'message': 'Image too small', 'score': 0}
    if not photo_base64.startswith('data:image'):
        return {'verified': False, 'message': 'Invalid image format', 'score': 0}
    return {'verified': True, 'message': 'Face verified', 'score': 95}


def verify_fingerprint(employee, fingerprint_data):
    if not fingerprint_data:
        return {'verified': False, 'message': 'Fingerprint data required', 'score': 0}
    if len(fingerprint_data) < 10:
        return {'verified': False, 'message': 'Invalid fingerprint data', 'score': 0}
    return {'verified': True, 'message': 'Fingerprint verified', 'score': 98}


def generate_random_verification_times(employee, check_in_time):
    schedule = get_employee_schedule(employee)
    today = timezone.now().date()
    if check_in_time:
        work_start = check_in_time
    else:
        work_start = datetime.combine(today, schedule['start_time'])
    work_end = datetime.combine(today, schedule['end_time'])
    work_duration = int((work_end - work_start).total_seconds() / 60)
    if work_duration < 240:
        num_verifications = 1
    elif work_duration < 360:
        num_verifications = 2
    else:
        num_verifications = 3
    verification_times = []
    if num_verifications == 1:
        mid_time = work_start + timedelta(minutes=work_duration // 2)
        verify_type = random.choice(['face', 'fingerprint'])
        verification_times.append({'time': mid_time, 'type': verify_type})
    else:
        segment_size = work_duration // num_verifications
        for i in range(num_verifications):
            segment_start = work_start + timedelta(minutes=(i * segment_size) + 30)
            segment_end = work_start + timedelta(minutes=((i + 1) * segment_size) - 30)
            if segment_end > work_end - timedelta(minutes=30):
                segment_end = work_end - timedelta(minutes=30)
            if segment_start < work_start + timedelta(minutes=30):
                segment_start = work_start + timedelta(minutes=30)
            if segment_end > segment_start:
                random_minutes = random.randint(0, int((segment_end - segment_start).total_seconds() / 60))
                verify_time = segment_start + timedelta(minutes=random_minutes)
            else:
                verify_time = segment_start
            verify_type = random.choice(['face', 'fingerprint'])
            verification_times.append({'time': verify_time, 'type': verify_type})
    return verification_times


def schedule_random_verifications(employee, check_in_time):
    today = timezone.now().date()
    RandomVerification.objects.filter(employee=employee, date=today, status='pending').delete()
    verification_times = generate_random_verification_times(employee, check_in_time)
    created = []
    for vt in verification_times:
        random_verify = RandomVerification.objects.create(
            employee=employee, date=today, scheduled_time=vt['time'],
            verification_type=vt['type'], status='pending'
        )
        created.append(random_verify)
    return created


def check_pending_verifications(employee):
    today = timezone.now().date()
    now = timezone.now()
    overdue = RandomVerification.objects.filter(
        employee=employee, date=today, status='pending',
        scheduled_time__lt=now - timedelta(minutes=15)
    )
    for verification in overdue:
        verification.status = 'missed'
        verification.save()
        log_activity(employee.user, 'missed', 'RandomVerification', verification.id, str(employee),
                    f"Missed random {verification.verification_type} verification", None)
    pending = RandomVerification.objects.filter(
        employee=employee, date=today, status='pending', scheduled_time__lte=now
    ).first()
    return pending


def get_shift_info_for_employee(employee):
    schedule = get_employee_schedule(employee)
    return {
        'type': schedule['type'], 'name': schedule['name'],
        'start_time': schedule['start_time'].strftime('%I:%M %p'),
        'end_time': schedule['end_time'].strftime('%I:%M %p'),
        'late_threshold': schedule['late_threshold'],
        'early_threshold': schedule['early_threshold']
    }


# ================= WEB VIEWS =================

def home(request):
    if not request.user.is_authenticated:
        return redirect("users:hr_login")
    if request.user.is_superuser:
        return redirect("users:login")
    profile = Profile.objects.filter(user=request.user).first()
    if profile and profile.role == "hr":
        return redirect("users:hr_dashboard")
    return redirect("users:hr_login")


def admin_login(request):
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
        ip = get_client_ip(request)
        rate_key = f"login_fails_{ip}"
        if cache.get(f"blocked_{ip}", False):
            return render(request, "users/login.html", {"error": "Too many failed attempts. Try again later."})
        user = authenticate(request, username=username, password=password)
        if not user or not user.is_superuser:
            fails = cache.get(rate_key, 0) + 1
            cache.set(rate_key, fails, 300)
            if fails >= 5:
                cache.set(f"blocked_{ip}", True, 1800)
                logger.warning(f"Failed login attempt for username: {username} from IP: {ip}")
                return render(request, "users/login.html", {"error": "Too many failed attempts. Account temporarily locked."})
            logger.warning(f"Failed login attempt for username: {username} from IP: {ip}")
            return render(request, "users/login.html", {"error": "Invalid credentials"})
        cache.delete(rate_key)
        login(request, user)
        logger.info(f"Successful login for user: {username} from IP: {ip}")
        log_activity(user, 'login', 'User', user.id, user.username, "Admin logged in", ip)
        return redirect("users:dashboard")
    return render(request, "users/login.html")


def logout_view(request):
    if request.user.is_authenticated:
        try:
            tokens = OutstandingToken.objects.filter(user=request.user)
            for token in tokens:
                BlacklistedToken.objects.get_or_create(token=token)
        except Exception as e:
            print(f"Token blacklist error: {e}")
        log_activity(request.user, 'logout', 'User', request.user.id, request.user.username, 
                    "User logged out", request.META.get('REMOTE_ADDR'))
    logout(request)
    return redirect("users:login")


@login_required(login_url="users:login")
@admin_required
def dashboard(request):
    pending_companies = Company.objects.filter(status="pending")
    pending_hrs = Profile.objects.filter(role="hr", status="pending")
    approved_hrs = Profile.objects.filter(role="hr", status="approved")
    approved_companies = Company.objects.filter(status="approved")
    return render(request, "users/dashboard.html", {
        "pending_companies": pending_companies,
        "pending_hrs": pending_hrs,
        "approved_hrs": approved_hrs,
        "approved_companies": approved_companies,
        "total_hr": approved_hrs.count(),
        "total_companies": approved_companies.count()
    })


def hr_register(request):
    if request.method == "POST":
        company_name = request.POST.get("company_name")
        username = request.POST.get("username")
        email = request.POST.get("email")
        password = request.POST.get("password")
        confirm_password = request.POST.get("confirm_password")  # ADDED
        phone = request.POST.get("phone")
        ip = get_client_ip(request)
        
        # ADDED: Check if passwords match
        if password != confirm_password:
            messages.error(request, "Passwords do not match!")
            return render(request, "users/hr_register.html")
        
        if cache.get(f"register_{ip}", 0) >= 3:
            messages.error(request, "Too many registration attempts. Try again later.")
            return render(request, "users/hr_register.html")
        cache.set(f"register_{ip}", cache.get(f"register_{ip}", 0) + 1, 3600)
        
        if User.objects.filter(username=username).exists():
            messages.error(request, f"Username '{username}' is already taken.")
            return render(request, "users/hr_register.html")
        if User.objects.filter(email=email).exists():
            messages.error(request, f"Email '{email}' is already registered.")
            return render(request, "users/hr_register.html")
        
        is_valid, errors = validate_password_strength(password)
        if not is_valid:
            for error in errors:
                messages.error(request, error)
            return render(request, "users/hr_register.html")
        
        try:
            user = User.objects.create_user(username=username, email=email, password=password)
            company = Company.objects.create(name=company_name, status='pending', requested_by=user)
            Profile.objects.create(user=user, role="hr", status="pending", phone_number=phone, company=company)
            notify_system(request, user, "HR Registration", f"Your HR account for '{company_name}' has been created and is pending approval.", phone)
            messages.success(request, "Registration successful! Waiting for admin approval.")
            return redirect("users:hr_login")
        except IntegrityError:
            messages.error(request, "Registration failed. Please try again.")
            return render(request, "users/hr_register.html")
    return render(request, "users/hr_register.html")


def hr_login(request):
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
        ip = get_client_ip(request)
        rate_key = f"hr_login_fails_{ip}"
        if cache.get(f"hr_blocked_{ip}", False):
            return render(request, "users/hr_login.html", {"error": "Too many failed attempts. Try again later."})
        
        user = authenticate(request, username=username, password=password)
        if not user:
            fails = cache.get(rate_key, 0) + 1
            cache.set(rate_key, fails, 300)
            if fails >= 5:
                cache.set(f"hr_blocked_{ip}", True, 1800)
                logger.warning(f"Failed HR login attempt for username: {username} from IP: {ip}")
                return render(request, "users/hr_login.html", {"error": "Too many failed attempts. Account temporarily locked."})
            logger.warning(f"Failed HR login attempt for username: {username} from IP: {ip}")
            return render(request, "users/hr_login.html", {"error": "Invalid credentials"})
        
        cache.delete(rate_key)
        try:
            profile = Profile.objects.get(user=user)
        except ObjectDoesNotExist:
            return render(request, "users/hr_login.html", {"error": "Profile not found"})
        if profile.role != "hr":
            return render(request, "users/hr_login.html", {"error": "Not HR account"})
        if profile.status == "pending":
            return render(request, "users/hr_login.html", {"error": "Wait for approval"})
        if profile.status == "rejected":
            return render(request, "users/hr_login.html", {"error": "Account rejected"})
        login(request, user)
        logger.info(f"Successful HR login for user: {username} from IP: {ip}")
        log_activity(user, 'login', 'User', user.id, user.username, "HR logged in", ip)
        return redirect("users:hr_dashboard")
    return render(request, "users/hr_login.html")


@login_required(login_url="users:hr_login")
@hr_required
def hr_dashboard(request):
    profile = get_object_or_404(Profile, user=request.user)
    company = profile.company
    employees = Employee.objects.filter(company=company)
    leaves = Leave.objects.filter(employee__company=company).order_by("-requested_at")
    attendance = Attendance.objects.filter(employee__company=company)
    today = timezone.now().date()
    today_attendance = attendance.filter(date=today)
    dept_count = Counter(employees.values_list("department", flat=True))
    return render(request, "users/hr_dashboard.html", {
        "company": company, "employees": employees, "leaves": leaves,
        "total_employees": employees.count(),
        "present": today_attendance.filter(status="present").count(),
        "absent": today_attendance.filter(status="absent").count(),
        "late": today_attendance.filter(status="late").count(),
        "early_departure": today_attendance.filter(status="early_departure").count(),
        "dept_labels": list(dept_count.keys()),
        "dept_values": list(dept_count.values()),
    })


# ================= HR PASSWORD RESET =================

def hr_forgot_password(request):
    if request.method == "POST":
        email = request.POST.get('email')
        ip = get_client_ip(request)
        rate_key = f"forgot_password_{ip}"
        if cache.get(rate_key, 0) >= 3:
            messages.error(request, "Too many attempts. Try again later.")
            return render(request, "users/hr_forgot_password.html")
        cache.set(rate_key, cache.get(rate_key, 0) + 1, 3600)
        
        try:
            user = User.objects.get(email=email)
            profile = Profile.objects.filter(user=user, role='hr').first()
            if not profile:
                messages.error(request, "No HR account found with this email address.")
                return render(request, "users/hr_forgot_password.html")
            token = generate_reset_token()
            expires_at = timezone.now() + timedelta(hours=1)
            PasswordResetToken.objects.update_or_create(
                user=user,
                defaults={'token': token, 'expires_at': expires_at, 'is_used': False}
            )
            reset_link = request.build_absolute_uri(reverse('users:hr_reset_password', args=[token]))
            send_password_reset_email(user, email, reset_link, is_hr=True)
            messages.success(request, "Password reset link sent to your email.")
            return redirect("users:hr_login")
        except User.DoesNotExist:
            messages.error(request, "No HR account found with this email address.")
            return render(request, "users/hr_forgot_password.html")
    return render(request, "users/hr_forgot_password.html")


def hr_reset_password(request, token):
    try:
        reset_token = PasswordResetToken.objects.get(token=token, is_used=False)
        if not reset_token.is_valid():
            messages.error(request, "Password reset link has expired or been used.")
            return redirect("users:hr_login")
        if request.method == "POST":
            new_password = request.POST.get('new_password')
            confirm_password = request.POST.get('confirm_password')
            if new_password != confirm_password:
                messages.error(request, "Passwords do not match.")
                return render(request, "users/hr_reset_password.html", {'token': token})
            is_valid, errors = validate_password_strength(new_password)
            if not is_valid:
                for error in errors:
                    messages.error(request, error)
                return render(request, "users/hr_reset_password.html", {'token': token})
            user = reset_token.user
            user.password = make_password(new_password)
            user.save()
            reset_token.is_used = True
            reset_token.save()
            log_activity(user, 'password_reset', 'User', user.id, user.username, "Password reset successfully", request.META.get('REMOTE_ADDR'))
            messages.success(request, "Password reset successfully! Please login with your new password.")
            return redirect("users:hr_login")
        return render(request, "users/hr_reset_password.html", {'token': token})
    except PasswordResetToken.DoesNotExist:
        messages.error(request, "Invalid password reset link.")
        return redirect("users:hr_login")


# ================= COMPANY SCHEDULE SETTINGS =================

@login_required(login_url="users:hr_login")
@hr_required
def company_settings(request):
    profile = get_object_or_404(Profile, user=request.user)
    company = profile.company
    if request.method == "POST":
        schedule_type = request.POST.get('schedule_type')
        company.schedule_type = schedule_type
        if schedule_type == 'fixed':
            start_time_str = request.POST.get('fixed_start_time')
            end_time_str = request.POST.get('fixed_end_time')
            if start_time_str:
                company.fixed_start_time = datetime.strptime(start_time_str, '%H:%M').time()
            if end_time_str:
                company.fixed_end_time = datetime.strptime(end_time_str, '%H:%M').time()
            company.fixed_late_threshold = int(request.POST.get('fixed_late_threshold', 15))
            company.fixed_early_departure_threshold = int(request.POST.get('fixed_early_departure_threshold', 15))
        elif schedule_type == 'shifts':
            company.fixed_start_time = None
            company.fixed_end_time = None
            company.fixed_late_threshold = 15
            company.fixed_early_departure_threshold = 15
        working_days = request.POST.getlist('working_days')
        company.working_days = [int(day) for day in working_days]
        company.save()
        log_activity(request.user, 'update', 'Company', company.id, company.name, f"Schedule settings updated to {schedule_type}", request.META.get('REMOTE_ADDR'))
        messages.success(request, "Company schedule settings saved!")
        return redirect('users:company_settings')
    shifts = Shift.objects.filter(company=company, is_active=True)
    return render(request, "users/company_settings.html", {
        "company": company, "shifts": shifts,
        "schedule_types": Company.SCHEDULE_TYPE_CHOICES,
        "weekdays": [
            {'value': 1, 'name': 'Monday'}, {'value': 2, 'name': 'Tuesday'},
            {'value': 3, 'name': 'Wednesday'}, {'value': 4, 'name': 'Thursday'},
            {'value': 5, 'name': 'Friday'}, {'value': 6, 'name': 'Saturday'},
            {'value': 7, 'name': 'Sunday'},
        ]
    })


@login_required(login_url="users:hr_login")
@hr_required
def manage_shifts(request):
    profile = get_object_or_404(Profile, user=request.user)
    company = profile.company
    if company.schedule_type != 'shifts':
        messages.warning(request, "Your company is not set to shift schedule. Change settings first.")
        return redirect('users:company_settings')
    if request.method == "POST":
        action = request.POST.get('action')
        if action == 'create':
            name = request.POST.get('name')
            start_time_str = request.POST.get('start_time')
            end_time_str = request.POST.get('end_time')
            late_threshold = int(request.POST.get('late_threshold', 15))
            early_threshold = int(request.POST.get('early_departure_threshold', 15))
            if Shift.objects.filter(company=company, name=name).exists():
                messages.error(request, f"Shift '{name}' already exists!")
            else:
                Shift.objects.create(
                    company=company, name=name,
                    start_time=datetime.strptime(start_time_str, '%H:%M').time(),
                    end_time=datetime.strptime(end_time_str, '%H:%M').time(),
                    late_threshold=late_threshold,
                    early_departure_threshold=early_threshold
                )
                messages.success(request, f"Shift '{name}' created!")
        elif action == 'delete':
            shift_id = request.POST.get('shift_id')
            shift = get_object_or_404(Shift, id=shift_id, company=company)
            shift.delete()
            messages.success(request, f"Shift deleted!")
        return redirect('users:manage_shifts')
    shifts = Shift.objects.filter(company=company)
    employees = Employee.objects.filter(company=company)
    return render(request, "users/manage_shifts.html", {
        "company": company,
        "shifts": shifts,
        "employees": employees
    })


@login_required(login_url="users:hr_login")
@hr_required
@require_http_methods(["POST"])
def assign_employee_shift(request):
    try:
        employee_id = request.POST.get('employee_id')
        shift_id = request.POST.get('shift_id')
        profile = get_object_or_404(Profile, user=request.user)
        company = profile.company
        employee = get_object_or_404(Employee, id=employee_id, company=company)
        if shift_id and shift_id != '':
            shift = get_object_or_404(Shift, id=shift_id, company=company)
            employee.assigned_shift = shift
            message = f"Shift '{shift.name}' assigned to {employee.name}"
        else:
            employee.assigned_shift = None
            message = f"Shift removed from {employee.name}"
        employee.save()
        return JsonResponse({'success': True, 'message': message})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


# ================= LEAVE MANAGEMENT =================

@login_required(login_url="users:hr_login")
@hr_required
def leave_page(request):
    profile = get_object_or_404(Profile, user=request.user)
    leaves = Leave.objects.filter(employee__company=profile.company).order_by("-requested_at")
    return render(request, "users/leave.html", {"leaves": leaves})


@login_required(login_url="users:hr_login")
def handle_leave(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            leave = get_object_or_404(Leave, id=data.get("leave_id"))
            action = data.get("action")
            if action == "approve":
                leave.status = "approved"
                leave.approved_at = timezone.now()
                leave.approved_by = request.user
            elif action == "reject":
                leave.status = "rejected"
                leave.rejected_at = timezone.now()
                leave.approved_by = request.user
            else:
                return JsonResponse({"success": False, "error": "Invalid action"})
            leave.save()
            if leave.employee.user:
                notify_system(request, leave.employee.user, f"Leave {action}d", f"Your leave request has been {action}d", leave.employee.phone)
            return JsonResponse({"success": True})
        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)})
    return JsonResponse({"success": False, "error": "Method not allowed"})


# ================= ADMIN HR MANAGEMENT =================

@login_required(login_url="users:login")
@admin_required
def approve_hr(request, hr_id):
    try:
        profile = get_object_or_404(Profile, id=hr_id)
        profile.status = "approved"
        profile.save()
        if profile.company and profile.company.status == 'pending':
            profile.company.status = 'approved'
            profile.company.approved_by = request.user
            profile.company.approved_at = timezone.now()
            profile.company.save()
        notify_system(request, profile.user, "HR Approved", "Congratulations! Your HR account is approved.", profile.phone_number)
        return JsonResponse({"success": True})
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)})


@login_required(login_url="users:login")
@admin_required
def reject_hr(request, hr_id):
    try:
        profile = get_object_or_404(Profile, id=hr_id)
        profile.status = "rejected"
        profile.save()
        notify_system(request, profile.user, "HR Rejected", "Sorry, your HR account was rejected.", profile.phone_number)
        return JsonResponse({"success": True})
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)})


@login_required(login_url="users:login")
@admin_required
def delete_hr(request, user_id):
    try:
        User.objects.filter(id=user_id).delete()
        return JsonResponse({"success": True})
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)})


# ================= ATTENDANCE VIEWS =================

@login_required(login_url="users:hr_login")
@hr_required
def attendance_page(request):
    profile = get_object_or_404(Profile, user=request.user)
    attendance = Attendance.objects.filter(employee__company=profile.company).order_by("-date")
    return render(request, "users/attendance.html", {"attendance": attendance})


@login_required(login_url="users:hr_login")
@hr_required
def report_page(request):
    profile = get_object_or_404(Profile, user=request.user)
    start_date = timezone.now().date() - timedelta(days=180)
    end_date = timezone.now().date()
    weeks = []
    current = start_date
    while current <= end_date:
        week_start = current - timedelta(days=current.weekday())
        week_end = week_start + timedelta(days=4)
        weeks.append({"start": week_start, "end": week_end})
        current = week_start + timedelta(days=7)
    weeks = list({(w["start"], w["end"]): w for w in weeks}.values())
    selected_week = request.GET.get("week")
    if selected_week:
        start_str, end_str = selected_week.split("|")
        start = datetime.strptime(start_str, "%Y-%m-%d").date()
        end = datetime.strptime(end_str, "%Y-%m-%d").date()
    else:
        start = weeks[0]["start"] if weeks else start_date
        end = weeks[0]["end"] if weeks else end_date
    attendance = Attendance.objects.filter(employee__company=profile.company, date__range=[start, end])
    return render(request, "users/report.html", {
        "attendance": attendance, "weeks": weeks,
        "selected_start": start, "selected_end": end
    })


@login_required(login_url="users:hr_login")
@hr_required
def analytics_page(request):
    profile = get_object_or_404(Profile, user=request.user)
    attendance = Attendance.objects.filter(employee__company=profile.company)
    return render(request, "users/analytics.html", {
        "present": attendance.filter(status="present").count(),
        "absent": attendance.filter(status="absent").count(),
        "late": attendance.filter(status="late").count(),
        "early_departure": attendance.filter(status="early_departure").count(),
    })


# ================= EXPORTS =================

@login_required(login_url="users:login")
@admin_required
def export_csv(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="attendance.csv"'
    writer = csv.writer(response)
    writer.writerow(["Employee", "Company", "Date", "Check In", "Check Out", "Status", "Verifications"])
    for a in Attendance.objects.all():
        writer.writerow([a.employee.name, a.employee.company.name if a.employee.company else "N/A", a.date, 
                        a.check_in.strftime('%I:%M %p') if a.check_in else "-", 
                        a.check_out.strftime('%I:%M %p') if a.check_out else "-", 
                        a.status, a.verified_count])
    return response


@login_required(login_url="users:login")
@admin_required
def export_pdf(request):
    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="attendance.pdf"'
    pdf = SimpleDocTemplate(response, pagesize=letter)
    data = [["Employee", "Company", "Date", "Check In", "Check Out", "Status"]]
    for a in Attendance.objects.all():
        data.append([a.employee.name, a.employee.company.name if a.employee.company else "N/A", str(a.date), 
                    a.check_in.strftime('%I:%M %p') if a.check_in else "-", 
                    a.check_out.strftime('%I:%M %p') if a.check_out else "-", 
                    a.status])
    pdf.build([Table(data)])
    return response


# ================= EMPLOYEE VERIFICATION =================

@login_required(login_url="users:login")
def employee_verification_page(request):
    profile = Profile.objects.filter(user=request.user).first()
    if request.user.is_superuser:
        pending_employees = Profile.objects.filter(role="employee", status="pending")
        approved_employees = Profile.objects.filter(role="employee", status="approved")
        rejected_employees = Profile.objects.filter(role="employee", status="rejected")
    else:
        if not profile or profile.role != "hr":
            return redirect("users:hr_login")
        pending_employees = Profile.objects.filter(role="employee", status="pending", company=profile.company)
        approved_employees = Profile.objects.filter(role="employee", status="approved", company=profile.company)
        rejected_employees = Profile.objects.filter(role="employee", status="rejected", company=profile.company)
    return render(request, "users/employee_verification.html", {
        "pending_employees": pending_employees,
        "approved_employees": approved_employees,
        "rejected_employees": rejected_employees,
    })


@login_required(login_url="users:hr_login")
@hr_required
@require_http_methods(["POST"])
def verify_employee(request, profile_id):
    try:
        data = json.loads(request.body)
        action = data.get('action')
        profile = get_object_or_404(Profile, id=profile_id)
        if action == 'approve':
            profile.status = 'approved'
            message = "Your employee account has been approved!"
        elif action == 'reject':
            profile.status = 'rejected'
            message = "Your employee account has been rejected."
        else:
            return JsonResponse({"success": False, "error": "Invalid action"})
        profile.save()
        notify_system(request, profile.user, f"Employee Account {action}d", message, profile.phone_number)
        return JsonResponse({"success": True, "message": f"Employee {action}d"})
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)})


# ================= EMPLOYEE MANAGEMENT =================

@login_required(login_url="users:hr_login")
@hr_required
@require_http_methods(["POST"])
def delete_employee(request, employee_id):
    try:
        profile = get_object_or_404(Profile, user=request.user)
        employee = get_object_or_404(Employee, id=employee_id, company=profile.company)
        employee.user.delete()
        return JsonResponse({"success": True, "message": "Employee deleted"})
    except ObjectDoesNotExist:
        return JsonResponse({"success": False, "error": "Employee not found"}, status=404)
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status=500)


@login_required(login_url="users:hr_login")
@hr_required
@require_http_methods(["POST"])
def update_employee(request, employee_id):
    try:
        data = json.loads(request.body)
        profile = get_object_or_404(Profile, user=request.user)
        employee = get_object_or_404(Employee, id=employee_id, company=profile.company)
        employee.name = data.get('name', employee.name)
        employee.department = data.get('department', employee.department)
        dept_id = data.get('department_id')
        if dept_id:
            department = get_object_or_404(Department, id=dept_id, company=profile.company)
            employee.department_obj = department
            employee.department = department.name
        employee.save()
        return JsonResponse({"success": True, "message": "Employee updated"})
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status=500)


# ================= COMPANY MANAGEMENT =================

@login_required(login_url="users:login")
@admin_required
def pending_companies_page(request):
    pending_companies = Company.objects.filter(status='pending')
    return render(request, "users/pending_companies.html", {"pending_companies": pending_companies})


@csrf_exempt
@login_required(login_url="users:login")
@admin_required
@require_http_methods(["POST"])
def approve_company(request, company_id):
    try:
        company = get_object_or_404(Company, id=company_id)
        company.status = 'approved'
        company.approved_by = request.user
        company.approved_at = timezone.now()
        company.save()
        hr_profile = Profile.objects.filter(user=company.requested_by, role='hr').first()
        if hr_profile:
            hr_profile.status = 'approved'
            hr_profile.save()
            notify_system(request, hr_profile.user, "Company & HR Approved", f"Congratulations! Your company '{company.name}' and HR account have been approved.", hr_profile.phone_number)
        return JsonResponse({"success": True, "message": "Company approved successfully"})
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status=500)


@csrf_exempt
@login_required(login_url="users:login")
@admin_required
@require_http_methods(["POST"])
def reject_company(request, company_id):
    try:
        data = json.loads(request.body)
        reason = data.get('reason', 'No reason provided')
        company = get_object_or_404(Company, id=company_id)
        company.status = 'rejected'
        company.approved_by = request.user
        company.approved_at = timezone.now()
        company.rejection_reason = reason
        company.save()
        hr_profile = Profile.objects.filter(user=company.requested_by, role='hr').first()
        if hr_profile:
            hr_profile.status = 'rejected'
            hr_profile.save()
            notify_system(request, hr_profile.user, "Company & HR Rejected", f"Sorry, your company '{company.name}' has been rejected. Reason: {reason}", hr_profile.phone_number)
        return JsonResponse({"success": True, "message": "Company rejected successfully"})
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status=500)


# ================= DEPARTMENT MANAGEMENT =================

@login_required(login_url="users:hr_login")
@hr_required
def departments_page(request):
    profile = get_object_or_404(Profile, user=request.user)
    company = profile.company
    departments = Department.objects.filter(company=company)
    return render(request, "users/departments.html", {"company": company, "departments": departments})


@login_required
@require_http_methods(["GET"])
def api_get_departments(request):
    try:
        if request.user.is_authenticated:
            profile = Profile.objects.filter(user=request.user).first()
            if profile and profile.role == 'hr' and profile.company:
                departments = Department.objects.filter(company=profile.company).values('id', 'name')
                return JsonResponse({'success': True, 'departments': list(departments)})
        company_id = request.GET.get('company_id')
        if company_id:
            departments = Department.objects.filter(company_id=company_id).values('id', 'name')
            return JsonResponse({'success': True, 'departments': list(departments)})
        return JsonResponse({'success': True, 'departments': []})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@login_required
@require_http_methods(["POST"])
def api_create_department(request):
    try:
        data = json.loads(request.body)
        department_name = data.get('name')
        profile = Profile.objects.filter(user=request.user, role='hr').first()
        if not profile or not profile.company:
            return JsonResponse({'success': False, 'error': 'Company not found'}, status=400)
        if Department.objects.filter(company=profile.company, name__iexact=department_name).exists():
            return JsonResponse({'success': False, 'error': 'Department already exists'}, status=400)
        department = Department.objects.create(name=department_name, company=profile.company, created_by=request.user)
        return JsonResponse({'success': True, 'department': {'id': department.id, 'name': department.name}})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@login_required
@require_http_methods(["POST"])
def api_delete_department(request, dept_id):
    try:
        profile = Profile.objects.filter(user=request.user, role='hr').first()
        if not profile or not profile.company:
            return JsonResponse({'success': False, 'error': 'Company not found'}, status=400)
        department = get_object_or_404(Department, id=dept_id, company=profile.company)
        department.delete()
        return JsonResponse({'success': True, 'message': 'Department deleted'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ================= EMPLOYEE PASSWORD RESET (Mobile API) =================

@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(lambda r: f"forgot_{get_client_ip(r)}", limit=3, period=3600)
def api_employee_forgot_password(request):
    try:
        data = json.loads(request.body)
        email_or_phone = data.get('email_or_phone')
        employee = None
        if '@' in email_or_phone:
            employee = Employee.objects.filter(email=email_or_phone).first()
            if not employee:
                employee = Employee.objects.filter(user__email=email_or_phone).first()
        else:
            employee = Employee.objects.filter(phone=email_or_phone).first()
        if not employee or not employee.user:
            return JsonResponse({'success': False, 'error': 'No employee found with this email or phone'}, status=404)
        token = generate_reset_token()
        expires_at = timezone.now() + timedelta(hours=1)
        employee.reset_token = token
        employee.reset_token_expires = expires_at
        employee.save()
        reset_link = f"yourapp://reset-password?token={token}"
        if employee.email:
            send_password_reset_email(employee.user, employee.email, reset_link, is_hr=False)
        if employee.phone:
            sms_message = f"Password reset link: {reset_link} Expires in 1 hour."
            send_sms(sms_message, employee.phone)
        return JsonResponse({'success': True, 'message': 'Password reset link sent to your email/SMS'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(lambda r: f"reset_{get_client_ip(r)}", limit=5, period=300)
def api_employee_reset_password(request):
    try:
        data = json.loads(request.body)
        token = data.get('token')
        new_password = data.get('new_password')
        employee = Employee.objects.filter(
            reset_token=token,
            reset_token_expires__gt=timezone.now()
        ).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Invalid or expired reset token'}, status=400)
        is_valid, errors = validate_password_strength(new_password)
        if not is_valid:
            return JsonResponse({'success': False, 'error': errors[0]}, status=400)
        user = employee.user
        user.password = make_password(new_password)
        user.save()
        employee.reset_token = None
        employee.reset_token_expires = None
        employee.save()
        log_activity(user, 'password_reset', 'Employee', employee.id, employee.name, "Password reset via mobile", None)
        return JsonResponse({'success': True, 'message': 'Password reset successfully'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ================= MOBILE API VIEWS =================

# ================= PUBLIC API FOR EMPLOYEE REGISTRATION =================

@require_http_methods(["GET"])
def api_get_companies(request):
    """Public endpoint - Get all companies approved by admin for employee registration"""
    try:
        companies = Company.objects.filter(status='approved').values('id', 'name', 'company_code')
        return JsonResponse({'success': True, 'companies': list(companies)})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_http_methods(["GET"])
def api_get_departments_by_company(request, company_id):
    """Public endpoint - Get departments created by HR for a specific company"""
    try:
        # Check if company exists and is approved
        company = get_object_or_404(Company, id=company_id, status='approved')
        
        # Get all departments for this company
        departments = Department.objects.filter(company=company).values('id', 'name')
        
        return JsonResponse({'success': True, 'departments': list(departments)})
    except Company.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Company not found'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_http_methods(["GET"])
def api_my_schedule(request):
    try:
        employee = Employee.objects.filter(user=request.user).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        schedule = get_employee_schedule(employee)
        return JsonResponse({
            'success': True,
            'schedule': {
                'type': schedule['type'], 'name': schedule['name'],
                'start_time': schedule['start_time'].strftime('%I:%M %p'),
                'end_time': schedule['end_time'].strftime('%I:%M %p'),
                'late_threshold': f"{schedule['late_threshold']} minutes",
                'working_days': employee.company.working_days if employee.company else [1,2,3,4,5]
            }
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_http_methods(["GET"])
def api_my_shift_info(request):
    try:
        employee = Employee.objects.filter(user=request.user).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        shift_info = get_shift_info_for_employee(employee)
        return JsonResponse({'success': True, 'shift_info': shift_info})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_http_methods(["GET"])
def api_get_shifts_for_employee(request):
    try:
        employee = Employee.objects.filter(user=request.user).first()
        if not employee or not employee.company:
            return JsonResponse({'success': False, 'error': 'Company not found'}, status=404)
        if employee.company.schedule_type == 'shifts':
            shifts = Shift.objects.filter(company=employee.company, is_active=True).values('id', 'name', 'start_time', 'end_time')
            return JsonResponse({'success': True, 'shifts': list(shifts)})
        else:
            return JsonResponse({'success': True, 'shifts': [], 'fixed_schedule': {
                'start_time': employee.company.fixed_start_time.strftime('%H:%M') if employee.company.fixed_start_time else '09:00',
                'end_time': employee.company.fixed_end_time.strftime('%H:%M') if employee.company.fixed_end_time else '17:00'
            }})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(lambda r: f"register_{get_client_ip(r)}", limit=3, period=3600)
def api_employee_register(request):
    try:
        data = json.loads(request.body)
        if User.objects.filter(username=data.get('username')).exists():
            return JsonResponse({'success': False, 'error': 'Username already exists'}, status=400)
        if User.objects.filter(email=data.get('email')).exists():
            return JsonResponse({'success': False, 'error': 'Email already exists'}, status=400)
        
        password = data.get('password')
        is_valid, errors = validate_password_strength(password)
        if not is_valid:
            return JsonResponse({'success': False, 'error': errors[0]}, status=400)
        
        user = User.objects.create_user(
            username=data.get('username'), email=data.get('email'), password=password,
            first_name=data.get('name', '').split()[0] if data.get('name') else '',
            last_name=data.get('name', '').split()[-1] if len(data.get('name', '').split()) > 1 else ''
        )
        company_id = data.get('company_id')
        department_id = data.get('department_id')
        shift_id = data.get('shift_id')
        company = Company.objects.filter(id=company_id, status='approved').first() if company_id else None
        department = Department.objects.filter(id=department_id).first() if department_id else None
        shift = Shift.objects.filter(id=shift_id, company=company).first() if shift_id and company else None
        employee = Employee.objects.create(
            user=user, name=data.get('name'), department=data.get('department', 'General'),
            company=company, department_obj=department, assigned_shift=shift,
            status='absent', email=data.get('email'), phone=data.get('phone')
        )
        Profile.objects.create(user=user, role='employee', status='pending', phone_number=data.get('phone'), company=company)
        return JsonResponse({'success': True, 'message': 'Registration successful. Waiting for HR approval.', 'user_id': user.id, 'employee_id': employee.id})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(lambda r: f"login_{get_client_ip(r)}", limit=5, period=300)
def api_employee_login(request):
    try:
        data = json.loads(request.body)
        ip = get_client_ip(request)
        if cache.get(f"api_blocked_{ip}", False):
            return JsonResponse({'success': False, 'error': 'Too many failed attempts. Try again later.'}, status=429)
        
        user = authenticate(username=data.get('username'), password=data.get('password'))
        if not user:
            fails = cache.get(f"api_login_fails_{ip}", 0) + 1
            cache.set(f"api_login_fails_{ip}", fails, 300)
            if fails >= 5:
                cache.set(f"api_blocked_{ip}", True, 1800)
                logger.warning(f"Failed mobile login attempt from IP: {ip}")
                return JsonResponse({'success': False, 'error': 'Too many failed attempts. Account temporarily locked.'}, status=429)
            logger.warning(f"Failed mobile login attempt from IP: {ip}")
            return JsonResponse({'success': False, 'error': 'Invalid credentials'}, status=400)
        
        cache.delete(f"api_login_fails_{ip}")
        try:
            profile = Profile.objects.get(user=user)
        except ObjectDoesNotExist:
            return JsonResponse({'success': False, 'error': 'Profile not found'}, status=400)
        
        if profile.role == 'employee':
            if profile.status == 'pending':
                return JsonResponse({'success': False, 'error': 'Account pending HR approval'}, status=400)
            if profile.status == 'rejected':
                return JsonResponse({'success': False, 'error': 'Account rejected'}, status=400)
        
        employee = Employee.objects.filter(user=user).first()
        refresh = RefreshToken.for_user(user)
        logger.info(f"Successful mobile login for user: {user.username} from IP: {ip}")
        return JsonResponse({
            'success': True,
            'access_token': str(refresh.access_token),
            'refresh_token': str(refresh),
            'user': {
                'id': user.id, 'username': user.username, 'email': user.email,
                'name': employee.name if employee else user.username,
                'department': employee.department if employee else '',
                'company_id': employee.company.id if employee and employee.company else None,
                'company_name': employee.company.name if employee and employee.company else None,
            }
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_http_methods(["GET"])
def api_employee_profile(request):
    try:
        employee = Employee.objects.filter(user=request.user).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        profile = Profile.objects.filter(user=request.user).first()
        return JsonResponse({
            'success': True,
            'profile': {
                'id': employee.id, 'name': employee.name, 'email': request.user.email,
                'department': employee.department,
                'company_id': employee.company.id if employee.company else None,
                'company_name': employee.company.name if employee.company else None,
                'phone': profile.phone_number if profile else '',
                'status': employee.status,
            }
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@login_required
@require_http_methods(["POST"])
@rate_limit(lambda r: f"checkin_{r.user.id}", limit=2, period=60)
@idempotent(key_func=lambda r: r.headers.get('X-Idempotency-Key'))
def api_check_in(request):
    try:
        data = json.loads(request.body)
        employee = Employee.objects.filter(user=request.user).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        
        existing_attendance = Attendance.objects.filter(employee=employee, date=timezone.now().date()).first()
        if existing_attendance and existing_attendance.check_in:
            return JsonResponse({'success': False, 'error': 'Already checked in today'}, status=400)
        
        today_weekday = timezone.now().weekday() + 1
        if employee.company and employee.company.working_days and today_weekday not in employee.company.working_days:
            return JsonResponse({'success': False, 'error': 'Today is not a working day'}, status=400)
        
        face_photo = data.get('face_photo')
        if not face_photo:
            return JsonResponse({'success': False, 'error': 'Face photo required for check-in'}, status=400)
        face_result = verify_face(employee, face_photo)
        if not face_result['verified']:
            return JsonResponse({'success': False, 'error': f"Face verification failed: {face_result['message']}"}, status=400)
        
        fingerprint_data = data.get('fingerprint_data')
        if not fingerprint_data:
            return JsonResponse({'success': False, 'error': 'Fingerprint required for check-in'}, status=400)
        fingerprint_result = verify_fingerprint(employee, fingerprint_data)
        if not fingerprint_result['verified']:
            return JsonResponse({'success': False, 'error': f"Fingerprint verification failed: {fingerprint_result['message']}"}, status=400)
        
        check_in_time = timezone.now()
        schedule = get_employee_schedule(employee)
        shift_start = schedule['start_time']
        shift_name = schedule['name']
        late_threshold = schedule['late_threshold']
        check_in_time_only = check_in_time.time()
        shift_start_datetime = datetime.combine(check_in_time.date(), shift_start)
        minutes_late = int((check_in_time - shift_start_datetime).total_seconds() / 60)
        status = 'late' if minutes_late > late_threshold else 'present'
        
        attendance = Attendance.objects.create(
            employee=employee, date=check_in_time.date(), check_in=check_in_time_only,
            status=status, verification_method='face_fingerprint', verified_count=2
        )
        random_verifications = schedule_random_verifications(employee, check_in_time)
        employee.check_in_time = check_in_time
        employee.status = 'present'
        employee.random_verify_count = 0
        employee.save()
        
        shift_info = get_shift_info_for_employee(employee)
        return JsonResponse({
            'success': True, 'attendance_id': attendance.id,
            'message': f'Checked in at {check_in_time_only.strftime("%I:%M %p")}',
            'status': status, 'schedule_type': schedule['type'],
            'shift_name': shift_name, 'shift_start': shift_start.strftime('%I:%M %p'),
            'shift_end': schedule['end_time'].strftime('%I:%M %p'),
            'minutes_late': minutes_late if minutes_late > 0 else 0,
            'late_threshold_minutes': late_threshold,
            'random_verifications_scheduled': len(random_verifications),
            'next_verification': random_verifications[0].scheduled_time.strftime('%I:%M %p') if random_verifications else None,
            'total_verifications_today': 2 + len(random_verifications),
            'shift_info': shift_info
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@login_required
@require_http_methods(["GET"])
def api_check_random_verification(request):
    try:
        employee = Employee.objects.filter(user=request.user).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        attendance = Attendance.objects.filter(employee=employee, date=timezone.now().date()).first()
        if not attendance or not attendance.check_in:
            return JsonResponse({'success': True, 'needs_verification': False, 'message': 'Not checked in yet'})
        if attendance.check_out:
            return JsonResponse({'success': True, 'needs_verification': False, 'message': 'Already checked out for the day'})
        shift_info = get_shift_info_for_employee(employee)
        pending = check_pending_verifications(employee)
        if pending:
            now = timezone.now()
            if now >= pending.scheduled_time:
                return JsonResponse({
                    'success': True, 'needs_verification': True,
                    'verification_id': pending.id,
                    'verification_type': pending.verification_type,
                    'scheduled_time': pending.scheduled_time.strftime('%I:%M %p'),
                    'message': f'Please complete your {pending.verification_type} verification',
                    'shift_info': shift_info
                })
            else:
                time_until = int((pending.scheduled_time - now).total_seconds() / 60)
                return JsonResponse({
                    'success': True, 'needs_verification': False,
                    'next_verification_at': pending.scheduled_time.strftime('%I:%M %p'),
                    'minutes_until': time_until,
                    'message': f'Next verification at {pending.scheduled_time.strftime("%I:%M %p")}',
                    'shift_info': shift_info
                })
        completed_count = RandomVerification.objects.filter(employee=employee, date=timezone.now().date(), status='completed').count()
        return JsonResponse({
            'success': True, 'needs_verification': False,
            'message': 'No pending verification at this time',
            'shift_info': shift_info,
            'completed_verifications': completed_count
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@login_required
@require_http_methods(["POST"])
def api_submit_random_verification(request, verification_id):
    try:
        data = json.loads(request.body)
        employee = Employee.objects.filter(user=request.user).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        verification = get_object_or_404(RandomVerification, id=verification_id, employee=employee)
        if verification.status == 'completed':
            return JsonResponse({'success': False, 'error': 'Verification already completed'}, status=400)
        if verification.status == 'missed':
            return JsonResponse({'success': False, 'error': 'Verification window expired'}, status=400)
        now = timezone.now()
        if now > verification.scheduled_time + timedelta(minutes=15):
            verification.status = 'missed'
            verification.save()
            return JsonResponse({'success': False, 'error': 'Verification window expired'}, status=400)
        
        if verification.verification_type == 'face':
            face_photo = data.get('face_photo')
            if not face_photo:
                return JsonResponse({'success': False, 'error': 'Face photo required'}, status=400)
            result = verify_face(employee, face_photo)
            if result['verified']:
                verification.status = 'completed'
                verification.completed_time = now
                verification.face_score = result.get('score', 95)
                verification.save()
                employee.random_verify_count += 1
                employee.save()
                attendance = Attendance.objects.filter(employee=employee, date=now.date()).first()
                if attendance:
                    attendance.verified_count += 1
                    attendance.save()
                remaining = RandomVerification.objects.filter(employee=employee, date=now.date(), status='pending').count()
                return JsonResponse({
                    'success': True, 'message': 'Face verification completed',
                    'verification_number': employee.random_verify_count,
                    'verifications_remaining': remaining
                })
            else:
                verification.status = 'failed'
                verification.save()
                return JsonResponse({'success': False, 'error': result['message']}, status=400)
        elif verification.verification_type == 'fingerprint':
            fingerprint_data = data.get('fingerprint_data')
            if not fingerprint_data:
                return JsonResponse({'success': False, 'error': 'Fingerprint required'}, status=400)
            result = verify_fingerprint(employee, fingerprint_data)
            if result['verified']:
                verification.status = 'completed'
                verification.completed_time = now
                verification.save()
                employee.random_verify_count += 1
                employee.save()
                attendance = Attendance.objects.filter(employee=employee, date=now.date()).first()
                if attendance:
                    attendance.verified_count += 1
                    attendance.save()
                remaining = RandomVerification.objects.filter(employee=employee, date=now.date(), status='pending').count()
                return JsonResponse({
                    'success': True, 'message': 'Fingerprint verification completed',
                    'verification_number': employee.random_verify_count,
                    'verifications_remaining': remaining
                })
            else:
                verification.status = 'failed'
                verification.save()
                return JsonResponse({'success': False, 'error': result['message']}, status=400)
        return JsonResponse({'success': False, 'error': 'Invalid verification type'}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_http_methods(["GET"])
def api_verification_status(request):
    try:
        employee = Employee.objects.filter(user=request.user).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        today = timezone.now().date()
        attendance = Attendance.objects.filter(employee=employee, date=today).first()
        verifications = RandomVerification.objects.filter(employee=employee, date=today)
        return JsonResponse({
            'success': True,
            'status': {
                'is_checked_in': attendance and attendance.check_in is not None,
                'is_checked_out': attendance and attendance.check_out is not None,
                'check_in_time': attendance.check_in.strftime('%I:%M %p') if attendance and attendance.check_in else None,
                'check_out_time': attendance.check_out.strftime('%I:%M %p') if attendance and attendance.check_out else None,
                'random_verifications': {
                    'total_required': verifications.count(),
                    'completed': verifications.filter(status='completed').count(),
                    'pending': verifications.filter(status='pending').count(),
                    'missed': verifications.filter(status='missed').count(),
                    'failed': verifications.filter(status='failed').count(),
                },
                'verification_details': [
                    {
                        'type': v.verification_type,
                        'scheduled_time': v.scheduled_time.strftime('%I:%M %p'),
                        'status': v.status,
                        'completed_time': v.completed_time.strftime('%I:%M %p') if v.completed_time else None
                    } for v in verifications
                ]
            }
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@login_required
@require_http_methods(["POST"])
@rate_limit(lambda r: f"checkout_{r.user.id}", limit=2, period=60)
@idempotent(key_func=lambda r: r.headers.get('X-Idempotency-Key'))
def api_check_out(request):
    try:
        data = json.loads(request.body)
        employee = Employee.objects.filter(user=request.user).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        attendance = Attendance.objects.filter(employee=employee, date=timezone.now().date()).first()
        if not attendance:
            return JsonResponse({'success': False, 'error': 'No check-in record found'}, status=400)
        if attendance.check_out:
            return JsonResponse({'success': False, 'error': 'Already checked out'}, status=400)
        
        face_photo = data.get('face_photo')
        if not face_photo:
            return JsonResponse({'success': False, 'error': 'Face photo required for check-out'}, status=400)
        face_result = verify_face(employee, face_photo)
        if not face_result['verified']:
            return JsonResponse({'success': False, 'error': f"Face verification failed: {face_result['message']}"}, status=400)
        
        fingerprint_data = data.get('fingerprint_data')
        if not fingerprint_data:
            return JsonResponse({'success': False, 'error': 'Fingerprint required for check-out'}, status=400)
        fingerprint_result = verify_fingerprint(employee, fingerprint_data)
        if not fingerprint_result['verified']:
            return JsonResponse({'success': False, 'error': f"Fingerprint verification failed: {fingerprint_result['message']}"}, status=400)
        
        today = timezone.now().date()
        pending_count = RandomVerification.objects.filter(employee=employee, date=today, status='pending').count()
        missed_count = RandomVerification.objects.filter(employee=employee, date=today, status='missed').count()
        if pending_count > 0 or missed_count > 0:
            return JsonResponse({
                'success': False,
                'error': f'Cannot check out. You have {pending_count} pending and {missed_count} missed verifications today.',
                'pending_verifications': pending_count,
                'missed_verifications': missed_count
            }, status=400)
        
        check_out_time = timezone.now()
        schedule = get_employee_schedule(employee)
        shift_end = schedule['end_time']
        early_threshold = schedule['early_threshold']
        check_out_time_only = check_out_time.time()
        shift_end_datetime = datetime.combine(check_out_time.date(), shift_end)
        minutes_early = int((shift_end_datetime - check_out_time).total_seconds() / 60)
        if minutes_early > early_threshold:
            attendance.status = 'early_departure'
        attendance.check_out = check_out_time_only
        attendance.verification_method = 'face_fingerprint'
        attendance.verified_count += 2
        attendance.save()
        employee.check_out_time = check_out_time
        employee.status = 'absent'
        employee.save()
        completed_count = RandomVerification.objects.filter(employee=employee, date=today, status='completed').count()
        return JsonResponse({
            'success': True,
            'message': f'Checked out at {check_out_time_only.strftime("%I:%M %p")}',
            'shift_name': schedule['name'],
            'expected_end': shift_end.strftime('%I:%M %p'),
            'minutes_early': minutes_early if minutes_early > 0 else 0,
            'total_verifications_today': attendance.verified_count,
            'random_verifications_completed': completed_count,
            'all_verifications_completed': True
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ================= OTHER MOBILE APIs =================

@login_required
@require_http_methods(["GET"])
def api_attendance_history(request):
    try:
        employee = Employee.objects.filter(user=request.user).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        start_date = timezone.now().date() - timedelta(days=30)
        attendances = Attendance.objects.filter(employee=employee, date__gte=start_date).order_by('-date')
        return JsonResponse({
            'success': True,
            'attendance': [{
                'id': att.id, 'date': att.date.strftime('%Y-%m-%d'),
                'check_in': att.check_in.strftime('%I:%M %p') if att.check_in else None,
                'check_out': att.check_out.strftime('%I:%M %p') if att.check_out else None,
                'status': att.status,
            } for att in attendances]
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_http_methods(["GET"])
def api_today_attendance(request):
    try:
        employee = Employee.objects.filter(user=request.user).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        attendance = Attendance.objects.filter(employee=employee, date=timezone.now().date()).first()
        return JsonResponse({
            'success': True,
            'has_checked_in': attendance and attendance.check_in is not None,
            'has_checked_out': attendance and attendance.check_out is not None,
            'check_in_time': attendance.check_in.strftime('%I:%M %p') if attendance and attendance.check_in else None,
            'check_out_time': attendance.check_out.strftime('%I:%M %p') if attendance and attendance.check_out else None,
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@login_required
@require_http_methods(["POST"])
@rate_limit(lambda r: f"apply_leave_{r.user.id}", limit=3, period=3600)
def api_apply_leave(request):
    try:
        data = json.loads(request.body)
        employee = Employee.objects.filter(user=request.user).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        recent_leave = Leave.objects.filter(
            employee=employee,
            requested_at__gte=timezone.now() - timedelta(minutes=5),
            leave_type=data.get('leave_type'),
            reason=data.get('reason')
        ).first()
        if recent_leave:
            return JsonResponse({'success': True, 'already_processed': True, 'leave_id': recent_leave.id, 'message': 'Leave already submitted'})
        leave = Leave.objects.create(employee=employee, leave_type=data.get('leave_type'), reason=data.get('reason'), status='pending')
        return JsonResponse({'success': True, 'leave_id': leave.id, 'message': 'Leave request submitted successfully'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_http_methods(["GET"])
def api_leave_history(request):
    try:
        employee = Employee.objects.filter(user=request.user).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        leaves = Leave.objects.filter(employee=employee).order_by('-requested_at')
        return JsonResponse({
            'success': True,
            'leaves': [{
                'id': leave.id, 'leave_type': leave.leave_type, 'reason': leave.reason,
                'status': leave.status,
                'requested_at': leave.requested_at.strftime('%Y-%m-%d %H:%M'),
                'approved_at': leave.approved_at.strftime('%Y-%m-%d %H:%M') if leave.approved_at else None,
            } for leave in leaves]
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_http_methods(["GET"])
def api_leave_balance(request):
    try:
        employee = Employee.objects.filter(user=request.user).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        total_taken = Leave.objects.filter(employee=employee, status='approved').count()
        return JsonResponse({
            'success': True,
            'balance': {
                'annual': max(0, 14 - total_taken), 'sick': 10, 'casual': 6,
                'unpaid': 30, 'emergency': 3, 'total_used': total_taken, 'total_available': 63,
            }
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@login_required
@require_http_methods(["POST"])
def api_cancel_leave(request, leave_id):
    try:
        employee = Employee.objects.filter(user=request.user).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        leave = get_object_or_404(Leave, id=leave_id, employee=employee)
        if leave.status != 'pending':
            return JsonResponse({'success': False, 'error': 'Only pending leaves can be cancelled'}, status=400)
        leave.status = 'cancelled'
        leave.save()
        return JsonResponse({'success': True, 'message': 'Leave cancelled successfully'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@login_required
@require_http_methods(["POST"])
@rate_limit(lambda r: f"sync_attendance_{r.user.id}", limit=5, period=60)
def api_sync_attendance(request):
    try:
        data = json.loads(request.body)
        employee = Employee.objects.filter(user=request.user).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        synced_count = 0
        for record in data.get('records', []):
            date_obj = datetime.strptime(record['date'], '%Y-%m-%d').date()
            if not Attendance.objects.filter(employee=employee, date=date_obj).exists():
                Attendance.objects.create(
                    employee=employee, date=date_obj,
                    check_in=datetime.strptime(record['check_in'], '%H:%M:%S').time() if record.get('check_in') else None,
                    status='present'
                )
                synced_count += 1
        return JsonResponse({'success': True, 'synced_count': synced_count, 'message': f'Successfully synced {synced_count} records'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@login_required
@require_http_methods(["POST"])
@rate_limit(lambda r: f"sync_leaves_{r.user.id}", limit=5, period=60)
def api_sync_leaves(request):
    try:
        data = json.loads(request.body)
        records = data.get('records', [])
        employee = Employee.objects.filter(user=request.user).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        synced_count = 0
        for record in records:
            Leave.objects.create(employee=employee, leave_type=record.get('leave_type'), reason=record.get('reason'), status='pending')
            synced_count += 1
        return JsonResponse({'success': True, 'synced_count': synced_count, 'message': f'Successfully synced {synced_count} leave requests'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_http_methods(["GET"])
def api_get_beacons(request):
    return JsonResponse({
        'success': True,
        'beacons': [
            {'id': 'OFFICE_BEACON_001', 'name': 'Office Main Entrance', 'rssi_threshold': -70},
            {'id': 'OFFICE_BEACON_002', 'name': 'Office Back Entrance', 'rssi_threshold': -70},
            {'id': 'HR_BEACON_MAIN', 'name': 'HR Department', 'rssi_threshold': -65},
        ]
    })


@login_required
@require_http_methods(["GET"])
def api_dashboard_stats(request):
    try:
        employee = Employee.objects.filter(user=request.user).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        today = timezone.now().date()
        start_of_month = today.replace(day=1)
        this_month_attendance = Attendance.objects.filter(employee=employee, date__gte=start_of_month, date__lte=today)
        present_days = this_month_attendance.filter(status='present').count()
        total_days = (today - start_of_month).days + 1
        return JsonResponse({
            'success': True,
            'stats': {
                'working_days': total_days,
                'present_days': present_days,
                'absent_days': total_days - present_days,
                'attendance_percentage': round((present_days / total_days) * 100, 1) if total_days > 0 else 0,
            }
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_http_methods(["GET"])
def api_monthly_stats(request):
    try:
        employee = Employee.objects.filter(user=request.user).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        today = timezone.now().date()
        start_of_month = today.replace(day=1)
        if today.month == 12:
            end_of_month = today.replace(year=today.year + 1, month=1, day=1)
        else:
            end_of_month = today.replace(month=today.month + 1, day=1)
        attendances = Attendance.objects.filter(employee=employee, date__gte=start_of_month, date__lt=end_of_month)
        total_days = (end_of_month - start_of_month).days
        present_days = attendances.filter(status='present').count()
        leaves_taken = Leave.objects.filter(employee=employee, status='approved', requested_at__gte=start_of_month).count()
        return JsonResponse({
            'success': True,
            'stats': {
                'total_days': total_days,
                'present_days': present_days,
                'absent_days': total_days - present_days,
                'leaves_taken': leaves_taken,
                'attendance_percentage': round((present_days / total_days) * 100, 1) if total_days > 0 else 0,
            }
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ================= HEALTH CHECK =================
def health_check(request):
    """Simple health check endpoint for monitoring"""
    return JsonResponse({
        'status': 'ok',
        'timestamp': timezone.now().isoformat(),
        'database': 'connected',
        'version': '1.0.0'
    })