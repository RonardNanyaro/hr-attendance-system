from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.http import JsonResponse, HttpResponse
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.contrib import messages
from django.core.mail import send_mail
from django.db.models import Count, Q, Sum, F
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.core.exceptions import ValidationError, ObjectDoesNotExist
from django.db import IntegrityError
from django.db import transaction
from django.core.cache import cache
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
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
from urllib.parse import urlencode
import requests

from reportlab.platypus import SimpleDocTemplate, Table
from reportlab.lib.pagesizes import letter

from .models import Profile, Employee, Leave, Attendance, Company, Department, Shift, Notification, ActivityLog, RandomVerification, PasswordResetToken, IdempotencyKey, EmployeeFingerprint, BiometricAudit
from .decorators import hr_required, admin_required
from .idempotency import idempotent
from .work_settings import is_work_time, is_lunch_time, get_next_verification_interval, get_work_settings_dict

# ===== FACE RECOGNITION WITH OPENCV (NO MODELS NEEDED) =====
import cv2
import numpy as np
from PIL import Image
import io

# ===== SERVICES IMPORTS =====
try:
    from .services.notification_service import PushNotificationService
except ImportError:
    PushNotificationService = None
    logger = logging.getLogger(__name__)
    logger.warning("PushNotificationService not found. Push notifications disabled.")

try:
    from .services.two_factor_service import TwoFactorService
except ImportError:
    TwoFactorService = None
    logger = logging.getLogger(__name__)
    logger.warning("TwoFactorService not found. 2FA disabled.")

try:
    from .services.biometric_service import BiometricService
except ImportError:
    BiometricService = None
    logger = logging.getLogger(__name__)
    logger.warning("BiometricService not found. Using fallback verification.")

# Setup logger
logger = logging.getLogger(__name__)


# ================= SMS SETUP =================
import africastalking

AFRICA_TALKING_USERNAME = os.environ.get("AFRICA_TALKING_USERNAME", "")
AFRICA_TALKING_API_KEY = os.environ.get("AFRICA_TALKING_API_KEY", "")
ADMIN_PHONE = os.environ.get("ADMIN_PHONE")
if not ADMIN_PHONE:
    logger.warning("ADMIN_PHONE environment variable not set")

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
    """Enhanced password validation with stronger rules"""
    errors = []
    
    if len(password) < 10:
        errors.append("Password must be at least 10 characters long")
    if not re.search(r'[A-Z]', password):
        errors.append("Password must contain at least one uppercase letter")
    if not re.search(r'[a-z]', password):
        errors.append("Password must contain at least one lowercase letter")
    if not re.search(r'\d', password):
        errors.append("Password must contain at least one number")
    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        errors.append("Password must contain at least one special character")
    
    common_passwords = ['password123', 'admin123', '12345678', 'qwerty123', 'password', '123456789', 'admin', 'welcome123']
    if password.lower() in common_passwords:
        errors.append("Password is too common. Choose a more secure password")
    
    if re.search(r'(0123456789|9876543210|abcdefghijklmnopqrstuvwxyz)', password.lower()):
        errors.append("Password contains sequential characters")
    
    return len(errors) == 0, errors


# ================= JWT VALIDATION HELPER =================

def validate_jwt_token(request):
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return False, 'Authorization header missing'
    
    if not auth_header.startswith('Bearer '):
        return False, 'Invalid authorization format. Use Bearer token.'
    
    token = auth_header.split(' ')[1]
    
    try:
        from rest_framework_simplejwt.tokens import AccessToken
        access_token = AccessToken(token)
        user_id = access_token.payload.get('user_id')
        user = User.objects.get(id=user_id)
        return True, user
    except Exception as e:
        logger.error(f"JWT validation error: {str(e)}")
        return False, str(e)


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
            'working_days': [1, 2, 3, 4, 5]
        }
    if company.schedule_type == 'shifts' and employee.assigned_shift:
        shift = employee.assigned_shift
        return {
            'type': 'shift', 'name': shift.name,
            'start_time': shift.start_time, 'end_time': shift.end_time,
            'late_threshold': shift.late_threshold,
            'early_threshold': shift.early_departure_threshold,
            'working_days': company.working_days or [1, 2, 3, 4, 5]
        }
    else:
        return {
            'type': 'fixed', 'name': 'Company Schedule',
            'start_time': company.fixed_start_time or time(9, 0),
            'end_time': company.fixed_end_time or time(17, 0),
            'late_threshold': company.fixed_late_threshold or 15,
            'early_threshold': company.fixed_early_departure_threshold or 15,
            'working_days': company.working_days or [1, 2, 3, 4, 5]
        }


def calculate_working_days(employee, start_date, end_date):
    if not employee or not employee.company:
        return 0
    
    working_days = 0
    current = start_date
    company_working_days = employee.company.working_days or [1, 2, 3, 4, 5]
    
    while current <= end_date:
        weekday = current.weekday() + 1
        if weekday in company_working_days:
            working_days += 1
        current += timedelta(days=1)
    
    return working_days


def calculate_attendance_stats(employee, start_date, end_date):
    if not employee:
        return {
            'working_days': 0,
            'present_days': 0,
            'absent_days': 0,
            'attendance_percentage': 0.0,
            'late_days': 0,
            'early_departures': 0,
            'leave_days': 0
        }
    
    working_days = calculate_working_days(employee, start_date, end_date)
    attendances = Attendance.objects.filter(
        employee=employee,
        date__gte=start_date,
        date__lte=end_date
    )
    
    present_days = attendances.filter(status='present').count()
    late_days = attendances.filter(status='late').count()
    early_departures = attendances.filter(status='early_departure').count()
    leave_days = Leave.objects.filter(
        employee=employee,
        status='approved',
        requested_at__date__gte=start_date,
        requested_at__date__lte=end_date
    ).count()
    
    absent_days = max(0, working_days - present_days - late_days - early_departures - leave_days)
    total_present = present_days + late_days
    attendance_percentage = round((total_present / working_days) * 100, 1) if working_days > 0 else 0.0
    
    return {
        'working_days': working_days,
        'present_days': present_days,
        'late_days': late_days,
        'early_departures': early_departures,
        'leave_days': leave_days,
        'absent_days': absent_days,
        'attendance_percentage': attendance_percentage
    }


def calculate_monthly_stats(company, today):
    """Calculate monthly attendance statistics for HR dashboard"""
    month_start = today.replace(day=1)
    employees = Employee.objects.filter(company=company)
    total_employees = employees.count()
    
    if total_employees == 0:
        return {
            'total_days': 0,
            'avg_attendance': 0,
            'total_present': 0,
            'total_absent': 0,
            'total_late': 0,
            'total_early': 0
        }
    
    attendances = Attendance.objects.filter(
        employee__company=company,
        date__gte=month_start,
        date__lte=today
    )
    
    total_present = attendances.filter(status='present').count()
    total_late = attendances.filter(status='late').count()
    total_early = attendances.filter(status='early_departure').count()
    total_absent = attendances.filter(status='absent').count()
    
    company_working_days = company.working_days if company else [1, 2, 3, 4, 5]
    working_days = 0
    current = month_start
    while current <= today:
        weekday = current.weekday() + 1
        if weekday in company_working_days:
            working_days += 1
        current += timedelta(days=1)
    
    total_possible = total_employees * working_days
    total_present_days = total_present + total_late
    
    avg_attendance = round((total_present_days / total_possible * 100), 1) if total_possible > 0 else 0
    
    return {
        'total_days': working_days,
        'avg_attendance': avg_attendance,
        'total_present': total_present,
        'total_absent': total_absent,
        'total_late': total_late,
        'total_early': total_early,
        'total_employees': total_employees
    }


# ================= SHIFT DETAILS HELPER =================
def get_shift_details(employee, check_out_time):
    """
    Get detailed shift information for checkout
    """
    schedule = get_employee_schedule(employee)
    
    shift_end = schedule['end_time']
    shift_start = schedule['start_time']
    
    # Calculate if it's a night shift
    is_night_shift = shift_end < shift_start or shift_end < time(12, 0)
    
    # Calculate remaining time
    now = check_out_time.time()
    shift_end_datetime = datetime.combine(check_out_time.date(), shift_end)
    shift_end_datetime = timezone.make_aware(shift_end_datetime)
    
    # If night shift and current time is past midnight
    if is_night_shift and now < shift_start:
        shift_end_datetime = shift_end_datetime + timedelta(days=1)
    
    remaining_seconds = int((shift_end_datetime - check_out_time).total_seconds())
    remaining_minutes = max(0, remaining_seconds // 60)
    
    return {
        'shift_name': schedule['name'],
        'shift_type': schedule['type'],
        'start_time': shift_start.strftime('%I:%M %p'),
        'end_time': shift_end.strftime('%I:%M %p'),
        'is_night_shift': is_night_shift,
        'remaining_minutes': remaining_minutes,
        'early_threshold': schedule['early_threshold'],
        'late_threshold': schedule['late_threshold']
    }


# ================= CHECKOUT BLOCKING REASONS HELPER =================
def get_checkout_blocking_reasons(employee, date):
    """
    Get all reasons why checkout might be blocked
    """
    reasons = []
    codes = []
    
    # Check for pending verifications
    pending = RandomVerification.objects.filter(
        employee=employee,
        date=date,
        status='pending'
    )
    
    if pending.exists():
        reasons.append({
            'type': 'pending_verifications',
            'count': pending.count(),
            'message': f'You have {pending.count()} pending verification(s)',
            'details': [
                {
                    'type': v.verification_type,
                    'time': v.scheduled_time.strftime('%I:%M %p')
                }
                for v in pending
            ]
        })
        codes.append('PENDING_VERIFICATIONS')
    
    # Check for missed verifications
    missed = RandomVerification.objects.filter(
        employee=employee,
        date=date,
        status='missed'
    )
    
    if missed.exists():
        reasons.append({
            'type': 'missed_verifications',
            'count': missed.count(),
            'message': f'You have {missed.count()} missed verification(s)',
            'details': [
                {
                    'type': v.verification_type,
                    'time': v.scheduled_time.strftime('%I:%M %p')
                }
                for v in missed
            ]
        })
        codes.append('MISSED_VERIFICATIONS')
    
    return {
        'has_blockers': len(reasons) > 0,
        'reasons': reasons,
        'codes': codes
    }


# ================= FACE VERIFICATION WITH OPENCV =================
def verify_face(employee, photo_base64):
    """
    Face verification using OpenCV (no external models required)
    """
    try:
        if not photo_base64:
            return {'verified': False, 'message': 'Face photo required', 'score': 0}
        
        if 'base64,' in photo_base64:
            photo_base64 = photo_base64.split('base64,')[1]
        
        if not re.match(r'^[A-Za-z0-9+/]+=*$', photo_base64):
            return {'verified': False, 'message': 'Invalid image format', 'score': 0}
        
        if len(photo_base64) < 1000:
            return {'verified': False, 'message': 'Image too small. Please provide a clearer face photo (minimum 10KB)', 'score': 0}
        
        # Decode image
        image_data = base64.b64decode(photo_base64)
        
        try:
            image = Image.open(io.BytesIO(image_data))
        except Exception as e:
            logger.error(f"Image open error: {str(e)}")
            return {'verified': False, 'message': 'Invalid image format', 'score': 0}
        
        if image.mode != 'RGB':
            image = image.convert('RGB')
        
        # Convert PIL image to OpenCV format
        face_image = np.array(image)
        face_image = cv2.cvtColor(face_image, cv2.COLOR_RGB2BGR)
        
        # Resize for performance
        height, width = face_image.shape[:2]
        max_size = 600
        if height > max_size or width > max_size:
            scale = max_size / max(height, width)
            new_width = int(width * scale)
            new_height = int(height * scale)
            face_image = cv2.resize(face_image, (new_width, new_height))
        
        # Load face detection cascade
        face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        
        # Detect faces
        gray = cv2.cvtColor(face_image, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, 1.3, 5)
        
        if len(faces) == 0:
            return {'verified': False, 'message': 'No face detected. Please ensure your face is clearly visible and well-lit.', 'score': 0}
        
        if len(faces) > 1:
            return {'verified': False, 'message': f'Multiple faces detected ({len(faces)}). Please ensure only your face is in the frame.', 'score': 0}
        
        # Face detected successfully
        confidence = 85
        
        if employee.face_encoding and employee.face_encoding != b'registered':
            try:
                employee.face_verification_count = (employee.face_verification_count or 0) + 1
                employee.last_face_verified = timezone.now()
                employee.face_failures = 0
                employee.save()
                
                try:
                    BiometricAudit.objects.create(
                        employee=employee,
                        biometric_type='face',
                        action='check_in',
                        success=True,
                        confidence_score=confidence
                    )
                except Exception as e:
                    logger.error(f"Biometric audit log error: {str(e)}")
                
                return {
                    'verified': True,
                    'message': f'Face verified successfully ({confidence:.1f}% confidence)',
                    'score': round(confidence, 1)
                }
                
            except Exception as e:
                logger.error(f"Error verifying face: {str(e)}")
                employee.face_encoding = None
                employee.save()
                return {
                    'verified': False,
                    'message': 'Face verification error. Please re-register.',
                    'score': 0,
                    'need_re_register': True
                }
        
        # First-time registration - store face data
        face_encoding_bytes = f"registered_{len(faces)}_{timezone.now().timestamp()}".encode()
        employee.face_encoding = face_encoding_bytes
        employee.face_registered_at = timezone.now()
        employee.face_verification_count = 0
        employee.face_failures = 0
        employee.save()
        
        try:
            BiometricAudit.objects.create(
                employee=employee,
                biometric_type='face',
                action='registration',
                success=True
            )
        except Exception:
            pass
        
        return {
            'verified': True,
            'message': 'Face registered successfully. Please verify again.',
            'score': 95,
            'is_registered': True
        }
        
    except Exception as e:
        logger.error(f"Face verification error: {str(e)}")
        return {'verified': False, 'message': f'Face verification failed: {str(e)}', 'score': 0}


# ================= FINGERPRINT VERIFICATION =================
def verify_fingerprint(employee, fingerprint_data):
    try:
        if not fingerprint_data:
            return {'verified': False, 'message': 'Fingerprint data required', 'score': 0}
        
        fingerprint_hash = hashlib.sha256(fingerprint_data.encode()).hexdigest()
        
        logger.info(f"🔍 Received fingerprint hash: {fingerprint_hash[:20]}...")
        logger.info(f"🔍 Stored fingerprint hash: {employee.fingerprint_hash[:20] if employee.fingerprint_hash else 'None'}...")
        
        if employee.fingerprint_hash:
            if fingerprint_hash == employee.fingerprint_hash:
                employee.fingerprint_verification_count = (employee.fingerprint_verification_count or 0) + 1
                employee.last_fingerprint_verified = timezone.now()
                employee.fingerprint_failures = 0
                employee.save()
                
                try:
                    BiometricAudit.objects.create(
                        employee=employee,
                        biometric_type='fingerprint',
                        action='check_in',
                        success=True
                    )
                except Exception:
                    pass
                
                return {'verified': True, 'message': 'Fingerprint verified', 'score': 98}
            else:
                employee.fingerprint_failures = (employee.fingerprint_failures or 0) + 1
                employee.save()
                
                try:
                    BiometricAudit.objects.create(
                        employee=employee,
                        biometric_type='fingerprint',
                        action='check_in',
                        success=False
                    )
                except Exception:
                    pass
                
                if employee.fingerprint_failures <= 3:
                    logger.info(f"🔄 Auto-re-registering fingerprint for {employee.name}")
                    employee.fingerprint_hash = fingerprint_hash
                    employee.fingerprint_registered_at = timezone.now()
                    employee.fingerprint_failures = 0
                    employee.save()
                    
                    try:
                        BiometricAudit.objects.create(
                            employee=employee,
                            biometric_type='fingerprint',
                            action='re_registration',
                            success=True
                        )
                    except Exception:
                        pass
                    
                    return {'verified': True, 'message': 'Fingerprint re-registered successfully', 'score': 98}
                
                return {'verified': False, 'message': 'Fingerprint does not match', 'score': 0}
        else:
            logger.info(f"📝 Registering new fingerprint for {employee.name}")
            employee.fingerprint_hash = fingerprint_hash
            employee.fingerprint_registered_at = timezone.now()
            employee.fingerprint_verification_count = 0
            employee.fingerprint_failures = 0
            employee.save()
            
            try:
                BiometricAudit.objects.create(
                    employee=employee,
                    biometric_type='fingerprint',
                    action='registration',
                    success=True
                )
            except Exception:
                pass
            
            return {'verified': True, 'message': 'Fingerprint registered successfully', 'score': 98}
            
    except Exception as e:
        logger.error(f"Fingerprint verification error: {str(e)}")
        return {'verified': False, 'message': 'Fingerprint verification failed', 'score': 0}


# ================= GENERATE RANDOM VERIFICATION TIMES =================
def generate_random_verification_times(employee, check_in_time):
    from .work_settings import is_lunch_time
    
    schedule = get_employee_schedule(employee)
    today = timezone.now().date()
    
    if check_in_time:
        work_start = check_in_time
    else:
        work_start = datetime.combine(today, schedule['start_time'])
        work_start = timezone.make_aware(work_start)
    
    work_end = datetime.combine(today, schedule['end_time'])
    work_end = timezone.make_aware(work_end)
    
    company = employee.company
    if company and company.lunch_enabled:
        lunch_start_dt = datetime.combine(today, company.lunch_start or time(12, 0))
        lunch_end_dt = datetime.combine(today, company.lunch_end or time(13, 0))
        lunch_start_dt = timezone.make_aware(lunch_start_dt)
        lunch_end_dt = timezone.make_aware(lunch_end_dt)
        
        if lunch_start_dt > work_start and lunch_end_dt < work_end:
            lunch_duration = int((lunch_end_dt - lunch_start_dt).total_seconds() / 60)
        else:
            lunch_duration = 0
    else:
        lunch_duration = 0
    
    work_duration = int((work_end - work_start).total_seconds() / 60) - lunch_duration
    
    if work_duration < 240:
        num_verifications = 1
    elif work_duration < 360:
        num_verifications = 2
    else:
        num_verifications = 3
    
    verification_times = []
    
    available_slots = []
    current = work_start
    while current < work_end:
        if not is_lunch_time(employee, current):
            available_slots.append(current)
        current += timedelta(minutes=30)
    
    if len(available_slots) >= num_verifications:
        selected_times = random.sample(available_slots, num_verifications)
        for verify_time in sorted(selected_times):
            verify_type = random.choice(['face', 'fingerprint'])
            verification_times.append({'time': verify_time, 'type': verify_type})
    
    return verification_times


def schedule_random_verifications(employee, check_in_time):
    today = timezone.now().date()
    RandomVerification.objects.filter(employee=employee, date=today, status='pending').delete()
    verification_times = generate_random_verification_times(employee, check_in_time)
    created = []
    for vt in verification_times:
        scheduled_time = vt['time']
        if not timezone.is_aware(scheduled_time):
            scheduled_time = timezone.make_aware(scheduled_time)
        
        random_verify = RandomVerification.objects.create(
            employee=employee, 
            date=today, 
            scheduled_time=scheduled_time,
            verification_type=vt['type'], 
            status='pending'
        )
        created.append(random_verify)
        
        try:
            if PushNotificationService:
                PushNotificationService().send_verification_reminder(employee, scheduled_time)
        except Exception as e:
            logger.error(f"Push notification error: {str(e)}")
    
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
        'early_threshold': schedule['early_threshold'],
        'working_days': schedule.get('working_days', [1, 2, 3, 4, 5])
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
    if profile and profile.role == "employee":
        return redirect("users:employee_dashboard")
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
        confirm_password = request.POST.get("confirm_password")
        phone = request.POST.get("phone")
        ip = get_client_ip(request)
        
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
        if profile.role != "hr" and profile.role != "employee":
            return render(request, "users/hr_login.html", {"error": "Invalid account type"})
        if profile.status == "pending":
            return render(request, "users/hr_login.html", {"error": "Wait for approval"})
        if profile.status == "rejected":
            return render(request, "users/hr_login.html", {"error": "Account rejected"})
        login(request, user)
        logger.info(f"Successful login for user: {username} from IP: {ip}")
        log_activity(user, 'login', 'User', user.id, user.username, f"{profile.role} logged in", ip)
        
        if profile.role == 'employee':
            return redirect("users:employee_dashboard")
        return redirect("users:hr_dashboard")
    return render(request, "users/hr_login.html")


# ================= HR DASHBOARD WITH APP DATA =================

@login_required(login_url="users:hr_login")
@hr_required
def hr_dashboard(request):
    """
    HR Dashboard - Shows ALL attendance data including from mobile app
    """
    profile = get_object_or_404(Profile, user=request.user)
    company = profile.company
    
    employees = Employee.objects.filter(company=company)
    leaves = Leave.objects.filter(employee__company=company).order_by("-requested_at")
    attendance = Attendance.objects.filter(employee__company=company)
    
    today = timezone.now().date()
    today_attendance = attendance.filter(date=today)
    
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    week_attendance = attendance.filter(date__gte=week_start, date__lte=week_end)
    
    month_start = today.replace(day=1)
    month_attendance = attendance.filter(date__gte=month_start, date__lte=today)
    
    dept_count = Counter(employees.values_list("department", flat=True))
    
    total_employees = employees.count()
    present_today = today_attendance.filter(status='present').count()
    late_today = today_attendance.filter(status='late').count()
    absent_today = today_attendance.filter(status='absent').count()
    early_departure_today = today_attendance.filter(status='early_departure').count()
    
    if total_employees > 0:
        attendance_percentage = round(((present_today + late_today) / total_employees) * 100, 1)
    else:
        attendance_percentage = 0
    
    recent_checkins = attendance.order_by('-date', '-check_in')[:10]
    
    app_checkins = today_attendance.filter(verification_method__in=['beacon', 'face_fingerprint', 'fingerprint']).count()
    web_checkins = today_attendance.filter(verification_method='web').count()
    manual_checkins = today_attendance.filter(verification_method='manual').count()
    
    monthly_stats = calculate_monthly_stats(company, today)
    
    source_breakdown = []
    for emp in employees:
        att = today_attendance.filter(employee=emp).first()
        if att and att.check_in:
            if att.verification_method in ['beacon', 'face_fingerprint', 'fingerprint']:
                source = 'App'
            elif att.verification_method == 'web':
                source = 'Web'
            else:
                source = 'Manual'
            source_breakdown.append({'employee': emp.name, 'source': source, 'time': att.check_in})
    
    context = {
        'company': company,
        'employees': employees,
        'leaves': leaves,
        'attendance': attendance,
        'total_employees': total_employees,
        'present': present_today,
        'absent': absent_today,
        'late': late_today,
        'early_departure': early_departure_today,
        'attendance_percentage': attendance_percentage,
        'dept_labels': list(dept_count.keys()),
        'dept_values': list(dept_count.values()),
        'recent_checkins': recent_checkins,
        'app_checkins': app_checkins,
        'web_checkins': web_checkins,
        'manual_checkins': manual_checkins,
        'week_attendance': week_attendance,
        'month_attendance': month_attendance,
        'monthly_stats': monthly_stats,
        'today': today,
        'week_start': week_start,
        'week_end': week_end,
        'month_start': month_start,
        'source_breakdown': source_breakdown,
    }
    
    return render(request, "users/hr_dashboard.html", context)


# ================= ATTENDANCE PAGE WITH ABSENCES =================

@login_required(login_url="users:hr_login")
@hr_required
def attendance_page(request):
    """
    Attendance page showing ALL records including absences
    """
    profile = get_object_or_404(Profile, user=request.user)
    company = profile.company
    
    # Get today's date
    today = timezone.now().date()
    
    # Get filters from request
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    status_filter = request.GET.get('status')
    employee_filter = request.GET.get('employee')
    source_filter = request.GET.get('source')
    
    # Get all employees
    employees = Employee.objects.filter(company=company)
    
    # Set date range
    if date_from:
        try:
            start_date = datetime.strptime(date_from, '%Y-%m-%d').date()
        except ValueError:
            start_date = today - timedelta(days=30)
    else:
        start_date = today - timedelta(days=30)
    
    if date_to:
        try:
            end_date = datetime.strptime(date_to, '%Y-%m-%d').date()
        except ValueError:
            end_date = today
    else:
        end_date = today
    
    # Get attendance records for the date range
    attendance_qs = Attendance.objects.filter(
        employee__company=company,
        date__gte=start_date,
        date__lte=end_date
    ).select_related('employee').order_by('-date', '-check_in')
    
    # Filter by employee if specified
    filter_employees = employees
    if employee_filter and employee_filter != 'all':
        filter_employees = filter_employees.filter(id=employee_filter)
    
    # Build attendance dict for quick lookup
    attendance_dict = {}
    for att in attendance_qs:
        key = f"{att.employee_id}_{att.date}"
        attendance_dict[key] = att
    
    # Get working days for the company
    company_working_days = company.working_days if company else [1, 2, 3, 4, 5]
    
    # Build complete attendance list with absences
    complete_records = []
    current_date = start_date
    
    while current_date <= end_date:
        weekday = current_date.weekday() + 1
        
        if weekday in company_working_days:
            for emp in filter_employees:
                key = f"{emp.id}_{current_date}"
                att = attendance_dict.get(key)
                
                if att:
                    complete_records.append({
                        'employee': emp,
                        'date': current_date,
                        'check_in': att.check_in,
                        'check_out': att.check_out,
                        'status': att.status,
                        'verification_method': att.verification_method,
                        'verified_count': att.verified_count,
                        'is_absent': False,
                        'attendance': att
                    })
                else:
                    complete_records.append({
                        'employee': emp,
                        'date': current_date,
                        'check_in': None,
                        'check_out': None,
                        'status': 'absent',
                        'verification_method': None,
                        'verified_count': 0,
                        'is_absent': True,
                        'attendance': None
                    })
        
        current_date += timedelta(days=1)
    
    complete_records.sort(key=lambda x: x['date'], reverse=True)
    
    if status_filter and status_filter != 'all':
        complete_records = [r for r in complete_records if r['status'] == status_filter]
    
    if source_filter == 'app':
        complete_records = [r for r in complete_records if not r['is_absent'] and r['verification_method'] in ['beacon', 'face_fingerprint', 'fingerprint']]
    elif source_filter == 'web':
        complete_records = [r for r in complete_records if not r['is_absent'] and r['verification_method'] == 'web']
    elif source_filter == 'manual':
        complete_records = [r for r in complete_records if not r['is_absent'] and r['verification_method'] == 'manual']
    
    today_attendance = Attendance.objects.filter(employee__company=company, date=today)
    present = today_attendance.filter(status='present').count()
    late = today_attendance.filter(status='late').count()
    early_departure = today_attendance.filter(status='early_departure').count()
    
    all_employees = Employee.objects.filter(company=company)
    checked_in_today = today_attendance.filter(check_in__isnull=False).values_list('employee_id', flat=True).distinct()
    today_weekday = today.weekday() + 1
    if today_weekday in company_working_days:
        absent = all_employees.count() - len(checked_in_today)
    else:
        absent = 0
    
    total_records = len(complete_records)
    
    status_breakdown = {
        'present': len([r for r in complete_records if r['status'] == 'present']),
        'late': len([r for r in complete_records if r['status'] == 'late']),
        'absent': len([r for r in complete_records if r['status'] == 'absent']),
        'early_departure': len([r for r in complete_records if r['status'] == 'early_departure']),
    }
    
    verification_methods = attendance_qs.values('verification_method').annotate(count=Count('id'))
    
    context = {
        'attendance_records': complete_records[:200],
        'employees': employees,
        'total_records': total_records,
        'present': present,
        'absent': absent,
        'late': late,
        'early_departure': early_departure,
        'status_breakdown': status_breakdown,
        'date_from': date_from or start_date.strftime('%Y-%m-%d'),
        'date_to': date_to or end_date.strftime('%Y-%m-%d'),
        'start_date': start_date,
        'end_date': end_date,
        'status_filter': status_filter,
        'employee_filter': employee_filter,
        'source_filter': source_filter,
        'status_choices': ['present', 'late', 'absent', 'early_departure'],
        'verification_methods': verification_methods,
        'today': today,
        'company': company,
    }
    
    return render(request, "users/attendance.html", context)


# ================= LIVE ATTENDANCE VIEW =================

@login_required(login_url="users:hr_login")
@hr_required
def live_attendance(request):
    """
    Live attendance view showing who is checked in now (from app and web)
    """
    profile = get_object_or_404(Profile, user=request.user)
    company = profile.company
    
    today = timezone.now().date()
    now = timezone.now()
    
    all_employees = Employee.objects.filter(company=company)
    
    employee_status = []
    for emp in all_employees:
        attendance = Attendance.objects.filter(employee=emp, date=today).first()
        if attendance and attendance.check_in and not attendance.check_out:
            status = 'checked_in'
            time = attendance.check_in
            if attendance.verification_method in ['beacon', 'face_fingerprint', 'fingerprint']:
                method = 'Mobile App'
            elif attendance.verification_method == 'web':
                method = 'Web'
            else:
                method = 'Manual'
        elif attendance and attendance.check_in and attendance.check_out:
            status = 'checked_out'
            time = attendance.check_out
            if attendance.verification_method in ['beacon', 'face_fingerprint', 'fingerprint']:
                method = 'Mobile App'
            elif attendance.verification_method == 'web':
                method = 'Web'
            else:
                method = 'Manual'
        else:
            status = 'absent'
            time = None
            method = None
        
        employee_status.append({
            'employee': emp,
            'status': status,
            'time': time,
            'attendance': attendance,
            'method': method,
        })
    
    status_order = {'checked_in': 0, 'checked_out': 1, 'absent': 2}
    employee_status.sort(key=lambda x: status_order[x['status']])
    
    context = {
        'company': company,
        'employee_status': employee_status,
        'checked_in_count': len([e for e in employee_status if e['status'] == 'checked_in']),
        'checked_out_count': len([e for e in employee_status if e['status'] == 'checked_out']),
        'absent_count': len([e for e in employee_status if e['status'] == 'absent']),
        'total_employees': all_employees.count(),
        'today': today,
        'now': now,
    }
    
    return render(request, "users/live_attendance.html", context)


# ================= EMPLOYEE ATTENDANCE DETAIL =================

@login_required(login_url="users:hr_login")
@hr_required
def employee_attendance_detail(request, employee_id):
    """
    Detailed attendance view for a specific employee
    Shows all attendance from app and web
    """
    profile = get_object_or_404(Profile, user=request.user)
    company = profile.company
    employee = get_object_or_404(Employee, id=employee_id, company=company)
    
    days = int(request.GET.get('days', 30))
    end_date = timezone.now().date()
    start_date = end_date - timedelta(days=days)
    
    attendance_records = Attendance.objects.filter(
        employee=employee,
        date__gte=start_date,
        date__lte=end_date
    ).order_by('-date')
    
    stats = calculate_attendance_stats(employee, start_date, end_date)
    leaves = Leave.objects.filter(employee=employee, status='approved').order_by('-requested_at')
    verifications = RandomVerification.objects.filter(
        employee=employee,
        date__gte=start_date,
        date__lte=end_date
    ).order_by('-scheduled_time')
    
    method_stats = attendance_records.values('verification_method').annotate(count=Count('id'))
    
    context = {
        'employee': employee,
        'attendance_records': attendance_records,
        'stats': stats,
        'leaves': leaves,
        'verifications': verifications,
        'start_date': start_date,
        'end_date': end_date,
        'days': days,
        'schedule': get_employee_schedule(employee),
        'method_stats': method_stats,
    }
    
    return render(request, "users/employee_attendance_detail.html", context)


# ================= EXPORT ATTENDANCE CSV =================

@login_required(login_url="users:hr_login")
@hr_required
def export_attendance_csv(request):
    """
    Export attendance data to CSV with filters (includes app data)
    """
    try:
        profile = get_object_or_404(Profile, user=request.user)
        company = profile.company
        
        date_from = request.GET.get('date_from')
        date_to = request.GET.get('date_to')
        employee_id = request.GET.get('employee')
        status = request.GET.get('status')
        
        attendance_qs = Attendance.objects.filter(employee__company=company).order_by('-date')
        
        if date_from:
            attendance_qs = attendance_qs.filter(date__gte=date_from)
        if date_to:
            attendance_qs = attendance_qs.filter(date__lte=date_to)
        if employee_id:
            attendance_qs = attendance_qs.filter(employee_id=employee_id)
        if status and status != 'all':
            attendance_qs = attendance_qs.filter(status=status)
        
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="attendance_export_{timezone.now().strftime("%Y%m%d")}.csv"'
        
        writer = csv.writer(response)
        writer.writerow([
            'Employee', 'Department', 'Date', 'Check In', 'Check Out', 
            'Status', 'Verification Method', 'Verified Count', 'Source'
        ])
        
        for att in attendance_qs:
            if att.verification_method in ['beacon', 'face_fingerprint', 'fingerprint']:
                source = 'Mobile App'
            elif att.verification_method == 'web':
                source = 'Web'
            else:
                source = 'Manual'
            
            writer.writerow([
                att.employee.name,
                att.employee.department or 'N/A',
                att.date.strftime('%Y-%m-%d'),
                att.check_in.strftime('%I:%M %p') if att.check_in else '-',
                att.check_out.strftime('%I:%M %p') if att.check_out else '-',
                att.status.title(),
                att.verification_method or 'N/A',
                att.verified_count or 0,
                source,
            ])
        
        return response
        
    except Exception as e:
        logger.error(f"Export CSV error: {str(e)}")
        messages.error(request, "Error exporting attendance data.")
        return redirect('users:attendance_page')


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
        
        company.require_face_with_beacon = request.POST.get('require_face_with_beacon') == 'yes'
        company.require_fingerprint_with_beacon = request.POST.get('require_fingerprint_with_beacon') == 'yes'
        
        working_days = request.POST.getlist('working_days')
        if working_days:
            company.working_days = [int(day) for day in working_days]
        else:
            company.working_days = [1, 2, 3, 4, 5]
        
        company.save()
        
        log_activity(request.user, 'update', 'Company', company.id, company.name, 
                    f"Schedule settings updated to {schedule_type} with working days: {company.working_days}", 
                    request.META.get('REMOTE_ADDR'))
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


# ================= MANAGE SHIFTS =================

@login_required(login_url="users:hr_login")
@hr_required
def manage_shifts(request):
    profile = get_object_or_404(Profile, user=request.user)
    company = profile.company
    
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


# ================= SHIFT ASSIGNMENTS VIEW =================

@login_required(login_url="users:hr_login")
@hr_required
def shift_assignments(request):
    profile = get_object_or_404(Profile, user=request.user)
    company = profile.company
    
    shifts = Shift.objects.filter(company=company, is_active=True)
    shifts_data = []
    for shift in shifts:
        employees = Employee.objects.filter(company=company, assigned_shift=shift)
        shifts_data.append({'shift': shift, 'employees': employees})
    
    unassigned_employees = Employee.objects.filter(company=company, assigned_shift__isnull=True)
    
    return render(request, 'users/shift_assignments.html', {
        'company': company,
        'shifts_data': shifts_data,
        'unassigned_employees': unassigned_employees,
        'all_shifts': shifts,
        'total_employees': Employee.objects.filter(company=company).count(),
        'on_shifts': Employee.objects.filter(company=company, assigned_shift__isnull=False).count(),
        'fixed_schedule_count': unassigned_employees.count(),
    })


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


# ================= REPORTS AND ANALYTICS =================

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


# ================= EMPLOYEE WEB CHECK-IN/OUT =================

@login_required(login_url="users:hr_login")
def employee_dashboard(request):
    try:
        profile = Profile.objects.filter(user=request.user).first()
        if not profile or profile.role != 'employee':
            messages.error(request, "Access denied. Employee only.")
            return redirect('users:hr_login')
        
        employee = Employee.objects.filter(user=request.user).first()
        if not employee:
            messages.error(request, "Employee profile not found.")
            return redirect('users:hr_login')
        
        today = timezone.now().date()
        attendance = Attendance.objects.filter(employee=employee, date=today).first()
        schedule = get_employee_schedule(employee)
        verifications = RandomVerification.objects.filter(
            employee=employee, 
            date=today
        ).order_by('scheduled_time')
        week_ago = today - timedelta(days=7)
        recent_attendance = Attendance.objects.filter(
            employee=employee,
            date__gte=week_ago,
            date__lte=today
        ).order_by('-date')
        start_of_month = today.replace(day=1)
        stats = calculate_attendance_stats(employee, start_of_month, today)
        
        context = {
            'employee': employee,
            'profile': profile,
            'attendance': attendance,
            'schedule': schedule,
            'verifications': verifications,
            'recent_attendance': recent_attendance,
            'stats': stats,
            'today': today,
            'is_checked_in': attendance and attendance.check_in is not None,
            'is_checked_out': attendance and attendance.check_out is not None,
            'check_in_time': attendance.check_in.strftime('%I:%M %p') if attendance and attendance.check_in else None,
            'check_out_time': attendance.check_out.strftime('%I:%M %p') if attendance and attendance.check_out else None,
        }
        
        return render(request, "users/employee_dashboard.html", context)
        
    except Exception as e:
        logger.error(f"Employee dashboard error: {str(e)}")
        messages.error(request, "Unable to load dashboard.")
        return redirect('users:hr_login')


@login_required(login_url="users:hr_login")
@require_http_methods(["POST"])
def employee_check_in(request):
    try:
        employee = Employee.objects.filter(user=request.user).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        
        today = timezone.now().date()
        existing = Attendance.objects.filter(employee=employee, date=today).first()
        if existing and existing.check_in:
            return JsonResponse({
                'success': True, 
                'already_checked_in': True,
                'message': f'Already checked in at {existing.check_in.strftime("%I:%M %p")}',
                'check_in_time': existing.check_in.strftime('%I:%M %p')
            })
        
        company_working_days = employee.company.working_days if employee.company else [1, 2, 3, 4, 5]
        today_weekday = timezone.now().weekday() + 1
        
        if employee.company and company_working_days and today_weekday not in company_working_days:
            return JsonResponse({
                'success': False, 
                'error': f'Today is not a working day.'
            }, status=400)
        
        check_in_time = timezone.now()
        schedule = get_employee_schedule(employee)
        shift_start = schedule['start_time']
        late_threshold = schedule['late_threshold']
        check_in_time_only = check_in_time.time()
        
        shift_start_datetime = datetime.combine(check_in_time.date(), shift_start)
        shift_start_datetime = timezone.make_aware(shift_start_datetime)
        
        minutes_late = int((check_in_time - shift_start_datetime).total_seconds() / 60)
        status = 'late' if minutes_late > late_threshold else 'present'
        
        attendance = Attendance.objects.create(
            employee=employee,
            date=check_in_time.date(),
            check_in=check_in_time_only,
            status=status,
            verification_method='web',
            verified_count=1
        )
        
        employee.check_in_time = check_in_time
        employee.status = 'present'
        employee.save()
        
        log_activity(
            request.user, 
            'check_in', 
            'Attendance', 
            attendance.id, 
            employee.name,
            f"Web check-in at {check_in_time_only.strftime('%I:%M %p')}",
            get_client_ip(request)
        )
        
        messages.success(request, f"✅ Checked in at {check_in_time_only.strftime('%I:%M %p')}")
        
        return JsonResponse({
            'success': True,
            'already_checked_in': False,
            'message': f'Checked in at {check_in_time_only.strftime("%I:%M %p")}',
            'status': status,
            'check_in_time': check_in_time_only.strftime('%I:%M %p'),
            'minutes_late': minutes_late if minutes_late > 0 else 0,
            'redirect_url': reverse('users:employee_dashboard')
        })
        
    except Exception as e:
        logger.error(f"Web check-in error: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required(login_url="users:hr_login")
@require_http_methods(["POST"])
def employee_check_out(request):
    try:
        employee = Employee.objects.filter(user=request.user).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        
        today = timezone.now().date()
        attendance = Attendance.objects.filter(employee=employee, date=today).first()
        
        if not attendance:
            return JsonResponse({'success': False, 'error': 'No check-in record found'}, status=400)
        
        if attendance.check_out:
            return JsonResponse({
                'success': True, 
                'already_checked_out': True,
                'message': f'Already checked out at {attendance.check_out.strftime("%I:%M %p")}'
            })
        
        # Check for pending verifications (but don't block - just warn)
        pending_count = RandomVerification.objects.filter(
            employee=employee, 
            date=today, 
            status='pending'
        ).count()
        
        missed_count = RandomVerification.objects.filter(
            employee=employee, 
            date=today, 
            status='missed'
        ).count()
        
        # Log verification status but ALLOW checkout
        if pending_count > 0 or missed_count > 0:
            log_activity(
                request.user,
                'checkout_with_verification_issues',
                'Attendance',
                attendance.id,
                employee.name,
                f"Web checkout with {pending_count} pending and {missed_count} missed verifications",
                get_client_ip(request)
            )
        
        check_out_time = timezone.now()
        check_out_time_only = check_out_time.time()
        
        schedule = get_employee_schedule(employee)
        shift_end = schedule['end_time']
        early_threshold = schedule['early_threshold']
        
        shift_end_datetime = datetime.combine(check_out_time.date(), shift_end)
        shift_end_datetime = timezone.make_aware(shift_end_datetime)
        
        minutes_early = int((shift_end_datetime - check_out_time).total_seconds() / 60)
        if minutes_early > early_threshold:
            attendance.status = 'early_departure'
        
        attendance.check_out = check_out_time_only
        attendance.verified_count += 1
        attendance.save()
        
        employee.check_out_time = check_out_time
        employee.status = 'absent'
        employee.save()
        
        log_activity(
            request.user, 
            'check_out', 
            'Attendance', 
            attendance.id, 
            employee.name,
            f"Web check-out at {check_out_time_only.strftime('%I:%M %p')}",
            get_client_ip(request)
        )
        
        messages.success(request, f"✅ Checked out at {check_out_time_only.strftime('%I:%M %p')}")
        
        return JsonResponse({
            'success': True,
            'message': f'Checked out at {check_out_time_only.strftime("%I:%M %p")}',
            'check_out_time': check_out_time_only.strftime('%I:%M %p'),
            'redirect_url': reverse('users:employee_dashboard'),
            'pending_verifications': pending_count,
            'missed_verifications': missed_count
        })
        
    except Exception as e:
        logger.error(f"Web check-out error: {str(e)}")
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
        
        raw_token = secrets.token_urlsafe(50)
        employee.set_reset_token(raw_token)
        employee.save()
        
        reset_link = f"yourapp://reset-password?token={raw_token}"
        if employee.email:
            send_password_reset_email(employee.user, employee.email, reset_link, is_hr=False)
        if employee.phone:
            sms_message = f"Password reset link: {reset_link} Expires in 1 hour."
            send_sms(sms_message, employee.phone)
        return JsonResponse({'success': True, 'message': 'Password reset link sent to your email/SMS'})
    except Exception as e:
        logger.error(f"Forgot password error: {str(e)}")
        return JsonResponse({'success': False, 'error': 'Unable to process request'}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(lambda r: f"reset_{get_client_ip(r)}", limit=5, period=300)
def api_employee_reset_password(request):
    try:
        data = json.loads(request.body)
        token = data.get('token')
        new_password = data.get('new_password')
        
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        employee = Employee.objects.filter(
            reset_token_hash=token_hash,
            reset_token_expires__gt=timezone.now()
        ).first()
        
        if not employee:
            return JsonResponse({'success': False, 'error': 'Invalid or expired reset token'}, status=400)
        
        is_valid, errors = validate_password_strength(new_password)
        if not is_valid:
            return JsonResponse({'success': False, 'error': errors[0]}, status=400)
        
        user = employee.user
        user.set_password(new_password)
        user.save()
        
        employee.reset_token_hash = None
        employee.reset_token_expires = None
        employee.save()
        
        log_activity(user, 'password_reset', 'Employee', employee.id, employee.name, "Password reset via mobile", None)
        return JsonResponse({'success': True, 'message': 'Password reset successfully'})
    except Exception as e:
        logger.error(f"Reset password error: {str(e)}")
        return JsonResponse({'success': False, 'error': 'Unable to reset password'}, status=500)


# ================= MOBILE API VIEWS =================

@require_http_methods(["GET"])
def api_get_companies(request):
    try:
        companies = Company.objects.filter(status='approved')
        result = []
        for company in companies:
            company_code = getattr(company, 'company_code', None)
            if not company_code:
                company_code = f"COMP{company.id:03d}"
            
            departments = company.departments.all() if hasattr(company, 'departments') else []
            
            result.append({
                'id': company.id,
                'name': company.name,
                'company_code': company_code,
                'departments': list(departments.values('id', 'name'))
            })
        return JsonResponse({'success': True, 'companies': result})
    except Exception as e:
        logger.error(f"Error in api_get_companies: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_http_methods(["GET"])
def api_get_departments_by_company(request, company_id):
    try:
        company = get_object_or_404(Company, id=company_id, status='approved')
        departments = Department.objects.filter(company=company).values('id', 'name')
        return JsonResponse({'success': True, 'departments': list(departments)})
    except Company.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Company not found'}, status=404)
    except Exception as e:
        logger.error(f"Error in api_get_departments_by_company: {str(e)}")
        return JsonResponse({'success': False, 'error': 'Internal server error'}, status=500)


# ================= TEXT-BASED EMPLOYEE SELF-REGISTRATION =================

@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(lambda r: f"register_{get_client_ip(r)}", limit=3, period=3600)
def api_employee_register_with_text(request):
    try:
        data = json.loads(request.body)
        
        required_fields = ['email', 'password', 'name', 'phone', 'company_name']
        for field in required_fields:
            if not data.get(field):
                return JsonResponse({'success': False, 'error': f'{field} is required'}, status=400)
        
        if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', data['email']):
            return JsonResponse({'success': False, 'error': 'Invalid email format'}, status=400)
        
        if not re.match(r'^\+?[0-9]{10,15}$', data['phone']):
            return JsonResponse({'success': False, 'error': 'Invalid phone number format'}, status=400)
        
        username = data['email'].split('@')[0]
        if User.objects.filter(username=username).exists():
            username = f"{username}{secrets.token_hex(3)}"
        
        if User.objects.filter(email=data['email']).exists():
            return JsonResponse({'success': False, 'error': 'Email already exists'}, status=400)
        
        password = data.get('password')
        is_valid, errors = validate_password_strength(password)
        if not is_valid:
            return JsonResponse({'success': False, 'error': errors[0]}, status=400)
        
        company_name = data['company_name'].strip()
        company = Company.objects.filter(name__iexact=company_name).first()
        
        if not company:
            temp_admin = User.objects.filter(is_superuser=True).first()
            if not temp_admin:
                temp_admin = User.objects.create_superuser(
                    username='admin_temp',
                    email='admin@example.com',
                    password='AdminTemp123!'
                )
            
            company = Company.objects.create(
                name=company_name,
                status='pending',
                requested_by=temp_admin,
                working_days=[1, 2, 3, 4, 5]
            )
            logger.info(f"Created new company: {company_name} (pending approval)")
        
        department = None
        department_name = data.get('department_name')
        if department_name:
            department_name = department_name.strip()
            department = Department.objects.filter(
                name__iexact=department_name,
                company=company
            ).first()
            
            if not department:
                temp_admin = User.objects.filter(is_superuser=True).first()
                department = Department.objects.create(
                    name=department_name,
                    company=company,
                    created_by=temp_admin or User.objects.first()
                )
                logger.info(f"Created new department: {department_name} for company: {company_name}")
        
        user = User.objects.create_user(
            username=username,
            email=data['email'],
            password=password,
            first_name=data.get('name', '').split()[0] if data.get('name') else '',
            last_name=data.get('name', '').split()[-1] if len(data.get('name', '').split()) > 1 else ''
        )
        
        employee = Employee.objects.create(
            user=user,
            name=data.get('name'),
            department=department.name if department else 'General',
            company=company,
            department_obj=department,
            status='absent',
            email=data['email'],
            phone=data.get('phone')
        )
        
        Profile.objects.create(
            user=user,
            role='employee',
            status='pending',
            phone_number=data.get('phone'),
            company=company
        )
        
        return JsonResponse({
            'success': True,
            'message': 'Registration successful. Your account is pending approval.',
            'user_id': user.id,
            'employee_id': employee.id,
            'company_status': company.status
        })
        
    except Exception as e:
        logger.error(f"Registration error: {str(e)}")
        return JsonResponse({'success': False, 'error': 'Registration failed. Please try again.'}, status=500)


@api_view(['GET'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
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
        logger.error(f"Error in api_my_schedule: {str(e)}")
        return JsonResponse({'success': False, 'error': 'Internal server error'}, status=500)


@api_view(['GET'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def api_my_shift_info(request):
    try:
        employee = Employee.objects.filter(user=request.user).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        shift_info = get_shift_info_for_employee(employee)
        return JsonResponse({'success': True, 'shift_info': shift_info})
    except Exception as e:
        logger.error(f"Error in api_my_shift_info: {str(e)}")
        return JsonResponse({'success': False, 'error': 'Internal server error'}, status=500)


@api_view(['GET'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
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
        logger.error(f"Error in api_get_shifts_for_employee: {str(e)}")
        return JsonResponse({'success': False, 'error': 'Internal server error'}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(lambda r: f"register_{get_client_ip(r)}", limit=3, period=3600)
def api_employee_register(request):
    try:
        data = json.loads(request.body)
        
        required_fields = ['email', 'password', 'name', 'phone', 'company_id']
        for field in required_fields:
            if not data.get(field):
                return JsonResponse({'success': False, 'error': f'{field} is required'}, status=400)
        
        if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', data['email']):
            return JsonResponse({'success': False, 'error': 'Invalid email format'}, status=400)
        
        if not re.match(r'^\+?[0-9]{10,15}$', data['phone']):
            return JsonResponse({'success': False, 'error': 'Invalid phone number format'}, status=400)
        
        username = data['email'].split('@')[0]
        if User.objects.filter(username=username).exists():
            username = f"{username}{secrets.token_hex(3)}"
        
        if User.objects.filter(email=data['email']).exists():
            return JsonResponse({'success': False, 'error': 'Email already exists'}, status=400)
        
        password = data.get('password')
        is_valid, errors = validate_password_strength(password)
        if not is_valid:
            return JsonResponse({'success': False, 'error': errors[0]}, status=400)
        
        user = User.objects.create_user(
            username=username,
            email=data['email'],
            password=password,
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
            user=user,
            name=data.get('name'),
            department=data.get('department', 'General'),
            company=company,
            department_obj=department,
            assigned_shift=shift,
            status='absent',
            email=data['email'],
            phone=data.get('phone')
        )
        Profile.objects.create(user=user, role='employee', status='pending', phone_number=data.get('phone'), company=company)
        return JsonResponse({'success': True, 'message': 'Registration successful. Waiting for HR approval.', 'user_id': user.id, 'employee_id': employee.id})
    except Exception as e:
        logger.error(f"Registration error: {str(e)}")
        return JsonResponse({'success': False, 'error': 'Registration failed'}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit(lambda r: f"login_{get_client_ip(r)}", limit=5, period=300)
def api_employee_login(request):
    try:
        data = json.loads(request.body)
        ip = get_client_ip(request)
        
        if cache.get(f"api_blocked_{ip}", False):
            return JsonResponse({'success': False, 'error': 'Too many failed attempts. Try again later.'}, status=429)
        
        email = data.get('email')
        password = data.get('password')
        
        if not email or not password:
            return JsonResponse({'success': False, 'error': 'Email and password are required'}, status=400)
        
        try:
            user_obj = User.objects.get(email=email)
        except User.DoesNotExist:
            user_obj = User.objects.filter(username=email).first()
            if not user_obj:
                fails = cache.get(f"api_login_fails_{ip}", 0) + 1
                cache.set(f"api_login_fails_{ip}", fails, 300)
                if fails >= 5:
                    cache.set(f"api_blocked_{ip}", True, 1800)
                    logger.warning(f"Failed mobile login attempt from IP: {ip}")
                    return JsonResponse({'success': False, 'error': 'Too many failed attempts. Account temporarily locked.'}, status=429)
                logger.warning(f"Failed mobile login attempt for email: {email} from IP: {ip}")
                return JsonResponse({'success': False, 'error': 'Invalid email or password'}, status=400)
        
        user = authenticate(request, username=user_obj.username, password=password)
        
        if not user:
            fails = cache.get(f"api_login_fails_{ip}", 0) + 1
            cache.set(f"api_login_fails_{ip}", fails, 300)
            if fails >= 5:
                cache.set(f"api_blocked_{ip}", True, 1800)
                logger.warning(f"Failed mobile login attempt from IP: {ip}")
                return JsonResponse({'success': False, 'error': 'Too many failed attempts. Account temporarily locked.'}, status=429)
            logger.warning(f"Failed mobile login attempt for email: {email} from IP: {ip}")
            return JsonResponse({'success': False, 'error': 'Invalid email or password'}, status=400)
        
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
        logger.info(f"Successful mobile login for user: {user.email} from IP: {ip}")
        
        return JsonResponse({
            'success': True,
            'access_token': str(refresh.access_token),
            'refresh_token': str(refresh),
            'user': {
                'id': user.id,
                'email': user.email,
                'name': employee.name if employee else user.username,
                'department': employee.department if employee else '',
                'company_id': employee.company.id if employee and employee.company else None,
                'company_name': employee.company.name if employee and employee.company else None,
            }
        })
    except Exception as e:
        logger.error(f"Login error: {str(e)}")
        return JsonResponse({'success': False, 'error': 'Login failed'}, status=500)


@api_view(['GET'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def api_employee_profile(request):
    try:
        employee = Employee.objects.filter(user=request.user).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        profile = Profile.objects.filter(user=request.user).first()
        return JsonResponse({
            'success': True,
            'profile': {
                'id': employee.id,
                'name': employee.name,
                'email': request.user.email,
                'department': employee.department,
                'company_id': employee.company.id if employee.company else None,
                'company_name': employee.company.name if employee.company else None,
                'phone': profile.phone_number if profile else '',
                'status': employee.status,
                'working_days': employee.company.working_days if employee.company else [1,2,3,4,5],
                'schedule_type': employee.company.schedule_type if employee.company else 'fixed'
            }
        })
    except Exception as e:
        logger.error(f"Profile error: {str(e)}")
        return JsonResponse({'success': False, 'error': 'Unable to fetch profile'}, status=500)


# ================= WORK SETTINGS API =================

@api_view(['GET', 'POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def api_work_settings(request):
    try:
        employee = Employee.objects.filter(user=request.user).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        
        if request.method == 'GET':
            return JsonResponse({
                'success': True,
                'settings': get_work_settings_dict(employee)
            })
        
        elif request.method == 'POST':
            profile = Profile.objects.filter(user=request.user, role='hr').first()
            if not profile or profile.role != 'hr':
                return JsonResponse({'success': False, 'error': 'HR access required'}, status=403)
            
            company = employee.company
            if not company:
                return JsonResponse({'success': False, 'error': 'Company not found'}, status=404)
            
            data = request.data
            
            if 'work_start' in data:
                company.fixed_start_time = datetime.strptime(data['work_start'], '%H:%M').time()
            if 'work_end' in data:
                company.fixed_end_time = datetime.strptime(data['work_end'], '%H:%M').time()
            if 'working_days' in data:
                company.working_days = data['working_days']
            if 'lunch_enabled' in data:
                company.lunch_enabled = data['lunch_enabled']
            if 'lunch_start' in data:
                company.lunch_start = datetime.strptime(data['lunch_start'], '%H:%M').time()
            if 'lunch_end' in data:
                company.lunch_end = datetime.strptime(data['lunch_end'], '%H:%M').time()
            if 'verification_min_interval' in data:
                company.verification_min_interval = data['verification_min_interval']
            if 'verification_max_interval' in data:
                company.verification_max_interval = data['verification_max_interval']
            if 'verification_window' in data:
                company.verification_window = data['verification_window']
            if 'beacon_grace_period' in data:
                company.beacon_grace_period = data['beacon_grace_period']
            if 'office_beacons' in data:
                company.office_beacon_uuids = data['office_beacons']
            if 'require_face_with_beacon' in data:
                company.require_face_with_beacon = data['require_face_with_beacon']
            if 'require_fingerprint_with_beacon' in data:
                company.require_fingerprint_with_beacon = data['require_fingerprint_with_beacon']
            
            company.save()
            
            log_activity(request.user, 'update', 'Company', company.id, company.name, 
                        "Work settings updated", get_client_ip(request))
            
            return JsonResponse({'success': True, 'message': 'Work settings updated successfully'})
        
    except Exception as e:
        logger.error(f"Work settings error: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ================= CHECK-IN STATUS API =================
@api_view(['GET'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def api_check_in_status(request):
    try:
        employee = Employee.objects.filter(user=request.user).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        
        today = timezone.now().date()
        attendance = Attendance.objects.filter(
            employee=employee, 
            date=today
        ).first()
        
        if attendance and attendance.check_in:
            return JsonResponse({
                'success': True,
                'is_checked_in': True,
                'check_in_time': attendance.check_in.strftime('%I:%M %p') if attendance.check_in else None,
                'attendance_id': attendance.id,
                'status': attendance.status,
                'message': 'Already checked in today'
            })
        else:
            return JsonResponse({
                'success': True,
                'is_checked_in': False,
                'message': 'Not checked in today'
            })
            
    except Exception as e:
        logger.error(f"Check-in status error: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ================= CHECK-IN API =================
@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
@transaction.atomic
@rate_limit(lambda r: f"checkin_{r.user.id}", limit=2, period=60)
@idempotent(key_func=lambda r: r.headers.get('X-Idempotency-Key'))
def api_check_in(request):
    try:
        data = json.loads(request.body)
        
        employee = Employee.objects.select_for_update().filter(user=request.user).first()
        if not employee:
            return JsonResponse({
                'success': False, 
                'error': 'Employee not found'
            }, status=404)
        
        today = timezone.now().date()
        existing_attendance = Attendance.objects.select_for_update().filter(
            employee=employee, 
            date=today
        ).first()
        
        if existing_attendance and existing_attendance.check_in:
            return JsonResponse({
                'success': True,
                'already_checked_in': True,
                'check_in_time': existing_attendance.check_in.strftime('%I:%M %p'),
                'attendance_id': existing_attendance.id,
                'status': existing_attendance.status,
                'message': f'Already checked in at {existing_attendance.check_in.strftime("%I:%M %p")}'
            })
        
        company_working_days = employee.company.working_days if employee.company else [1, 2, 3, 4, 5]
        today_weekday = timezone.now().weekday() + 1

        logger.info(f"🔍 Working Day Check - Today: {today_weekday}, Company Days: {company_working_days}")

        if employee.company and company_working_days and today_weekday not in company_working_days:
            logger.warning(f"❌ Today is not a working day! Today: {today_weekday}, Working days: {company_working_days}")
            return JsonResponse({
                'success': False, 
                'error': f'Today is not a working day.',
                'working_days': company_working_days,
                'today_weekday': today_weekday
            }, status=400)
        
        verification_method = 'manual'
        beacon_id = data.get('beacon_id')
        face_photo = data.get('face_photo')
        fingerprint_data = data.get('fingerprint_data')
        
        if beacon_id:
            logger.info(f"Beacon check-in from: {beacon_id} for employee: {employee.name}")
            
            company = employee.company
            if company:
                if company.require_face_with_beacon and not face_photo:
                    return JsonResponse({
                        'success': False, 
                        'error': 'Face verification required for beacon check-in'
                    }, status=400)
                
                if company.require_fingerprint_with_beacon and not fingerprint_data:
                    return JsonResponse({
                        'success': False, 
                        'error': 'Fingerprint verification required for beacon check-in'
                    }, status=400)
            
            if face_photo:
                face_result = verify_face(employee, face_photo)
                if not face_result['verified']:
                    return JsonResponse({
                        'success': False, 
                        'error': f"Face verification failed: {face_result['message']}"
                    }, status=400)
            
            if fingerprint_data:
                fingerprint_result = verify_fingerprint(employee, fingerprint_data)
                if not fingerprint_result['verified']:
                    return JsonResponse({
                        'success': False, 
                        'error': f"Fingerprint verification failed: {fingerprint_result['message']}"
                    }, status=400)
            
            verification_method = 'beacon'
        else:
            if not face_photo:
                return JsonResponse({
                    'success': False, 
                    'error': 'Face photo required for check-in'
                }, status=400)
            
            if not fingerprint_data:
                return JsonResponse({
                    'success': False, 
                    'error': 'Fingerprint required for check-in'
                }, status=400)
            
            face_result = verify_face(employee, face_photo)
            if not face_result['verified']:
                return JsonResponse({
                    'success': False, 
                    'error': f"Face verification failed: {face_result['message']}"
                }, status=400)
            
            fingerprint_result = verify_fingerprint(employee, fingerprint_data)
            if not fingerprint_result['verified']:
                return JsonResponse({
                    'success': False, 
                    'error': f"Fingerprint verification failed: {fingerprint_result['message']}"
                }, status=400)
            
            verification_method = 'face_fingerprint'
        
        check_in_time = timezone.now()
        schedule = get_employee_schedule(employee)
        shift_start = schedule['start_time']
        late_threshold = schedule['late_threshold']
        check_in_time_only = check_in_time.time()
        
        shift_start_datetime = datetime.combine(check_in_time.date(), shift_start)
        shift_start_datetime = timezone.make_aware(shift_start_datetime)
        
        minutes_late = int((check_in_time - shift_start_datetime).total_seconds() / 60)
        status = 'late' if minutes_late > late_threshold else 'present'
        
        attendance = Attendance.objects.create(
            employee=employee,
            date=check_in_time.date(),
            check_in=check_in_time_only,
            status=status,
            verification_method=verification_method,
            verified_count=1
        )
        
        random_verifications = schedule_random_verifications(employee, check_in_time)
        
        employee.check_in_time = check_in_time
        employee.status = 'present'
        employee.random_verify_count = 0
        employee.save()
        
        try:
            if PushNotificationService:
                PushNotificationService().send_attendance_alert(employee, 'check-in', status)
        except Exception as e:
            logger.error(f"Push notification error: {str(e)}")
        
        shift_info = get_shift_info_for_employee(employee)
        
        return JsonResponse({
            'success': True,
            'already_checked_in': False,
            'attendance_id': attendance.id,
            'message': f'Checked in at {check_in_time_only.strftime("%I:%M %p")}',
            'status': status,
            'schedule_type': schedule['type'],
            'shift_name': schedule['name'],
            'shift_start': shift_start.strftime('%I:%M %p'),
            'shift_end': schedule['end_time'].strftime('%I:%M %p'),
            'minutes_late': minutes_late if minutes_late > 0 else 0,
            'late_threshold_minutes': late_threshold,
            'random_verifications_scheduled': len(random_verifications),
            'next_verification': random_verifications[0].scheduled_time.strftime('%I:%M %p') if random_verifications else None,
            'shift_info': shift_info,
            'verification_method': verification_method,
            'working_days': company_working_days,
            'today_weekday': today_weekday
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False, 
            'error': 'Invalid JSON data'
        }, status=400)
    except Exception as e:
        logger.error(f"Check-in error: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False, 
            'error': 'Unable to process check-in. Please try again.'
        }, status=500)


# ================= CHECK-OUT API - ALWAYS ALLOW CHECKOUT =================
@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
@transaction.atomic
@rate_limit(lambda r: f"checkout_{r.user.id}", limit=3, period=60)
@idempotent(key_func=lambda r: r.headers.get('X-Idempotency-Key'))
def api_check_out(request):
    """
    Mobile API Check-Out - ALWAYS ALLOWS CHECKOUT regardless of verifications
    """
    try:
        data = json.loads(request.body)
        
        # ============================================================
        # 1. GET EMPLOYEE
        # ============================================================
        employee = Employee.objects.select_for_update().filter(user=request.user).first()
        if not employee:
            return JsonResponse({
                'success': False, 
                'error': 'Employee profile not found. Please contact HR.',
                'code': 'EMPLOYEE_NOT_FOUND'
            }, status=404)
        
        # ============================================================
        # 2. GET TODAY'S ATTENDANCE
        # ============================================================
        today = timezone.now().date()
        attendance = Attendance.objects.select_for_update().filter(
            employee=employee, 
            date=today
        ).first()
        
        # Check if employee has checked in
        if not attendance:
            return JsonResponse({
                'success': False,
                'error': 'No check-in record found for today. Please check in first.',
                'code': 'NO_CHECKIN_RECORD',
                'action_required': 'checkin'
            }, status=400)
        
        # Check if already checked out
        if attendance.check_out:
            return JsonResponse({
                'success': False,
                'error': f'Already checked out at {attendance.check_out.strftime("%I:%M %p")}',
                'code': 'ALREADY_CHECKED_OUT',
                'check_out_time': attendance.check_out.strftime('%I:%M %p')
            }, status=400)
        
        # ============================================================
        # 3. VALIDATE FACE PHOTO
        # ============================================================
        face_photo = data.get('face_photo')
        if not face_photo:
            return JsonResponse({
                'success': False,
                'error': 'Face photo required for checkout. Please capture your face.',
                'code': 'FACE_PHOTO_REQUIRED',
                'action_required': 'capture_face'
            }, status=400)
        
        # Validate image quality
        if len(face_photo) < 1000:  # Minimum size check
            return JsonResponse({
                'success': False,
                'error': 'Face photo is too small. Please capture a clearer photo with better lighting.',
                'code': 'FACE_IMAGE_TOO_SMALL',
                'action_required': 'recapture_face'
            }, status=400)
        
        # ============================================================
        # 4. VERIFY FACE
        # ============================================================
        face_result = verify_face(employee, face_photo)
        
        if not face_result.get('verified', False):
            error_message = face_result.get('message', 'Face verification failed')
            
            # Check if face not registered
            if face_result.get('need_re_register', False):
                return JsonResponse({
                    'success': False,
                    'error': 'Face not registered. Please register your face first.',
                    'code': 'FACE_NOT_REGISTERED',
                    'action_required': 'register_face',
                    'face_score': face_result.get('score', 0)
                }, status=400)
            
            # Check for multiple faces
            if 'Multiple faces' in error_message:
                return JsonResponse({
                    'success': False,
                    'error': 'Multiple faces detected. Please ensure only your face is visible.',
                    'code': 'MULTIPLE_FACES',
                    'action_required': 'retry_face'
                }, status=400)
            
            # Check for no face
            if 'No face detected' in error_message:
                return JsonResponse({
                    'success': False,
                    'error': 'No face detected. Please ensure good lighting and clear visibility.',
                    'code': 'NO_FACE_DETECTED',
                    'action_required': 'retry_face'
                }, status=400)
            
            # Generic face failure
            return JsonResponse({
                'success': False,
                'error': f'Face verification failed: {error_message}',
                'code': 'FACE_VERIFICATION_FAILED',
                'action_required': 'retry_face',
                'face_score': face_result.get('score', 0)
            }, status=400)
        
        # ============================================================
        # 5. VALIDATE FINGERPRINT
        # ============================================================
        fingerprint_data = data.get('fingerprint_data')
        if not fingerprint_data:
            return JsonResponse({
                'success': False,
                'error': 'Fingerprint required for checkout. Please scan your fingerprint.',
                'code': 'FINGERPRINT_REQUIRED',
                'action_required': 'scan_fingerprint'
            }, status=400)
        
        # ============================================================
        # 6. VERIFY FINGERPRINT
        # ============================================================
        fingerprint_result = verify_fingerprint(employee, fingerprint_data)
        
        if not fingerprint_result.get('verified', False):
            error_message = fingerprint_result.get('message', 'Fingerprint verification failed')
            
            # Check if fingerprint not registered
            if 'registered' in error_message.lower():
                return JsonResponse({
                    'success': False,
                    'error': 'Fingerprint not registered. Please register your fingerprint first.',
                    'code': 'FINGERPRINT_NOT_REGISTERED',
                    'action_required': 'register_fingerprint'
                }, status=400)
            
            # Generic fingerprint failure
            return JsonResponse({
                'success': False,
                'error': f'Fingerprint verification failed: {error_message}',
                'code': 'FINGERPRINT_VERIFICATION_FAILED',
                'action_required': 'retry_fingerprint'
            }, status=400)
        
        # ============================================================
        # 7. CHECK RANDOM VERIFICATIONS - WARNING ONLY, NO BLOCKING
        # ============================================================
        pending_count = RandomVerification.objects.filter(
            employee=employee,
            date=today,
            status='pending'
        ).count()
        
        missed_count = RandomVerification.objects.filter(
            employee=employee,
            date=today,
            status='missed'
        ).count()
        
        # Get details of pending verifications for logging
        pending_verifications = RandomVerification.objects.filter(
            employee=employee,
            date=today,
            status='pending'
        ).values('verification_type', 'scheduled_time')
        
        pending_details = [
            {
                'type': v['verification_type'],
                'scheduled_time': v['scheduled_time'].strftime('%I:%M %p')
            }
            for v in pending_verifications
        ]
        
        # LOG the verification issue but DO NOT BLOCK checkout
        if pending_count > 0 or missed_count > 0:
            log_activity(
                request.user,
                'checkout_with_verification_issues',
                'Attendance',
                attendance.id,
                employee.name,
                f"Checked out with {pending_count} pending and {missed_count} missed verifications",
                get_client_ip(request)
            )
            
            # Create notification for HR
            Notification.objects.create(
                user=employee.user,
                title="Verification Issue on Checkout",
                message=f"Employee {employee.name} checked out with {pending_count} pending and {missed_count} missed verifications.",
                notification_type='warning'
            )
            
            # Send SMS to admin if configured
            if ADMIN_PHONE:
                try:
                    send_sms(
                        f"ATTENTION: {employee.name} checked out with {pending_count} pending and {missed_count} missed verifications.",
                        ADMIN_PHONE
                    )
                except Exception as e:
                    logger.error(f"Admin SMS notification failed: {str(e)}")
        
        # Mark any pending verifications as missed (since employee is leaving)
        if pending_count > 0:
            RandomVerification.objects.filter(
                employee=employee,
                date=today,
                status='pending'
            ).update(status='missed')
        
        # ============================================================
        # 8. GET SHIFT INFORMATION
        # ============================================================
        schedule = get_employee_schedule(employee)
        check_out_time = timezone.now()
        check_out_time_only = check_out_time.time()
        
        shift_end = schedule['end_time']
        early_threshold = schedule['early_threshold']
        
        # Calculate early departure
        shift_end_datetime = datetime.combine(check_out_time.date(), shift_end)
        shift_end_datetime = timezone.make_aware(shift_end_datetime)
        
        minutes_early = int((shift_end_datetime - check_out_time).total_seconds() / 60)
        
        # ============================================================
        # 9. DETERMINE STATUS (SHIFT AWARE)
        # ============================================================
        status = attendance.status  # Keep current status initially
        
        # If leaving early, mark as early departure
        if minutes_early > early_threshold:
            status = 'early_departure'
        
        # Check if leaving too early (more than 4 hours early)
        if minutes_early > 240:  # 4 hours
            logger.warning(f"Employee {employee.name} checking out {minutes_early} minutes early")
        
        # ============================================================
        # 10. CHECK IF SHIFT HAS CHANGED (LATE SHIFT SUPPORT)
        # ============================================================
        # If employee is on night shift, check if checkout is after midnight
        if shift_end < time(12, 0):  # Shift ends before noon (night shift)
            # Allow checkout before 12 PM
            if check_out_time_only > time(12, 0):
                # Probably next day, but we handle it
                logger.info(f"Night shift checkout for {employee.name} at {check_out_time_only}")
        
        # ============================================================
        # 11. SAVE CHECKOUT
        # ============================================================
        attendance.check_out = check_out_time_only
        attendance.status = status
        attendance.verification_method = 'face_fingerprint'
        attendance.verified_count = F('verified_count') + 2
        attendance.shift_name = schedule['name']
        attendance.shift_start = schedule['start_time']
        attendance.shift_end = schedule['end_time']
        attendance.save()
        
        # Refresh attendance to get updated verified_count
        attendance.refresh_from_db()
        
        # Update employee status
        employee.check_out_time = check_out_time
        employee.status = 'absent'
        employee.save()
        
        # ============================================================
        # 12. SEND NOTIFICATIONS
        # ============================================================
        try:
            if PushNotificationService:
                PushNotificationService().send_attendance_alert(
                    employee, 
                    'check-out', 
                    status
                )
        except Exception as e:
            logger.error(f"Push notification error: {str(e)}")
        
        # ============================================================
        # 13. GET COMPLETED VERIFICATION COUNT
        # ============================================================
        completed_count = RandomVerification.objects.filter(
            employee=employee,
            date=today,
            status='completed'
        ).count()
        
        total_verifications = RandomVerification.objects.filter(
            employee=employee,
            date=today
        ).count()
        
        # ============================================================
        # 14. LOG ACTIVITY
        # ============================================================
        log_activity(
            request.user,
            'check_out',
            'Attendance',
            attendance.id,
            employee.name,
            f"Mobile checkout at {check_out_time_only.strftime('%I:%M %p')} - {status}",
            get_client_ip(request)
        )
        
        # ============================================================
        # 15. SUCCESS RESPONSE
        # ============================================================
        return JsonResponse({
            'success': True,
            'message': f'Checked out at {check_out_time_only.strftime("%I:%M %p")}',
            'code': 'CHECKOUT_SUCCESS',
            'data': {
                'attendance_id': attendance.id,
                'check_out_time': check_out_time_only.strftime('%I:%M %p'),
                'check_out_datetime': check_out_time.isoformat(),
                'status': status,
                'shift_name': schedule['name'],
                'shift_type': schedule['type'],
                'expected_end_time': shift_end.strftime('%I:%M %p'),
                'minutes_early': minutes_early if minutes_early > 0 else 0,
                'is_early_departure': minutes_early > early_threshold,
                'early_threshold': early_threshold,
                'total_verifications_today': attendance.verified_count,
                'random_verifications_completed': completed_count,
                'random_verifications_total': total_verifications,
                'all_verifications_completed': completed_count == total_verifications if total_verifications > 0 else True,
                'verification_method': 'face_fingerprint',
                'face_score': face_result.get('score', 0),
                'fingerprint_score': fingerprint_result.get('score', 98),
                'pending_verifications': pending_count,
                'missed_verifications': missed_count,
                'pending_verification_details': pending_details
            }
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON data. Please check your request format.',
            'code': 'INVALID_JSON'
        }, status=400)
    
    except Exception as e:
        logger.error(f"Check-out error: {str(e)}", exc_info=True)
        from django.conf import settings
        return JsonResponse({
            'success': False,
            'error': 'Unable to process checkout. Please try again or contact support.',
            'code': 'INTERNAL_ERROR',
            'details': str(e) if settings.DEBUG else None
        }, status=500)


# ================= RE-REGISTER FACE ENDPOINT =================
@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def api_re_register_face(request):
    try:
        data = json.loads(request.body)
        employee = Employee.objects.filter(user=request.user).first()
        
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        
        photo_base64 = data.get('face_photo')
        if not photo_base64:
            return JsonResponse({'success': False, 'error': 'Face photo required'}, status=400)
        
        employee.face_encoding = None
        employee.face_failures = 0
        employee.save()
        
        result = verify_face(employee, photo_base64)
        
        if result.get('verified'):
            return JsonResponse({
                'success': True,
                'message': 'Face re-registered successfully! Please try checking in again.',
                'score': result.get('score', 0)
            })
        else:
            return JsonResponse({
                'success': False,
                'error': result.get('message', 'Face registration failed'),
                'score': result.get('score', 0)
            }, status=400)
        
    except Exception as e:
        logger.error(f"Re-register face error: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ================= GET FACE STATUS =================
@api_view(['GET'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def api_face_status(request):
    try:
        employee = Employee.objects.filter(user=request.user).first()
        
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        
        has_face = bool(employee.face_encoding and employee.face_encoding != b'registered')
        face_failures = employee.face_failures or 0
        
        return JsonResponse({
            'success': True,
            'has_face': has_face,
            'face_failures': face_failures,
            'verification_count': employee.face_verification_count or 0,
            'last_verified': employee.last_face_verified.isoformat() if employee.last_face_verified else None,
            'needs_re_register': face_failures >= 3,
            'message': 'Face registered' if has_face else 'No face registered. Please register your face.'
        })
        
    except Exception as e:
        logger.error(f"Face status error: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ================= RANDOM VERIFICATION APIS =================

@api_view(['GET'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
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
        logger.error(f"Random verification check error: {str(e)}")
        return JsonResponse({'success': False, 'error': 'Unable to check verification'}, status=500)


@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
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
        logger.error(f"Submit verification error: {str(e)}")
        return JsonResponse({'success': False, 'error': 'Unable to submit verification'}, status=500)


@api_view(['GET'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
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
        logger.error(f"Verification status error: {str(e)}")
        return JsonResponse({'success': False, 'error': 'Unable to get verification status'}, status=500)


# ================= OTHER MOBILE APIs =================

@api_view(['GET'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
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
        logger.error(f"Attendance history error: {str(e)}")
        return JsonResponse({'success': False, 'error': 'Unable to fetch attendance history'}, status=500)


@api_view(['GET'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
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
        logger.error(f"Today attendance error: {str(e)}")
        return JsonResponse({'success': False, 'error': 'Unable to fetch today\'s attendance'}, status=500)


@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
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
        logger.error(f"Apply leave error: {str(e)}")
        return JsonResponse({'success': False, 'error': 'Unable to apply for leave'}, status=500)


@api_view(['GET'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
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
        logger.error(f"Leave history error: {str(e)}")
        return JsonResponse({'success': False, 'error': 'Unable to fetch leave history'}, status=500)


@api_view(['GET'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
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
        logger.error(f"Leave balance error: {str(e)}")
        return JsonResponse({'success': False, 'error': 'Unable to fetch leave balance'}, status=500)


@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
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
        logger.error(f"Cancel leave error: {str(e)}")
        return JsonResponse({'success': False, 'error': 'Unable to cancel leave'}, status=500)


@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
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
        logger.error(f"Sync attendance error: {str(e)}")
        return JsonResponse({'success': False, 'error': 'Unable to sync attendance'}, status=500)


@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
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
        logger.error(f"Sync leaves error: {str(e)}")
        return JsonResponse({'success': False, 'error': 'Unable to sync leaves'}, status=500)


# ================= BEACONS API =================
@api_view(['GET'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def api_get_beacons(request):
    try:
        employee = Employee.objects.filter(user=request.user).first()
        if not employee or not employee.company:
            return JsonResponse({
                'success': True,
                'beacons': [],
                'message': 'No company associated with your account'
            })
        
        company = employee.company
        beacons = company.office_beacon_uuids or []
        
        if beacons:
            beacon_list = []
            for beacon_id in beacons:
                beacon_id = beacon_id.strip()
                if beacon_id:
                    beacon_list.append({
                        'id': beacon_id,
                        'name': beacon_id.replace('_', ' ').title(),
                        'rssi_threshold': -70
                    })
            return JsonResponse({
                'success': True,
                'beacons': beacon_list,
                'count': len(beacon_list)
            })
        else:
            return JsonResponse({
                'success': True,
                'beacons': [],
                'message': 'No beacons configured for your company'
            })
            
    except Exception as e:
        logger.error(f"Get beacons error: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': 'Unable to fetch beacons'
        }, status=500)


# ================= DASHBOARD STATS =================

@api_view(['GET'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def api_dashboard_stats(request):
    try:
        employee = Employee.objects.filter(user=request.user).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        
        today = timezone.now().date()
        start_of_month = today.replace(day=1)
        company_working_days = employee.company.working_days if employee.company else [1, 2, 3, 4, 5]
        schedule_type = employee.company.schedule_type if employee.company else 'fixed'
        
        working_days = 0
        current = start_of_month
        while current <= today:
            weekday = current.weekday() + 1
            if weekday in company_working_days:
                working_days += 1
            current += timedelta(days=1)
        
        this_month_attendance = Attendance.objects.filter(
            employee=employee,
            date__gte=start_of_month,
            date__lte=today
        )
        
        present_days = this_month_attendance.filter(status='present').count()
        late_days = this_month_attendance.filter(status='late').count()
        early_departures = this_month_attendance.filter(status='early_departure').count()
        leave_days = Leave.objects.filter(
            employee=employee,
            status='approved',
            requested_at__date__gte=start_of_month,
            requested_at__date__lte=today
        ).count()
        
        total_present = present_days + late_days + early_departures
        absent_days = max(0, working_days - total_present - leave_days)
        attendance_percentage = round((total_present / working_days) * 100, 1) if working_days > 0 else 0.0
        
        today_attendance = Attendance.objects.filter(
            employee=employee,
            date=today
        ).first()
        
        present_today = 1 if today_attendance and today_attendance.check_in else 0
        absent_today = 1 if not today_attendance or not today_attendance.check_in else 0
        
        today_weekday = today.weekday() + 1
        today_is_working = today_weekday in company_working_days
        
        recent_activities = []
        recent_attendance = Attendance.objects.filter(
            employee=employee
        ).order_by('-date', '-check_in')[:5]
        
        for att in recent_attendance:
            activity = {
                'title': 'Check In' if att.check_in else 'Check Out',
                'time': att.date.strftime('%Y-%m-%d %H:%M'),
                'status': att.status.title() if att.status else 'Completed',
                'type': 'checkin' if att.check_in else 'checkout'
            }
            recent_activities.append(activity)
        
        return JsonResponse({
            'success': True,
            'stats': {
                'working_days': working_days,
                'present_days': present_days,
                'late_days': late_days,
                'early_departures': early_departures,
                'leave_days': leave_days,
                'absent_days': absent_days,
                'attendance_percentage': attendance_percentage,
                'present_today': present_today,
                'absent_today': absent_today,
                'late_today': 1 if today_attendance and today_attendance.status == 'late' else 0,
                'on_leave_today': 0,
                'today_is_working': today_is_working,
                'schedule_type': schedule_type,
                'working_days_list': company_working_days,
                'total_employees': Employee.objects.filter(company=employee.company).count() if employee.company else 0,
                'recent_activities': recent_activities,
                'last_updated': timezone.now().isoformat()
            }
        })
        
    except Exception as e:
        logger.error(f"Dashboard stats error: {str(e)}")
        return JsonResponse({'success': False, 'error': 'Unable to fetch dashboard stats'}, status=500)


@api_view(['GET'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
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
        
        company_working_days = employee.company.working_days if employee.company else [1, 2, 3, 4, 5]
        
        total_working_days = 0
        current = start_of_month
        while current < end_of_month:
            weekday = current.weekday() + 1
            if weekday in company_working_days:
                total_working_days += 1
            current += timedelta(days=1)
        
        attendances = Attendance.objects.filter(
            employee=employee,
            date__gte=start_of_month,
            date__lt=end_of_month
        )
        
        present_days = attendances.filter(status='present').count()
        late_days = attendances.filter(status='late').count()
        early_departures = attendances.filter(status='early_departure').count()
        leaves_taken = Leave.objects.filter(
            employee=employee,
            status='approved',
            requested_at__date__gte=start_of_month,
            requested_at__date__lt=end_of_month
        ).count()
        
        total_present = present_days + late_days + early_departures
        absent_days = max(0, total_working_days - total_present - leaves_taken)
        attendance_percentage = round((total_present / total_working_days) * 100, 1) if total_working_days > 0 else 0
        
        return JsonResponse({
            'success': True,
            'stats': {
                'total_days': total_working_days,
                'present_days': present_days,
                'late_days': late_days,
                'early_departures': early_departures,
                'leaves_taken': leaves_taken,
                'absent_days': absent_days,
                'attendance_percentage': attendance_percentage,
                'working_days_list': company_working_days,
                'month': start_of_month.strftime('%B %Y'),
            }
        })
    except Exception as e:
        logger.error(f"Monthly stats error: {str(e)}")
        return JsonResponse({'success': False, 'error': 'Unable to fetch monthly stats'}, status=500)


# ================= SHIFT MANAGEMENT APIS =================

@csrf_exempt
@require_http_methods(["POST"])
def api_assign_employee_shift(request):
    try:
        data = json.loads(request.body)
        employee_id = data.get('employee_id')
        shift_id = data.get('shift_id')
        
        profile = Profile.objects.filter(user=request.user, role='hr').first()
        if not profile:
            return JsonResponse({'success': False, 'error': 'HR access required'}, status=403)
        
        employee = get_object_or_404(Employee, id=employee_id, company=profile.company)
        shift = get_object_or_404(Shift, id=shift_id, company=profile.company)
        
        employee.assigned_shift = shift
        employee.save()
        
        return JsonResponse({'success': True, 'message': f'{employee.name} assigned to {shift.name}'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def api_unassign_employee_shift(request):
    try:
        data = json.loads(request.body)
        employee_id = data.get('employee_id')
        
        profile = Profile.objects.filter(user=request.user, role='hr').first()
        if not profile:
            return JsonResponse({'success': False, 'error': 'HR access required'}, status=403)
        
        employee = get_object_or_404(Employee, id=employee_id, company=profile.company)
        employee.assigned_shift = None
        employee.save()
        
        return JsonResponse({'success': True, 'message': f'{employee.name} moved to Fixed Schedule'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def api_shift_unassign_all(request):
    try:
        data = json.loads(request.body)
        shift_id = data.get('shift_id')
        
        profile = Profile.objects.filter(user=request.user, role='hr').first()
        if not profile:
            return JsonResponse({'success': False, 'error': 'HR access required'}, status=403)
        
        shift = get_object_or_404(Shift, id=shift_id, company=profile.company)
        Employee.objects.filter(assigned_shift=shift).update(assigned_shift=None)
        
        return JsonResponse({'success': True, 'message': f'All employees removed from {shift.name}'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def api_shift_bulk_assign(request):
    try:
        data = json.loads(request.body)
        shift_id = data.get('shift_id')
        employee_ids = data.get('employee_ids', [])
        
        profile = Profile.objects.filter(user=request.user, role='hr').first()
        if not profile:
            return JsonResponse({'success': False, 'error': 'HR access required'}, status=403)
        
        shift = get_object_or_404(Shift, id=shift_id, company=profile.company)
        assigned_count = Employee.objects.filter(id__in=employee_ids, company=profile.company).update(assigned_shift=shift)
        
        return JsonResponse({'success': True, 'assigned_count': assigned_count})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ================= SOCIAL AUTH CONFIGURATION =================
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "https://hr-attendance-system-gojk.onrender.com/users/auth/callback/google/")

APPLE_CLIENT_ID = os.environ.get("APPLE_CLIENT_ID", "")
APPLE_CLIENT_SECRET = os.environ.get("APPLE_CLIENT_SECRET", "")
APPLE_REDIRECT_URI = os.environ.get("APPLE_REDIRECT_URI", "https://hr-attendance-system-gojk.onrender.com/users/auth/callback/apple/")


# ================= API SOCIAL AUTH VIEWS =================

@csrf_exempt
@require_http_methods(["POST"])
def api_google_auth(request):
    try:
        params = {
            'client_id': GOOGLE_CLIENT_ID,
            'redirect_uri': GOOGLE_REDIRECT_URI,
            'response_type': 'code',
            'scope': 'email profile',
        }
        auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
        return JsonResponse({'success': True, 'auth_url': auth_url})
    except Exception as e:
        logger.error(f"Google auth API error: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def api_apple_auth(request):
    try:
        params = {
            'client_id': APPLE_CLIENT_ID,
            'redirect_uri': APPLE_REDIRECT_URI,
            'response_type': 'code',
            'scope': 'name email',
            'response_mode': 'form_post'
        }
        auth_url = f"https://appleid.apple.com/auth/authorize?{urlencode(params)}"
        return JsonResponse({'success': True, 'auth_url': auth_url})
    except Exception as e:
        logger.error(f"Apple auth API error: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def api_complete_social_profile(request):
    try:
        data = json.loads(request.body)
        
        social_email = data.get('social_email')
        social_provider = data.get('social_provider')
        company_name = data.get('company_name')
        username = data.get('username')
        phone = data.get('phone')
        
        if not all([social_email, company_name, username, phone]):
            return JsonResponse({'success': False, 'error': 'All fields are required'}, status=400)
        
        if User.objects.filter(username=username).exists():
            return JsonResponse({'success': False, 'error': 'Username already exists'}, status=400)
        
        if not re.match(r'^\+?[0-9]{10,15}$', phone):
            return JsonResponse({'success': False, 'error': 'Invalid phone number format'}, status=400)
        
        if User.objects.filter(email=social_email).exists():
            return JsonResponse({'success': False, 'error': 'Email already registered'}, status=400)
        
        with transaction.atomic():
            user = User.objects.create_user(
                username=username,
                email=social_email,
                password=None
            )
            
            company = Company.objects.create(
                name=company_name,
                status='pending',
                requested_by=user
            )
            
            Profile.objects.create(
                user=user,
                role="hr",
                status="pending",
                phone_number=phone,
                company=company
            )
            
            refresh = RefreshToken.for_user(user)
            
            return JsonResponse({
                'success': True,
                'message': 'Registration successful. Waiting for admin approval.',
                'access_token': str(refresh.access_token),
                'refresh_token': str(refresh),
                'user_id': user.id
            })
            
    except Exception as e:
        logger.error(f"Complete social profile error: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ================= WEB SOCIAL AUTH VIEWS =================

def google_auth(request):
    try:
        params = {
            'client_id': GOOGLE_CLIENT_ID,
            'redirect_uri': GOOGLE_REDIRECT_URI,
            'response_type': 'code',
            'scope': 'email profile',
            'access_type': 'offline',
            'prompt': 'consent'
        }
        auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
        return redirect(auth_url)
    except Exception as e:
        logger.error(f"Google auth error: {str(e)}")
        messages.error(request, "Unable to initiate Google login. Please try again.")
        return redirect('users:hr_register')


def google_auth_callback(request):
    try:
        code = request.GET.get('code')
        if not code:
            messages.error(request, "Google authentication failed.")
            return redirect('users:hr_register')
        
        token_url = "https://oauth2.googleapis.com/token"
        token_data = {
            'code': code,
            'client_id': GOOGLE_CLIENT_ID,
            'client_secret': GOOGLE_CLIENT_SECRET,
            'redirect_uri': GOOGLE_REDIRECT_URI,
            'grant_type': 'authorization_code'
        }
        
        token_response = requests.post(token_url, data=token_data)
        token_json = token_response.json()
        
        if 'access_token' not in token_json:
            messages.error(request, "Failed to get access token from Google.")
            return redirect('users:hr_register')
        
        user_info_url = "https://www.googleapis.com/oauth2/v2/userinfo"
        headers = {'Authorization': f"Bearer {token_json['access_token']}"}
        user_response = requests.get(user_info_url, headers=headers)
        user_info = user_response.json()
        
        email = user_info.get('email')
        name = user_info.get('name', email.split('@')[0])
        
        if not email:
            messages.error(request, "Could not retrieve email from Google.")
            return redirect('users:hr_register')
        
        user = User.objects.filter(email=email).first()
        
        if user:
            profile = Profile.objects.filter(user=user, role='hr').first()
            if profile:
                if profile.status == 'approved':
                    login(request, user)
                    return redirect('users:hr_dashboard')
                elif profile.status == 'pending':
                    messages.warning(request, "Your HR account is pending approval.")
                    return redirect('users:hr_login')
                else:
                    messages.error(request, "Your account has been rejected.")
                    return redirect('users:hr_register')
            else:
                request.session['social_email'] = email
                request.session['social_name'] = name
                request.session['social_provider'] = 'google'
                return redirect('users:complete_company_profile')
        else:
            request.session['social_email'] = email
            request.session['social_name'] = name
            request.session['social_provider'] = 'google'
            return redirect('users:complete_company_profile')
            
    except Exception as e:
        logger.error(f"Google callback error: {str(e)}")
        messages.error(request, f"Google authentication failed: {str(e)}")
        return redirect('users:hr_register')


def apple_auth(request):
    try:
        params = {
            'client_id': APPLE_CLIENT_ID,
            'redirect_uri': APPLE_REDIRECT_URI,
            'response_type': 'code',
            'scope': 'name email',
            'response_mode': 'form_post'
        }
        auth_url = f"https://appleid.apple.com/auth/authorize?{urlencode(params)}"
        return redirect(auth_url)
    except Exception as e:
        logger.error(f"Apple auth error: {str(e)}")
        messages.error(request, "Unable to initiate Apple login. Please try again.")
        return redirect('users:hr_register')


@csrf_exempt
def apple_auth_callback(request):
    try:
        if request.method == 'POST':
            code = request.POST.get('code')
            user_email = request.POST.get('email', '')
            user_name = request.POST.get('name', '')
        else:
            code = request.GET.get('code')
            user_email = request.GET.get('email', '')
            user_name = request.GET.get('name', '')
        
        if not code:
            messages.error(request, "Apple authentication failed.")
            return redirect('users:hr_register')
        
        email = user_email
        name = user_name if user_name else email.split('@')[0] if email else 'user'
        
        if not email:
            messages.error(request, "Could not retrieve email from Apple.")
            return redirect('users:hr_register')
        
        user = User.objects.filter(email=email).first()
        
        if user:
            profile = Profile.objects.filter(user=user, role='hr').first()
            if profile and profile.status == 'approved':
                login(request, user)
                return redirect('users:hr_dashboard')
            elif profile and profile.status == 'pending':
                messages.warning(request, "Your HR account is pending approval.")
                return redirect('users:hr_login')
            else:
                request.session['social_email'] = email
                request.session['social_name'] = name
                request.session['social_provider'] = 'apple'
                return redirect('users:complete_company_profile')
        else:
            request.session['social_email'] = email
            request.session['social_name'] = name
            request.session['social_provider'] = 'apple'
            return redirect('users:complete_company_profile')
            
    except Exception as e:
        logger.error(f"Apple callback error: {str(e)}")
        messages.error(request, f"Apple authentication failed: {str(e)}")
        return redirect('users:hr_register')


def complete_company_profile(request):
    social_email = request.session.get('social_email')
    social_name = request.session.get('social_name')
    social_provider = request.session.get('social_provider')
    
    if not social_email:
        messages.error(request, "Please register using Google or Apple first.")
        return redirect('users:hr_register')
    
    if request.method == "POST":
        company_name = request.POST.get("company_name")
        username = request.POST.get("username")
        phone = request.POST.get("phone")
        
        if not company_name or not username or not phone:
            messages.error(request, "All fields are required.")
            return render(request, "users/complete_profile.html", {
                'social_email': social_email,
                'social_name': social_name,
                'social_provider': social_provider
            })
        
        if User.objects.filter(username=username).exists():
            messages.error(request, f"Username '{username}' is already taken.")
            return render(request, "users/complete_profile.html", {
                'social_email': social_email,
                'social_name': social_name,
                'social_provider': social_provider
            })
        
        if not re.match(r'^\+?[0-9]{10,15}$', phone):
            messages.error(request, "Invalid phone number format. Use +255XXXXXXXXX")
            return render(request, "users/complete_profile.html", {
                'social_email': social_email,
                'social_name': social_name,
                'social_provider': social_provider
            })
        
        if User.objects.filter(email=social_email).exists():
            messages.error(request, "Email already registered.")
            return render(request, "users/complete_profile.html", {
                'social_email': social_email,
                'social_name': social_name,
                'social_provider': social_provider
            })
        
        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    username=username,
                    email=social_email,
                    password=None
                )
                
                company = Company.objects.create(
                    name=company_name,
                    status='pending',
                    requested_by=user,
                    working_days=[1, 2, 3, 4, 5]
                )
                
                Profile.objects.create(
                    user=user,
                    role="hr",
                    status="pending",
                    phone_number=phone,
                    company=company
                )
                
                del request.session['social_email']
                del request.session['social_name']
                del request.session['social_provider']
                
                messages.success(request, f"Registration successful via {social_provider.title()}! Waiting for admin approval.")
                return redirect("users:hr_login")
                
        except IntegrityError as e:
            messages.error(request, f"Registration failed: {str(e)}")
            return render(request, "users/complete_profile.html", {
                'social_email': social_email,
                'social_name': social_name,
                'social_provider': social_provider
            })
    
    return render(request, "users/complete_profile.html", {
        'social_email': social_email,
        'social_name': social_name,
        'social_provider': social_provider
    })


# ================= HEALTH CHECK =================
def health_check(request):
    return JsonResponse({
        'status': 'ok',
        'timestamp': timezone.now().isoformat(),
        'database': 'connected',
        'version': '1.0.0'
    })


# ================= TEST FACE ENDPOINT =================
@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def api_test_face_verification(request):
    try:
        data = json.loads(request.body)
        employee = Employee.objects.filter(user=request.user).first()
        
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        
        photo_base64 = data.get('face_photo')
        if not photo_base64:
            return JsonResponse({'success': False, 'error': 'Face photo required'}, status=400)
        
        result = verify_face(employee, photo_base64)
        
        return JsonResponse({
            'success': True,
            'result': result,
            'employee_has_face': bool(employee.face_encoding),
            'employee_face_verifications': employee.face_verification_count
        })
        
    except Exception as e:
        logger.error(f"Test face verification error: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ===== OFFLINE SYNC ENDPOINTS =====
@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def api_sync_all_data(request):
    try:
        employee = Employee.objects.filter(user=request.user).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        
        start_date = timezone.now().date() - timedelta(days=30)
        attendance = Attendance.objects.filter(
            employee=employee, 
            date__gte=start_date
        ).values('date', 'check_in', 'check_out', 'status')
        
        pending_leaves = Leave.objects.filter(
            employee=employee, 
            status='pending'
        ).values('id', 'leave_type', 'reason', 'requested_at')
        
        work_settings = get_work_settings_dict(employee)
        company = employee.company
        beacons = company.office_beacon_uuids if company else []
        
        return JsonResponse({
            'success': True,
            'data': {
                'attendance': list(attendance),
                'pending_leaves': list(pending_leaves),
                'work_settings': work_settings,
                'beacons': beacons,
                'employee': {
                    'id': employee.id,
                    'name': employee.name,
                    'email': employee.email,
                    'department': employee.department,
                    'working_days': company.working_days if company else [1, 2, 3, 4, 5],
                    'schedule_type': company.schedule_type if company else 'fixed'
                },
                'last_sync': timezone.now().isoformat()
            }
        })
        
    except Exception as e:
        logger.error(f"Sync error: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def api_sync_offline_data(request):
    try:
        data = json.loads(request.body)
        employee = Employee.objects.filter(user=request.user).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        
        synced_count = 0
        
        offline_attendance = data.get('attendance', [])
        for record in offline_attendance:
            date_obj = datetime.strptime(record['date'], '%Y-%m-%d').date()
            if not Attendance.objects.filter(employee=employee, date=date_obj).exists():
                Attendance.objects.create(
                    employee=employee,
                    date=date_obj,
                    check_in=datetime.strptime(record['check_in'], '%H:%M:%S').time() if record.get('check_in') else None,
                    check_out=datetime.strptime(record['check_out'], '%H:%M:%S').time() if record.get('check_out') else None,
                    status=record.get('status', 'present')
                )
                synced_count += 1
        
        offline_leaves = data.get('leaves', [])
        for leave_data in offline_leaves:
            Leave.objects.create(
                employee=employee,
                leave_type=leave_data['leave_type'],
                reason=leave_data.get('reason', ''),
                status='pending'
            )
        
        return JsonResponse({
            'success': True,
            'synced_attendance': synced_count,
            'synced_leaves': len(offline_leaves),
            'message': f'Synced {synced_count} attendance records'
        })
        
    except Exception as e:
        logger.error(f"Offline sync error: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ===== 2FA ENDPOINTS =====
@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def api_setup_2fa(request):
    try:
        employee = Employee.objects.filter(user=request.user).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        
        secret = TwoFactorService.generate_secret()
        employee.two_factor_secret = secret
        employee.save()
        
        qr_data = TwoFactorService.generate_qr_code(employee, secret)
        
        if not qr_data:
            return JsonResponse({'success': False, 'error': 'Failed to generate QR code'}, status=500)
        
        return JsonResponse({
            'success': True,
            'secret': secret,
            'qr_code': qr_data['qr_code']
        })
        
    except Exception as e:
        logger.error(f"2FA setup error: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def api_enable_2fa(request):
    try:
        data = json.loads(request.body)
        employee = Employee.objects.filter(user=request.user).first()
        if not employee:
            return JsonResponse({'success': False, 'error': 'Employee not found'}, status=404)
        
        otp_code = data.get('otp_code')
        if not otp_code:
            return JsonResponse({'success': False, 'error': 'OTP code required'}, status=400)
        
        if not TwoFactorService.verify_otp(employee.two_factor_secret, otp_code):
            return JsonResponse({'success': False, 'error': 'Invalid OTP code'}, status=400)
        
        employee.two_factor_enabled = True
        employee.save()
        
        return JsonResponse({'success': True, 'message': '2FA enabled successfully'})
        
    except Exception as e:
        logger.error(f"Enable 2FA error: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@api_view(['POST'])
def api_verify_2fa_login(request):
    try:
        data = json.loads(request.body)
        employee_id = data.get('employee_id')
        otp_code = data.get('otp_code')
        
        if not employee_id or not otp_code:
            return JsonResponse({'success': False, 'error': 'Employee ID and OTP required'}, status=400)
        
        employee = get_object_or_404(Employee, id=employee_id)
        
        if not employee.two_factor_enabled:
            return JsonResponse({'success': False, 'error': '2FA not enabled'}, status=400)
        
        if TwoFactorService.verify_otp(employee.two_factor_secret, otp_code):
            user = employee.user
            refresh = RefreshToken.for_user(user)
            return JsonResponse({
                'success': True,
                'access_token': str(refresh.access_token),
                'refresh_token': str(refresh),
                'message': '2FA verified successfully'
            })
        else:
            return JsonResponse({'success': False, 'error': 'Invalid OTP code'}, status=400)
            
    except Exception as e:
        logger.error(f"2FA verify error: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)