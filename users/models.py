from django.db import models
from django.contrib.auth.models import User
from datetime import time, timedelta
from django.utils import timezone as django_timezone


# ================= COMPANY =================
class Company(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    )
    
    SCHEDULE_TYPE_CHOICES = [
        ('fixed', 'Fixed Schedule - All employees same time'),
        ('shifts', 'Shift Schedule - Different employees different times'),
    ]
    
    name = models.CharField(max_length=200)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    
    requested_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='requested_companies')
    requested_at = models.DateTimeField(auto_now_add=True)
    
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,  related_name='approved_companies')
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, null=True)
    
    phone = models.CharField(max_length=20, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    company_code = models.CharField(max_length=20, blank=True, null=True, unique=True)
    
    schedule_type = models.CharField(max_length=10, choices=SCHEDULE_TYPE_CHOICES, default='fixed')
    
    fixed_start_time = models.TimeField(null=True, blank=True, default=time(9, 0))
    fixed_end_time = models.TimeField(null=True, blank=True, default=time(17, 0))
    fixed_late_threshold = models.IntegerField(default=15)
    fixed_early_departure_threshold = models.IntegerField(default=15)
    
    working_days = models.JSONField(default=list)
    
    # ========== LUNCH SETTINGS ==========
    lunch_enabled = models.BooleanField(default=True)
    lunch_start = models.TimeField(null=True, blank=True, default=time(12, 0))
    lunch_end = models.TimeField(null=True, blank=True, default=time(13, 0))
    
    # ========== VERIFICATION SETTINGS ==========
    verification_min_interval = models.IntegerField(default=30)
    verification_max_interval = models.IntegerField(default=90)
    verification_window = models.IntegerField(default=5)
    beacon_grace_period = models.IntegerField(default=2)
    
    # ========== BEACON WHITELIST ==========
    def get_default_beacon():return ["E2C56DB5-DFFB-48D2-B060-D0F5A71096E0"]

    office_beacon_uuids = models.JSONField(
    default=get_default_beacon,
    blank=True
    )
    
    # ========== BIOMETRIC RULES ==========
    require_face_with_beacon = models.BooleanField(default=True)
    require_fingerprint_with_beacon = models.BooleanField(default=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.name} ({self.status})"
    
    def get_working_days_display(self):
        days_map = {1: 'Mon', 2: 'Tue', 3: 'Wed', 4: 'Thu', 5: 'Fri', 6: 'Sat', 7: 'Sun'}
        return ', '.join([days_map.get(d, '') for d in self.working_days])
    
    class Meta:
        verbose_name_plural = "Companies"
        ordering = ['-requested_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['company_code']),
            models.Index(fields=['name']),
        ]


# ================= DEPARTMENT =================
class Department(models.Model):
    name = models.CharField(max_length=100)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='departments')
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_departments')
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.name} - {self.company.name}"
    
    class Meta:
        unique_together = ['name', 'company']
        ordering = ['name']
        indexes = [
            models.Index(fields=['company', 'name']),
        ]


# ================= SHIFT =================
class Shift(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='shifts')
    name = models.CharField(max_length=50)
    start_time = models.TimeField()
    end_time = models.TimeField()
    late_threshold = models.IntegerField(default=15)
    early_departure_threshold = models.IntegerField(default=15)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.company.name} - {self.name}"
    
    class Meta:
        unique_together = ['company', 'name']
        ordering = ['start_time']
        indexes = [
            models.Index(fields=['company', 'is_active']),
        ]


# ================= PROFILE =================
class Profile(models.Model):
    ROLE_CHOICES = (
        ('admin', 'Admin'),
        ('hr', 'HR'),
        ('employee', 'Employee'),
    )

    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    )

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    phone_number = models.CharField(max_length=15, blank=True, null=True)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, null=True, blank=True, related_name='profiles')

    def __str__(self):
        return f"{self.user.username} ({self.role})"
    
    class Meta:
        indexes = [
            models.Index(fields=['role', 'status']),
            models.Index(fields=['company', 'role']),
            models.Index(fields=['user', 'role']),
        ]


# ================= EMPLOYEE =================
class Employee(models.Model):
    STATUS_CHOICES = (
        ('present', 'Present'),
        ('absent', 'Absent'),
        ('late', 'Late'),
        ('leave', 'On Leave'),
    )

    user = models.OneToOneField(User, on_delete=models.SET_NULL, null=True, blank=True)
    name = models.CharField(max_length=100)
    department = models.CharField(max_length=50, blank=True, null=True)
    
    company = models.ForeignKey(Company, on_delete=models.CASCADE, null=True, blank=True, related_name='employees')
    department_obj = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name='employees')
    assigned_shift = models.ForeignKey(Shift, on_delete=models.SET_NULL, null=True, blank=True, related_name='employees')
    
    check_in_time = models.DateTimeField(null=True, blank=True)
    check_out_time = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='absent')
    
    random_verify_count = models.IntegerField(default=0)
    last_random_verify_time = models.DateTimeField(null=True, blank=True)
    
    # ========== FACE RECOGNITION FIELDS ==========
    face_encoding = models.BinaryField(null=True, blank=True)
    face_registered_at = models.DateTimeField(null=True, blank=True)
    face_verification_count = models.IntegerField(default=0)
    face_failures = models.IntegerField(default=0)
    last_face_verified = models.DateTimeField(null=True, blank=True)
    
    # ========== FINGERPRINT FIELDS ==========
    fingerprint_hash = models.CharField(max_length=128, null=True, blank=True)
    fingerprint_verification_count = models.IntegerField(default=0)
    fingerprint_failures = models.IntegerField(default=0)
    last_fingerprint_verified = models.DateTimeField(null=True, blank=True)
    fingerprint_registered_at = models.DateTimeField(null=True, blank=True)
    
    # ========== PUSH NOTIFICATIONS ==========
    fcm_token = models.CharField(max_length=255, blank=True, null=True)
    
    # ========== 2FA AUTHENTICATION ==========
    two_factor_secret = models.CharField(max_length=255, blank=True, null=True)
    two_factor_enabled = models.BooleanField(default=False)
    
    # ========== RESET TOKEN ==========
    reset_token_hash = models.CharField(max_length=128, null=True, blank=True)
    reset_token_expires = models.DateTimeField(null=True, blank=True)
    
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    joined_date = models.DateField(auto_now_add=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return self.user.username if self.user else self.name
    
    def set_reset_token(self, raw_token):
        import hashlib
        self.reset_token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        self.reset_token_expires = django_timezone.now() + timedelta(hours=1)
    
    def verify_reset_token(self, raw_token):
        import hashlib
        from django.utils import timezone
        
        if not self.reset_token_hash or not self.reset_token_expires:
            return False
        if timezone.now() > self.reset_token_expires:
            return False
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        return token_hash == self.reset_token_hash
    
    def get_work_schedule(self):
        if self.company and self.company.schedule_type == 'shifts' and self.assigned_shift:
            return {
                'type': 'shift',
                'name': self.assigned_shift.name,
                'start_time': self.assigned_shift.start_time,
                'end_time': self.assigned_shift.end_time,
                'late_threshold': self.assigned_shift.late_threshold,
                'early_threshold': self.assigned_shift.early_departure_threshold,
            }
        elif self.company:
            return {
                'type': 'fixed',
                'name': 'Company Schedule',
                'start_time': self.company.fixed_start_time,
                'end_time': self.company.fixed_end_time,
                'late_threshold': self.company.fixed_late_threshold,
                'early_threshold': self.company.fixed_early_departure_threshold,
            }
        else:
            return {
                'type': 'fixed',
                'name': 'Default Schedule',
                'start_time': time(9, 0),
                'end_time': time(17, 0),
                'late_threshold': 15,
                'early_threshold': 15,
            }
    
    class Meta:
        indexes = [
            models.Index(fields=['email']),
            models.Index(fields=['phone']),
            models.Index(fields=['company', 'status']),
            models.Index(fields=['user', 'company']),
            models.Index(fields=['-created_at']),
            models.Index(fields=['reset_token_hash', 'reset_token_expires']),
        ]


# ================= EMPLOYEE FINGERPRINT (Multi-Fingerprint Support) =================
class EmployeeFingerprint(models.Model):
    FINGER_POSITIONS = [
        ('RIGHT_THUMB', 'Right Thumb'),
        ('RIGHT_INDEX', 'Right Index'),
        ('RIGHT_MIDDLE', 'Right Middle'),
        ('RIGHT_RING', 'Right Ring'),
        ('RIGHT_PINKY', 'Right Pinky'),
        ('LEFT_THUMB', 'Left Thumb'),
        ('LEFT_INDEX', 'Left Index'),
        ('LEFT_MIDDLE', 'Left Middle'),
        ('LEFT_RING', 'Left Ring'),
        ('LEFT_PINKY', 'Left Pinky'),
    ]
    
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='fingerprints')
    fingerprint_hash = models.CharField(max_length=255)
    finger_position = models.CharField(max_length=20, choices=FINGER_POSITIONS)
    registered_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    last_verified = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        unique_together = ('employee', 'finger_position')
        ordering = ['finger_position']
    
    def __str__(self):
        return f"{self.employee.name} - {self.get_finger_position_display()}"


# ================= BIOMETRIC AUDIT TRAIL =================
class BiometricAudit(models.Model):
    BIOMETRIC_TYPES = [
        ('face', 'Face'),
        ('fingerprint', 'Fingerprint'),
    ]
    
    ACTIONS = [
        ('check_in', 'Check In'),
        ('check_out', 'Check Out'),
        ('random_verification', 'Random Verification'),
        ('registration', 'Registration'),
    ]
    
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='biometric_audits')
    biometric_type = models.CharField(max_length=20, choices=BIOMETRIC_TYPES)
    action = models.CharField(max_length=30, choices=ACTIONS)
    success = models.BooleanField()
    confidence_score = models.FloatField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    location_lat = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    location_lng = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    details = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['employee', 'biometric_type']),
            models.Index(fields=['timestamp']),
        ]
    
    def __str__(self):
        return f"{self.employee.name} - {self.biometric_type} - {self.action} - {'Success' if self.success else 'Failed'}"


# ================= RANDOM VERIFICATION =================
class RandomVerification(models.Model):
    VERIFICATION_TYPE = (
        ('face', 'Face Recognition'),
        ('fingerprint', 'Fingerprint'),
    )
    
    VERIFICATION_STATUS = (
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('missed', 'Missed'),
        ('failed', 'Failed'),
    )
    
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='random_verifications')
    date = models.DateField(db_index=True)
    scheduled_time = models.DateTimeField()
    completed_time = models.DateTimeField(null=True, blank=True)
    verification_type = models.CharField(max_length=20, choices=VERIFICATION_TYPE)
    status = models.CharField(max_length=20, choices=VERIFICATION_STATUS, default='pending')
    face_score = models.FloatField(null=True, blank=True)
    location_lat = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    location_lng = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-date', 'scheduled_time']
        indexes = [
            models.Index(fields=['employee', 'date', 'status']),
            models.Index(fields=['scheduled_time']),
        ]
    
    def __str__(self):
        return f"{self.employee.name} - {self.date} - {self.verification_type} - {self.status}"


# ================= LEAVE =================
class Leave(models.Model):
    LEAVE_TYPES = (
        ('annual', 'Annual Leave'),
        ('sick', 'Sick Leave'),
        ('casual', 'Casual Leave'),
        ('unpaid', 'Unpaid Leave'),
        ('emergency', 'Emergency Leave'),
    )

    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
    )

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='leaves')
    leave_type = models.CharField(max_length=20, choices=LEAVE_TYPES)
    reason = models.TextField()
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    document = models.FileField(upload_to='leave_docs/', null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    requested_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_leaves')

    class Meta:
        ordering = ['-requested_at']
        indexes = [
            models.Index(fields=['employee', 'status']),
            models.Index(fields=['requested_at']),
        ]

    def __str__(self):
        return f"{self.employee.name} - {self.leave_type} - {self.status}"


# ================= ATTENDANCE =================
class Attendance(models.Model):
    STATUS_CHOICES = (
        ("present", "Present"),
        ("absent", "Absent"),
        ("late", "Late"),
        ("early_departure", "Early Departure"),
        ("out_of_zone", "Out of Zone"),
    )

    VERIFICATION_METHODS = (
        ('face_fingerprint', 'Face + Fingerprint'),
        ('face', 'Face Only'),
        ('fingerprint', 'Fingerprint Only'),
        ('manual', 'Manual Entry'),
        ('beacon', 'Beacon Auto Check-in'),
    )

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='attendances')
    date = models.DateField(db_index=True)
    check_in = models.TimeField(null=True, blank=True)
    check_out = models.TimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="absent")
    verification_method = models.CharField(max_length=20, choices=VERIFICATION_METHODS, null=True, blank=True)
    verified_count = models.PositiveIntegerField(default=0)
    check_in_location = models.CharField(max_length=255, blank=True, null=True)
    check_out_location = models.CharField(max_length=255, blank=True, null=True)
    shift_name = models.CharField(max_length=50, blank=True, null=True)
    shift_start = models.TimeField(null=True, blank=True)
    shift_end = models.TimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "date"],
                name="unique_attendance_per_day"
            )
        ]
        indexes = [
            models.Index(fields=["date"]),
            models.Index(fields=["employee", "date"]),
            models.Index(fields=["status"]),
        ]
        ordering = ["-date"]

    def __str__(self):
        return f"{self.employee.name} - {self.date} - {self.status}"


# ================= PASSWORD RESET TOKEN =================
class PasswordResetToken(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reset_tokens')
    token = models.CharField(max_length=100, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    
    def is_valid(self):
        from django.utils import timezone
        return not self.is_used and self.expires_at > timezone.now()
    
    def __str__(self):
        return f"Reset token for {self.user.username}"
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['token', 'expires_at']),
            models.Index(fields=['user', 'is_used']),
        ]


# ================= NOTIFICATIONS =================
class Notification(models.Model):
    NOTIFICATION_TYPES = (
        ('info', 'Info'),
        ('success', 'Success'),
        ('warning', 'Warning'),
        ('error', 'Error'),
    )
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    title = models.CharField(max_length=100, blank=True, null=True)
    message = models.CharField(max_length=255)
    notification_type = models.CharField(max_length=10, choices=NOTIFICATION_TYPES, default='info')
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'is_read', '-created_at']),
        ]

    def __str__(self):
        return f"{self.user.username}: {self.message[:50]}"


# ================= ACTIVITY LOG =================
class ActivityLog(models.Model):
    ACTION_CHOICES = (
        ('create', 'Create'),
        ('update', 'Update'),
        ('delete', 'Delete'),
        ('approve', 'Approve'),
        ('reject', 'Reject'),
        ('login', 'Login'),
        ('logout', 'Logout'),
        ('missed', 'Missed Verification'),
        ('failed', 'Failed Verification'),
        ('password_reset', 'Password Reset'),
        ('forgot_password', 'Forgot Password'),
    )
    
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    model_name = models.CharField(max_length=50)
    object_id = models.IntegerField(null=True, blank=True)
    object_name = models.CharField(max_length=200, blank=True)
    details = models.TextField(blank=True, null=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = "Activity Logs"
        indexes = [
            models.Index(fields=['user', 'action', '-created_at']),
            models.Index(fields=['created_at']),
        ]
    
    def __str__(self):
        return f"{self.user} - {self.action} - {self.model_name} at {self.created_at}"


# ================= IDEMPOTENCY KEY =================
class IdempotencyKey(models.Model):
    key = models.CharField(max_length=255, unique=True, db_index=True)
    request_type = models.CharField(max_length=50)
    user_id = models.IntegerField(null=True, blank=True)
    response_data = models.JSONField()
    status_code = models.IntegerField(default=200)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    
    class Meta:
        indexes = [
            models.Index(fields=['key', 'expires_at']),
            models.Index(fields=['user_id', 'created_at']),
        ]
    
    def __str__(self):
        return f"{self.request_type} - {self.key[:20]}"