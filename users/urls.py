from django.urls import path
from . import views

app_name = "users"

urlpatterns = [
    # ================= DIRECT API URLs (Accessible at /api/...) =================
    # Public APIs (No login required)
    path("api/companies/", views.api_get_companies, name="api_get_companies"),
    path("api/companies/<int:company_id>/departments/", views.api_get_departments_by_company, name="api_get_departments_by_company"),
    path("api/employee/register/", views.api_employee_register, name="api_employee_register"),
    path("api/employee/login/", views.api_employee_login, name="api_employee_login"),
    path("api/employee/forgot-password/", views.api_employee_forgot_password, name="api_employee_forgot_password"),
    path("api/employee/reset-password/", views.api_employee_reset_password, name="api_employee_reset_password"),
    
    # Protected APIs (Require login)
    path("api/attendance/check-in/", views.api_check_in, name="api_check_in"),
    path("api/attendance/check-out/", views.api_check_out, name="api_check_out"),
    path("api/attendance/history/", views.api_attendance_history, name="api_attendance_history"),
    path("api/attendance/today/", views.api_today_attendance, name="api_today_attendance"),
    path("api/attendance/random-verification/", views.api_check_random_verification, name="api_check_random_verification"),
    path("api/attendance/random-verification/<int:verification_id>/", views.api_submit_random_verification, name="api_submit_random_verification"),
    path("api/attendance/verification-status/", views.api_verification_status, name="api_verification_status"),
    path("api/leave/apply/", views.api_apply_leave, name="api_apply_leave"),
    path("api/leave/history/", views.api_leave_history, name="api_leave_history"),
    path("api/leave/balance/", views.api_leave_balance, name="api_leave_balance"),
    path("api/leave/cancel/<int:leave_id>/", views.api_cancel_leave, name="api_cancel_leave"),
    path("api/sync/attendance/", views.api_sync_attendance, name="api_sync_attendance"),
    path("api/sync/leaves/", views.api_sync_leaves, name="api_sync_leaves"),
    path("api/beacons/", views.api_get_beacons, name="api_get_beacons"),
    path("api/stats/dashboard/", views.api_dashboard_stats, name="api_dashboard_stats"),
    path("api/stats/monthly/", views.api_monthly_stats, name="api_monthly_stats"),
    path("api/my-schedule/", views.api_my_schedule, name="api_my_schedule"),
    path("api/my-shift-info/", views.api_my_shift_info, name="api_my_shift_info"),
    path("api/employee/shifts/", views.api_get_shifts_for_employee, name="api_get_shifts_for_employee"),
    path("api/employee/profile/", views.api_employee_profile, name="api_employee_profile"),
    
    # ================= WEB VIEWS =================
    # Root URL - Redirect to HR Login
    path("", views.hr_login, name="home"),
    
    # Authentication
    path("login/", views.admin_login, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("hr-login/", views.hr_login, name="hr_login"),
    path("hr-register/", views.hr_register, name="hr_register"),
    path("hr-dashboard/", views.hr_dashboard, name="hr_dashboard"),
    path("dashboard/", views.dashboard, name="dashboard"),
    
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