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
  const [serverUrl, setServerUrl] = useState<string>('');
  const [connecting, setConnecting] = useState(false);

  const startConversation = useCallback(async () => {
    try {
      setConnecting(true);
      const randomRoom = 'room-' + Math.random().toString(36).substring(7);
      const res = await fetch(`http://localhost:3001/token?room=${randomRoom}`);
      const data = await res.json();
      setToken(data.token);
      setServerUrl(data.url);
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
          serverUrl={serverUrl}
          token={token}
          connect={true}
          audio={{ echoCancellation: true, noiseSuppression: true, autoGainControl: true }}
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
          width: '12px', height: '12px', borderRadius: '50%',
          backgroundColor: state === 'connected' ? 'green' : 'orange'
        }} />
        <span>State: {state}</span>
      </div>

      <div style={{ height: '50px', marginTop: '20px' }}>
        {audioTrack && <BarVisualizer state={state} trackRef={audioTrack} />}
      </div>

      <p style={{ color: '#888', fontSize: '14px' }}>Mic is live — just speak!</p>

      <div style={{ marginTop: '10px' }}>
        <button onClick={() => window.location.reload()} style={{ padding: '10px 20px', cursor: 'pointer' }}>
          Disconnect
        </button>
      </div>
    </div>
  );
}
