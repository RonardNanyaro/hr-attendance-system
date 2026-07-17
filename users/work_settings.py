# users/work_settings.py
from datetime import datetime, time
from django.utils import timezone

def is_work_time(employee, check_time=None):
    """Check if current time is within work hours (excluding lunch)"""
    if check_time is None:
        check_time = timezone.now()
    
    company = employee.company
    if not company:
        return False
    
    # Check if working day
    weekday = check_time.weekday() + 1
    if weekday not in company.working_days:
        return False
    
    # Check if within work hours
    work_start = company.fixed_start_time or time(9, 0)
    work_end = company.fixed_end_time or time(17, 0)
    
    current_time = check_time.time()
    
    if current_time < work_start or current_time > work_end:
        return False
    
    # Check if lunch break
    if company.lunch_enabled:
        lunch_start = company.lunch_start or time(12, 0)
        lunch_end = company.lunch_end or time(13, 0)
        if lunch_start <= current_time <= lunch_end:
            return False
    
    return True

def is_lunch_time(employee, check_time=None):
    """Check if current time is within lunch break"""
    if check_time is None:
        check_time = timezone.now()
    
    company = employee.company
    if not company or not company.lunch_enabled:
        return False
    
    lunch_start = company.lunch_start or time(12, 0)
    lunch_end = company.lunch_end or time(13, 0)
    current_time = check_time.time()
    
    return lunch_start <= current_time <= lunch_end

def get_next_verification_interval(employee):
    """Get random interval for next verification"""
    import random
    company = employee.company
    min_interval = company.verification_min_interval if company else 30
    max_interval = company.verification_max_interval if company else 90
    return random.randint(min_interval, max_interval) * 60

def get_work_settings_dict(employee):
    """Get work settings as dictionary for API response"""
    company = employee.company
    return {
        'work_start': company.fixed_start_time.strftime('%H:%M') if company.fixed_start_time else '09:00',
        'work_end': company.fixed_end_time.strftime('%H:%M') if company.fixed_end_time else '17:00',
        'working_days': company.working_days,
        'lunch_enabled': company.lunch_enabled,
        'lunch_start': company.lunch_start.strftime('%H:%M') if company.lunch_start else '12:00',
        'lunch_end': company.lunch_end.strftime('%H:%M') if company.lunch_end else '13:00',
        'verification_min_interval': company.verification_min_interval,
        'verification_max_interval': company.verification_max_interval,
        'verification_window': company.verification_window,
        'beacon_grace_period': company.beacon_grace_period,
        'office_beacons': company.office_beacon_uuids,
    }