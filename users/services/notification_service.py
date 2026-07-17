import firebase_admin
from firebase_admin import credentials, messaging
import os
import logging
from django.conf import settings
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

logger = logging.getLogger(__name__)

class PushNotificationService:
    def __init__(self):
        try:
            if not firebase_admin._apps:
                cred = credentials.Certificate(settings.FIREBASE_CREDENTIALS_PATH)
                firebase_admin.initialize_app(cred)
        except Exception as e:
            logger.error(f"Firebase init error: {str(e)}")
    
    def send_verification_reminder(self, employee, verification_time):
        try:
            if not employee.fcm_token:
                return False
            
            message = messaging.Message(
                notification=messaging.Notification(
                    title="Random Verification Required",
                    body=f"Verify at {verification_time.strftime('%I:%M %p')}"
                ),
                data={
                    'type': 'random_verification',
                    'verification_time': verification_time.isoformat()
                },
                token=employee.fcm_token
            )
            
            messaging.send(message)
            return True
            
        except Exception as e:
            logger.error(f"Push notification error: {str(e)}")
            return False
    
    def send_attendance_alert(self, employee, check_type, status):
        try:
            if not employee.fcm_token:
                return False
            
            message = messaging.Message(
                notification=messaging.Notification(
                    title=f"{check_type.title()} {status}",
                    body=f"Your {check_type} was recorded as {status}"
                ),
                data={
                    'type': 'attendance',
                    'check_type': check_type,
                    'status': status
                },
                token=employee.fcm_token
            )
            
            messaging.send(message)
            return True
            
        except Exception as e:
            logger.error(f"Attendance alert error: {str(e)}")
            return False
    
    def send_websocket_notification(self, user_id, notification_type, data):
        try:
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f'employee_{user_id}',
                {
                    'type': 'send_notification',
                    'data': data,
                    'notification_type': notification_type
                }
            )
            return True
        except Exception as e:
            logger.error(f"WebSocket notification error: {str(e)}")
            return False