import asyncio
from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli
from .config import Config

async def entrypoint(ctx: JobContext):
    print("Connecting to room...")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    print(f"Agent connected to room: {ctx.room.name}")
    
    # In later modules, we will initialize the AgentSession and AI components here.

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
        )
    )
