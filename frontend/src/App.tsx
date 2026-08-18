import { useState, useCallback } from 'react';
import {
  LiveKitRoom,
  RoomAudioRenderer,
  BarVisualizer,
  useVoiceAssistant,
} from '@livekit/components-react';
import '@livekit/components-styles';

export default function App() {
  const [token, setToken] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);

  const startConversation = useCallback(async () => {
    try {
      setConnecting(true);
      const res = await fetch('http://localhost:3001/token?room=voice-agent-room');
      const data = await res.json();
      setToken(data.token);
    } catch (e) {
      console.error('Failed to fetch token', e);
      alert('Failed to get token. Is the token server running?');
    } finally {
      setConnecting(false);
    }
  }, []);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '50px', fontFamily: 'sans-serif' }}>
      <h1>Voice Agent</h1>
      
      {!token ? (
        <button 
          onClick={startConversation} 
          disabled={connecting}
          style={{ padding: '10px 20px', fontSize: '16px', cursor: 'pointer' }}
        >
          {connecting ? 'Connecting...' : 'Start Conversation'}
        </button>
      ) : (
        <LiveKitRoom
          serverUrl="ws://127.0.0.1:7880"
          token={token}
          connect={true}
          audio={true}
          video={false}
          onDisconnected={() => setToken(null)}
        >
          <RoomAudioRenderer />
          <VoiceAssistantUI />
        </LiveKitRoom>
      )}
    </div>
  );
}

function VoiceAssistantUI() {
  const { state, audioTrack } = useVoiceAssistant();

  return (
    <div style={{ marginTop: '20px', textAlign: 'center' }}>
      <div style={{ display: 'flex', gap: '10px', alignItems: 'center', justifyContent: 'center' }}>
        <div style={{
          width: '12px',
          height: '12px',
          borderRadius: '50%',
          backgroundColor: state === 'connected' ? 'green' : 'gray'
        }} />
        <span>State: {state}</span>
      </div>
      
      <div style={{ height: '50px', marginTop: '20px' }}>
        {audioTrack && (
          <BarVisualizer state={state} trackRef={audioTrack} />
        )}
      </div>

      <p style={{ marginTop: '30px' }}>
        <button onClick={() => window.location.reload()}>Disconnect</button>
      </p>
    </div>
  );
}
