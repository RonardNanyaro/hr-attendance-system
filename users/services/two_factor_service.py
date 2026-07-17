import pyotp
import qrcode
from io import BytesIO
import base64
import logging

logger = logging.getLogger(__name__)

class TwoFactorService:
    @staticmethod
    def generate_secret():
        return pyotp.random_base32()
    
    @staticmethod
    def generate_qr_code(employee, secret):
        try:
            totp = pyotp.TOTP(secret)
            uri = totp.provisioning_uri(
                name=employee.email,
                issuer_name="YourApp"
            )
            
            qr = qrcode.QRCode(version=1, box_size=10, border=5)
            qr.add_data(uri)
            qr.make(fit=True)
            
            img = qr.make_image(fill_color="black", back_color="white")
            buffered = BytesIO()
            img.save(buffered, format="PNG")
            img_str = base64.b64encode(buffered.getvalue()).decode()
            
            return {
                'secret': secret,
                'qr_code': f'data:image/png;base64,{img_str}'
            }
        except Exception as e:
            logger.error(f"QR code generation error: {str(e)}")
            return None
    
    @staticmethod
    def verify_otp(secret, otp_code):
        try:
            totp = pyotp.TOTP(secret)
            return totp.verify(otp_code)
        except Exception as e:
            logger.error(f"OTP verification error: {str(e)}")
            return False