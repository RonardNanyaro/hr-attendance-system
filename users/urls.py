from django.urls import path
from . import views

app_name = "users"

urlpatterns = [
    # ================= ROOT URL - Redirect to HR Login =================
    path("", views.hr_login, name="home"),  # Changed from views.home to views.hr_login
    
    # ================= EXISTING WEB URLs =================
    path("login/", views.admin_login, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("hr-login/", views.hr_login, name="hr_login"),
    path("hr-register/", views.hr_register, name="hr_register"),
    path("hr-dashboard/", views.hr_dashboard, name="hr_dashboard"),
    
    # ================= HR PASSWORD RESET URLs =================
    path("hr-forgot-password/", views.hr_forgot_password, name="hr_forgot_password"),
    path("hr-reset-password/<str:token>/", views.hr_reset_password, name="hr_reset_password"),
    
    # ================= MOBILE EMPLOYEE PASSWORD RESET URLs =================
    path("api/employee/forgot-password/", views.api_employee_forgot_password, name="api_employee_forgot_password"),
    path("api/employee/reset-password/", views.api_employee_reset_password, name="api_employee_reset_password"),
    
    # ================= ADMIN HR MANAGEMENT URLs =================
    path("approve-hr/<int:hr_id>/", views.approve_hr, name="approve_hr"),
    path("reject-hr/<int:hr_id>/", views.reject_hr, name="reject_hr"),
    path("delete-hr/<int:user_id>/", views.delete_hr, name="delete_hr"),
    
    # ================= LEAVE MANAGEMENT URLs =================
    path("leave/", views.leave_page, name="leave_page"),
    path("handle-leave/", views.handle_leave, name="handle_leave"),
    
    # ================= ATTENDANCE & REPORT URLs =================
    path("attendance/", views.attendance_page, name="attendance_page"),
    path("report/", views.report_page, name="report_page"),
    path("export-csv/", views.export_csv, name="export_csv"),
    path("export-pdf/", views.export_pdf, name="export_pdf"),
    path("analytics/", views.analytics_page, name="analytics_page"),
    
    # ================= EMPLOYEE VERIFICATION URLs =================
    path("employee-verification/", views.employee_verification_page, name="employee_verification"),
    path("verify-employee/<int:profile_id>/", views.verify_employee, name="verify_employee"),
    
    # ================= EMPLOYEE MANAGEMENT URLs =================
    path('delete-employee/<int:employee_id>/', views.delete_employee, name='delete_employee'),
    path('update-employee/<int:employee_id>/', views.update_employee, name='update_employee'),
    
    # ================= COMPANY SCHEDULE & SHIFT URLs =================
    path("company-settings/", views.company_settings, name="company_settings"),
    path("manage-shifts/", views.manage_shifts, name="manage_shifts"),
    path("assign-employee-shift/", views.assign_employee_shift, name="assign_employee_shift"),
    
    # ================= COMPANY MANAGEMENT URLs (Admin) =================
    path("pending-companies/", views.pending_companies_page, name="pending_companies"),
    path("approve-company/<int:company_id>/", views.approve_company, name="approve_company"),
    path("reject-company/<int:company_id>/", views.reject_company, name="reject_company"),
    
    # ================= DEPARTMENT MANAGEMENT URLs =================
    path("departments/", views.departments_page, name="departments_page"),
    path("api/departments/", views.api_get_departments, name="api_get_departments"),
    path("api/departments/create/", views.api_create_department, name="api_create_department"),
    path("api/departments/delete/<int:dept_id>/", views.api_delete_department, name="api_delete_department"),
    
    # ================= PUBLIC API FOR EMPLOYEE REGISTRATION (NEW) =================
    # These endpoints do NOT require login - for employee registration
    path("api/companies/", views.api_get_companies, name="api_get_companies"),
    path("api/companies/<int:company_id>/departments/", views.api_get_departments_by_company, name="api_get_departments_by_company"),
    
    # ================= EMPLOYEE SCHEDULE API URLs =================
    path("api/my-schedule/", views.api_my_schedule, name="api_my_schedule"),
    path("api/my-shift-info/", views.api_my_shift_info, name="api_my_shift_info"),
    path("api/employee/shifts/", views.api_get_shifts_for_employee, name="api_get_shifts_for_employee"),
    
    # ================= MOBILE AUTHENTICATION APIs =================
    path("api/employee/register/", views.api_employee_register, name="api_employee_register"),
    path("api/employee/login/", views.api_employee_login, name="api_employee_login"),
    path("api/employee/profile/", views.api_employee_profile, name="api_employee_profile"),
    
    # ================= MOBILE ATTENDANCE APIs =================
    path("api/attendance/check-in/", views.api_check_in, name="api_check_in"),
    path("api/attendance/check-out/", views.api_check_out, name="api_check_out"),
    path("api/attendance/history/", views.api_attendance_history, name="api_attendance_history"),
    path("api/attendance/today/", views.api_today_attendance, name="api_today_attendance"),
    
    # ================= MOBILE RANDOM VERIFICATION APIs =================
    path("api/attendance/random-verification/", views.api_check_random_verification, name="api_check_random_verification"),
    path("api/attendance/random-verification/<int:verification_id>/", views.api_submit_random_verification, name="api_submit_random_verification"),
    path("api/attendance/verification-status/", views.api_verification_status, name="api_verification_status"),
    
    # ================= MOBILE LEAVE APIs =================
    path("api/leave/apply/", views.api_apply_leave, name="api_apply_leave"),
    path("api/leave/history/", views.api_leave_history, name="api_leave_history"),
    path("api/leave/balance/", views.api_leave_balance, name="api_leave_balance"),
    path("api/leave/cancel/<int:leave_id>/", views.api_cancel_leave, name="api_cancel_leave"),
    
    # ================= MOBILE SYNC APIs =================
    path("api/sync/attendance/", views.api_sync_attendance, name="api_sync_attendance"),
    path("api/sync/leaves/", views.api_sync_leaves, name="api_sync_leaves"),
    
    # ================= MOBILE UTILITY APIs =================
    path("api/beacons/", views.api_get_beacons, name="api_get_beacons"),
    path("api/stats/dashboard/", views.api_dashboard_stats, name="api_dashboard_stats"),
    path("api/stats/monthly/", views.api_monthly_stats, name="api_monthly_stats"),
    
    # ================= API VERSIONING =================
    path("api/v1/employee/register/", views.api_employee_register, name="api_employee_register_v1"),
    path("api/v1/employee/login/", views.api_employee_login, name="api_employee_login_v1"),
    path("api/v1/attendance/check-in/", views.api_check_in, name="api_check_in_v1"),
    path("api/v1/attendance/check-out/", views.api_check_out, name="api_check_out_v1"),
]