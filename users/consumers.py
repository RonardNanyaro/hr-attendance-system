import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth.models import User
from rest_framework_simplejwt.tokens import AccessToken

class EmployeeConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        token = self.scope['query_string'].decode()
        if not token:
            await self.close()
            return
        
        user = await self.get_user_from_token(token)
        if not user:
            await self.close()
            return
        
        self.user = user
        self.room_group_name = f'employee_{user.id}'
        
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        await self.accept()
    
    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )
    
    async def receive(self, text_data):
        data = json.loads(text_data)
        pass
    
    async def send_notification(self, event):
        await self.send(text_data=json.dumps({
            'type': event['type'],
            'data': event['data']
        }))
    
    @database_sync_to_async
    def get_user_from_token(self, token):
        try:
            if token.startswith('token='):
                token = token.replace('token=', '')
            
            access_token = AccessToken(token)
            user_id = access_token.payload.get('user_id')
            return User.objects.get(id=user_id)
        except Exception:
            return None
