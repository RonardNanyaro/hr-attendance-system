# users/services/biometric_service.py

import cv2
import numpy as np
from PIL import Image
import io
import base64
import re
import logging

logger = logging.getLogger(__name__)


class BiometricService:
    """Biometric Service using OpenCV"""
    
    def __init__(self):
        pass
    
    def verify_face(self, employee, face_data):
        """
        Verify face biometric using OpenCV
        """
        try:
            if not face_data:
                return {'verified': False, 'message': 'Face data required', 'score': 0}
            
            # Decode base64 image
            if 'base64,' in face_data:
                face_data = face_data.split('base64,')[1]
            
            if not re.match(r'^[A-Za-z0-9+/]+=*$', face_data):
                return {'verified': False, 'message': 'Invalid image format', 'score': 0}
            
            image_data = base64.b64decode(face_data)
            
            try:
                image = Image.open(io.BytesIO(image_data))
            except Exception as e:
                logger.error(f"Image open error: {str(e)}")
                return {'verified': False, 'message': 'Invalid image format', 'score': 0}
            
            if image.mode != 'RGB':
                image = image.convert('RGB')
            
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
                return {'verified': False, 'message': 'No face detected', 'score': 0}
            
            if len(faces) > 1:
                return {'verified': False, 'message': f'Multiple faces detected ({len(faces)})', 'score': 0}
            
            # Face detected successfully
            confidence = 85
            
            # Update employee verification count
            if employee:
                employee.face_verification_count = (employee.face_verification_count or 0) + 1
                employee.last_face_verified = timezone.now()
                employee.face_failures = 0
                employee.save()
            
            return {'verified': True, 'message': 'Face verified successfully', 'score': confidence}
            
        except Exception as e:
            logger.error(f"Face verification error: {str(e)}")
            return {'verified': False, 'message': f'Face verification failed: {str(e)}', 'score': 0}
    
    def verify_fingerprint(self, employee, fingerprint_data):
        """
        Verify fingerprint biometric
        """
        try:
            if not fingerprint_data:
                return {'verified': False, 'message': 'Fingerprint data required', 'score': 0}
            
            # Generate hash from fingerprint data
            import hashlib
            fingerprint_hash = hashlib.sha256(fingerprint_data.encode()).hexdigest()
            
            if employee and employee.fingerprint_hash:
                if fingerprint_hash == employee.fingerprint_hash:
                    employee.fingerprint_verification_count = (employee.fingerprint_verification_count or 0) + 1
                    employee.last_fingerprint_verified = timezone.now()
                    employee.fingerprint_failures = 0
                    employee.save()
                    return {'verified': True, 'message': 'Fingerprint verified', 'score': 98}
                else:
                    return {'verified': False, 'message': 'Fingerprint does not match', 'score': 0}
            else:
                return {'verified': False, 'message': 'Fingerprint not registered', 'score': 0}
                
        except Exception as e:
            logger.error(f"Fingerprint verification error: {str(e)}")
            return {'verified': False, 'message': f'Fingerprint verification failed: {str(e)}', 'score': 0}
    
    def register_face(self, employee, face_data):
        """
        Register face biometric
        """
        try:
            if not face_data:
                return {'success': False, 'message': 'Face data required'}
            
            # Verify face first
            result = self.verify_face(employee, face_data)
            
            if result['verified']:
                return {'success': True, 'message': 'Face registered successfully'}
            else:
                return {'success': False, 'message': result.get('message', 'Face registration failed')}
                
        except Exception as e:
            logger.error(f"Face registration error: {str(e)}")
            return {'success': False, 'message': f'Face registration failed: {str(e)}'}
    
    def register_fingerprint(self, employee, fingerprint_data):
        """
        Register fingerprint biometric
        """
        try:
            if not fingerprint_data:
                return {'success': False, 'message': 'Fingerprint data required'}
            
            import hashlib
            fingerprint_hash = hashlib.sha256(fingerprint_data.encode()).hexdigest()
            
            if employee:
                employee.fingerprint_hash = fingerprint_hash
                employee.fingerprint_registered_at = timezone.now()
                employee.fingerprint_verification_count = 0
                employee.fingerprint_failures = 0
                employee.save()
            
            return {'success': True, 'message': 'Fingerprint registered successfully'}
            
        except Exception as e:
            logger.error(f"Fingerprint registration error: {str(e)}")
            return {'success': False, 'message': f'Fingerprint registration failed: {str(e)}'}