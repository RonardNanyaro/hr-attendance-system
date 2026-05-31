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
    
    # Who requested this company
    requested_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='requested_companies')
    requested_at = models.DateTimeField(auto_now_add=True)
    
    # Who approved/rejected this company
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_companies')
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, null=True)
    
    # Additional company info
    phone = models.CharField(max_length=20, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    company_code = models.CharField(max_length=20, blank=True, null=True, unique=True, help_text="Unique company identifier")
    
    # ========== SCHEDULE SETTINGS (Set by HR) ==========
    schedule_type = models.CharField(max_length=10, choices=SCHEDULE_TYPE_CHOICES, default='fixed')
    
    # For FIXED schedule (HR sets these)
    fixed_start_time = models.TimeField(null=True, blank=True, default=time(9, 0), help_text="Work start time e.g., 09:00")
    fixed_end_time = models.TimeField(null=True, blank=True, default=time(17, 0), help_text="Work end time e.g., 17:00")
    fixed_late_threshold = models.IntegerField(default=15, help_text="Minutes after start time considered late")
    fixed_early_departure_threshold = models.IntegerField(default=15, help_text="Minutes before end time considered early departure")
    
    # Working days (1=Monday, 7=Sunday)
    working_days = models.JSONField(default=list, help_text="[1,2,3,4,5] for Mon-Fri")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.name} ({self.status})"
    
    def get_working_days_display(self):
        """Return readable working days"""
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


# ================= SHIFT (For companies using shift schedule) =================
class Shift(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='shifts')
    name = models.CharField(max_length=50, help_text="e.g., Morning Shift, Night Shift")
    start_time = models.TimeField(help_text="Shift start time")
    end_time = models.TimeField(help_text="Shift end time")
    late_threshold = models.IntegerField(default=15, help_text="Minutes after start time considered late")
    early_departure_threshold = models.IntegerField(default=15, help_text="Minutes before end time considered early departure")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.company.name} - {self.name} ({self.start_time.strftime('%I:%M %p')} - {self.end_time.strftime('%I:%M %p')})"
    
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
    
    # Relationships
    company = models.ForeignKey(Company, on_delete=models.CASCADE, null=True, blank=True, related_name='employees')
    department_obj = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name='employees')
    
    # Shift assignment (only used if company uses shift schedule)
    assigned_shift = models.ForeignKey(Shift, on_delete=models.SET_NULL, null=True, blank=True, related_name='employees')
    
    # Attendance tracking
    check_in_time = models.DateTimeField(null=True, blank=True)
    check_out_time = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='absent')
    
    # Random verification tracking
    random_verify_count = models.IntegerField(default=0, help_text="Number of random verifications completed today")
    last_random_verify_time = models.DateTimeField(null=True, blank=True)
    
    # ========== SECURITY & AUTHENTICATION FIELDS ==========
    # FIX 1: Store face encoding for real face recognition
    face_encoding = models.BinaryField(null=True, blank=True, help_text="Stored face encoding for verification")
    
    # FIX 2: Store fingerprint hash instead of raw data
    fingerprint_hash = models.CharField(max_length=128, null=True, blank=True, help_text="SHA256 hash of fingerprint data")
    
    # FIX 3: Store reset token as HASH (not plain text)
    reset_token_hash = models.CharField(max_length=128, null=True, blank=True, help_text="SHA256 hash of reset token")
    reset_token_expires = models.DateTimeField(null=True, blank=True, help_text="Token expiration time")
    
    # Additional info
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    joined_date = models.DateField(auto_now_add=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return self.user.username if self.user else self.name
    
    # FIX 3: Secure token methods
    def set_reset_token(self, raw_token):
        """Hash and store reset token securely"""
        import hashlib
        self.reset_token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        self.reset_token_expires = django_timezone.now() + timedelta(hours=1)
    
    def verify_reset_token(self, raw_token):
        """Verify reset token"""
        import hashlib
        from django.utils import timezone
        
        if not self.reset_token_hash or not self.reset_token_expires:
            return False
        if timezone.now() > self.reset_token_expires:
            return False
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        return token_hash == self.reset_token_hash
    
    def get_work_schedule(self):
        """Get employee's work schedule based on company settings"""
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
            # FIX 4: Database indexes for performance
            models.Index(fields=['email']),
            models.Index(fields=['phone']),
            models.Index(fields=['company', 'status']),
            models.Index(fields=['user', 'company']),
            models.Index(fields=['-created_at']),
            models.Index(fields=['reset_token_hash', 'reset_token_expires']),
        ]


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
    face_score = models.FloatField(null=True, blank=True, help_text="Face recognition confidence score")
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
    
    # Date range for leave
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
    )

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='attendances')
    date = models.DateField(db_index=True)
    check_in = models.TimeField(null=True, blank=True)
    check_out = models.TimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="absent")
    
    # Verification tracking
    verification_method = models.CharField(max_length=20, choices=VERIFICATION_METHODS, null=True, blank=True)
    verified_count = models.PositiveIntegerField(default=0, help_text="Total number of verifications (check-in + random + check-out)")
    
    # Location tracking
    check_in_location = models.CharField(max_length=255, blank=True, null=True)
    check_out_location = models.CharField(max_length=255, blank=True, null=True)
    
    # Shift info for this attendance
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


# ================= PASSWORD RESET TOKEN (For HR/Admin web) =================
class PasswordResetToken(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reset_tokens')
    token = models.CharField(max_length=100, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    
    def is_valid(self):
        """Check if token is still valid (not expired and not used)"""
        from django.utils import timezone
        return not self.is_used and self.expires_at > timezone.now()
    
    def __str__(self):
        return f"Reset token for {self.user.username} - Expires: {self.expires_at}"
    
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