from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/employee/(?P<employee_id>\w+)/$', consumers.EmployeeConsumer.as_asgi()),
]
