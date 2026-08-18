import os
import logging
from aiohttp import web
from livekit import api
from .config import Config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def try_cloud_room(room_name: str) -> bool:
    if not Config.LIVEKIT_CLOUD_URL or not Config.LIVEKIT_CLOUD_API_KEY:
        return False
        
    try:
        # LiveKit API needs http/https, not ws/wss
        http_url = Config.LIVEKIT_CLOUD_URL.replace("wss://", "https://").replace("ws://", "http://")
        async with api.LiveKitAPI(http_url, Config.LIVEKIT_CLOUD_API_KEY, Config.LIVEKIT_CLOUD_API_SECRET) as lkapi:
            # We trigger an API call to explicitly catch quota/billing/auth failures
            await lkapi.room.create_room(api.CreateRoomRequest(name=room_name))
        return True
    except Exception as e:
        err_str = str(e).lower()
        if "quota" in err_str or "limit" in err_str or "billing" in err_str or "402" in err_str or "429" in err_str or "unauthorized" in err_str:
            logger.warning(f"[Routing] Cloud quota/billing/auth error: {e}. Falling back to local.")
            return False
        # To be safe, if we get any other unexpected error attempting to reach Cloud, we fallback
        logger.warning(f"[Routing] Unexpected Cloud API error: {e}. Falling back to local.")
        return False

async def handle_token(request):
    room_name = request.query.get('room', 'voice-agent-room')
    participant_name = request.query.get('participant', 'user')

    use_cloud = await try_cloud_room(room_name)

    if use_cloud:
        logger.info("[Routing] Using LiveKit Cloud path")
        url = Config.LIVEKIT_CLOUD_URL
        api_key = Config.LIVEKIT_CLOUD_API_KEY
        api_secret = Config.LIVEKIT_CLOUD_API_SECRET
    else:
        logger.info("[Routing] Using LiveKit Local path (Fallback)")
        url = Config.LIVEKIT_LOCAL_URL
        api_key = Config.LIVEKIT_LOCAL_API_KEY
        api_secret = Config.LIVEKIT_LOCAL_API_SECRET

    token = api.AccessToken(api_key, api_secret)
    token.with_identity(participant_name).with_name(participant_name).with_grants(
        api.VideoGrants(room_join=True, room=room_name)
    )

    return web.json_response({
        'token': token.to_jwt(),
        'url': url
    })

app = web.Application()
app.router.add_get('/token', handle_token)

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
    logger.info("Starting Token Server on port 3001")
    web.run_app(app, port=3001)
