from django.urls import path
from . import views
from rest_framework_simplejwt.views import TokenRefreshView

app_name = "users"

urlpatterns = [
    # ================= DIRECT API URLs (Accessible at /api/...) =================
    # Public APIs (No login required)
    path("api/companies/", views.api_get_companies, name="api_get_companies"),
    path("api/companies/<int:company_id>/departments/", views.api_get_departments_by_company, name="api_get_departments_by_company"),
    path("api/employee/register/", views.api_employee_register, name="api_employee_register"),
    path("api/employee/register-text/", views.api_employee_register_with_text, name="api_employee_register_text"),
    path("api/employee/login/", views.api_employee_login, name="api_employee_login"),
    
    # Token Refresh Endpoint
    path("api/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    
    # Password Reset (Mobile)
    path("api/employee/forgot-password/", views.api_employee_forgot_password, name="api_employee_forgot_password"),
    path("api/employee/reset-password/", views.api_employee_reset_password, name="api_employee_reset_password"),
    
    # Work Settings API
    path("api/work-settings/", views.api_work_settings, name="api_work_settings"),
    
    # Social Auth APIs
    path("api/auth/google/", views.api_google_auth, name="api_google_auth"),
    path("api/auth/apple/", views.api_apple_auth, name="api_apple_auth"),
    path("api/auth/complete-profile/", views.api_complete_social_profile, name="api_complete_social_profile"),
    
    # ================= PROTECTED APIS (Require JWT Authentication) =================
    
    # Face Status & Re-register
    path("api/face/status/", views.api_face_status, name="api_face_status"),
    path("api/face/re-register/", views.api_re_register_face, name="api_re_register_face"),
    
    # Face Test Endpoint
    path("api/test-face/", views.api_test_face_verification, name="api_test_face"),
    
    # Offline Sync Endpoints
    path("api/sync/all-data/", views.api_sync_all_data, name="api_sync_all_data"),
    path("api/sync/offline-data/", views.api_sync_offline_data, name="api_sync_offline_data"),
    
    # 2FA Endpoints
    path("api/2fa/setup/", views.api_setup_2fa, name="api_setup_2fa"),
    path("api/2fa/enable/", views.api_enable_2fa, name="api_enable_2fa"),
    path("api/2fa/verify-login/", views.api_verify_2fa_login, name="api_verify_2fa_login"),
    
    # Check-in Status
    path("api/attendance/check-in-status/", views.api_check_in_status, name="api_check_in_status"),
    
    # Attendance APIs
    path("api/attendance/check-in/", views.api_check_in, name="api_check_in"),
    path("api/attendance/check-out/", views.api_check_out, name="api_check_out"),
    path("api/attendance/history/", views.api_attendance_history, name="api_attendance_history"),
    path("api/attendance/today/", views.api_today_attendance, name="api_today_attendance"),
    path("api/attendance/random-verification/", views.api_check_random_verification, name="api_check_random_verification"),
    path("api/attendance/random-verification/<int:verification_id>/", views.api_submit_random_verification, name="api_submit_random_verification"),
    path("api/attendance/verification-status/", views.api_verification_status, name="api_verification_status"),
    
    # Leave APIs
    path("api/leave/apply/", views.api_apply_leave, name="api_apply_leave"),
    path("api/leave/history/", views.api_leave_history, name="api_leave_history"),
    path("api/leave/balance/", views.api_leave_balance, name="api_leave_balance"),
    path("api/leave/cancel/<int:leave_id>/", views.api_cancel_leave, name="api_cancel_leave"),
    
    # Sync APIs
    path("api/sync/attendance/", views.api_sync_attendance, name="api_sync_attendance"),
    path("api/sync/leaves/", views.api_sync_leaves, name="api_sync_leaves"),
    
    # Employee APIs
    path("api/beacons/", views.api_get_beacons, name="api_get_beacons"),
    path("api/stats/dashboard/", views.api_dashboard_stats, name="api_dashboard_stats"),
    path("api/stats/monthly/", views.api_monthly_stats, name="api_monthly_stats"),
    path("api/my-schedule/", views.api_my_schedule, name="api_my_schedule"),
    path("api/my-shift-info/", views.api_my_shift_info, name="api_my_shift_info"),
    path("api/employee/shifts/", views.api_get_shifts_for_employee, name="api_get_shifts_for_employee"),
    path("api/employee/profile/", views.api_employee_profile, name="api_employee_profile"),
    
    # Shift Management APIs
    path("api/employee/assign-shift/", views.api_assign_employee_shift, name="api_assign_employee_shift"),
    path("api/employee/unassign-shift/", views.api_unassign_employee_shift, name="api_unassign_employee_shift"),
    path("api/shift/unassign-all/", views.api_shift_unassign_all, name="api_shift_unassign_all"),
    path("api/shift/bulk-assign/", views.api_shift_bulk_assign, name="api_shift_bulk_assign"),
    
    # ================= WEB VIEWS =================
    # Home & Auth
    path("", views.hr_login, name="home"),
    path("login/", views.admin_login, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("hr-login/", views.hr_login, name="hr_login"),
    path("hr-register/", views.hr_register, name="hr_register"),
    path("hr-dashboard/", views.hr_dashboard, name="hr_dashboard"),
    path("dashboard/", views.dashboard, name="dashboard"),
    
    # Employee Web Dashboard
    path("employee/dashboard/", views.employee_dashboard, name="employee_dashboard"),
    path("employee/check-in/", views.employee_check_in, name="employee_check_in"),
    path("employee/check-out/", views.employee_check_out, name="employee_check_out"),
    
    # Social Auth Web Views
    path("auth/google/", views.google_auth, name="google_auth"),
    path("auth/apple/", views.apple_auth, name="apple_auth"),
    path("auth/callback/google/", views.google_auth_callback, name="google_auth_callback"),
    path("auth/callback/apple/", views.apple_auth_callback, name="apple_auth_callback"),
    path("complete-profile/", views.complete_company_profile, name="complete_company_profile"),
    
    # Password Reset (Web)
    path("hr-forgot-password/", views.hr_forgot_password, name="hr_forgot_password"),
    path("hr-reset-password/<str:token>/", views.hr_reset_password, name="hr_reset_password"),
    
    # Admin HR Management
    path("approve-hr/<int:hr_id>/", views.approve_hr, name="approve_hr"),
    path("reject-hr/<int:hr_id>/", views.reject_hr, name="reject_hr"),
    path("delete-hr/<int:user_id>/", views.delete_hr, name="delete_hr"),
    
    # Leave Management
    path("leave/", views.leave_page, name="leave_page"),
    path("handle-leave/", views.handle_leave, name="handle_leave"),
    
    # Attendance & Reports
    path("attendance/", views.attendance_page, name="attendance_page"),
    path("attendance/export/", views.export_attendance_csv, name="export_attendance_csv"),
    path("attendance/live/", views.live_attendance, name="live_attendance"),
    path("attendance/employee/<int:employee_id>/", views.employee_attendance_detail, name="employee_attendance_detail"),
    path("report/", views.report_page, name="report_page"),
    path("export-csv/", views.export_csv, name="export_csv"),
    path("export-pdf/", views.export_pdf, name="export_pdf"),
    path("analytics/", views.analytics_page, name="analytics_page"),
    
    # Employee Verification
    path("employee-verification/", views.employee_verification_page, name="employee_verification"),
    path("verify-employee/<int:profile_id>/", views.verify_employee, name="verify_employee"),
    
    # Employee Management
    path('delete-employee/<int:employee_id>/', views.delete_employee, name='delete_employee'),
    path('update-employee/<int:employee_id>/', views.update_employee, name='update_employee'),
    
    # Company Settings & Shifts
    path("company-settings/", views.company_settings, name="company_settings"),
    path("manage-shifts/", views.manage_shifts, name="manage_shifts"),
    path("shift-assignments/", views.shift_assignments, name="shift_assignments"),
    path("assign-employee-shift/", views.assign_employee_shift, name="assign_employee_shift"),
    
    # Company Management (Admin)
    path("pending-companies/", views.pending_companies_page, name="pending_companies"),
    path("approve-company/<int:company_id>/", views.approve_company, name="approve_company"),
    path("reject-company/<int:company_id>/", views.reject_company, name="reject_company"),
    
    # Department Management
    path("departments/", views.departments_page, name="departments_page"),
    path("api/departments/", views.api_get_departments, name="api_get_departments"),
    path("api/departments/create/", views.api_create_department, name="api_create_department"),
    path("api/departments/delete/<int:dept_id>/", views.api_delete_department, name="api_delete_department"),
    
    # Health Check
    path("health/", views.health_check, name="health_check"),
]