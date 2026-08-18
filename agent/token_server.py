import os
from aiohttp import web
from livekit import api
from .config import Config

async def handle_token(request):
    room_name = request.query.get('room', 'voice-agent-room')
    participant_name = request.query.get('participant', 'user')

    token = api.AccessToken(Config.LIVEKIT_API_KEY, Config.LIVEKIT_API_SECRET)
    token.with_identity(participant_name).with_name(participant_name).with_grants(
        api.VideoGrants(room_join=True, room=room_name)
    )

    return web.json_response({'token': token.to_jwt()})

app = web.Application()
app.router.add_get('/token', handle_token)

# Add CORS
import aiohttp_cors
cors = aiohttp_cors.setup(app, defaults={
    "*": aiohttp_cors.ResourceOptions(
        allow_credentials=True,
        expose_headers="*",
        allow_headers="*"
    )
})
for route in list(app.router.routes()):
    cors.add(route)

if __name__ == '__main__':
    web.run_app(app, port=3001)
