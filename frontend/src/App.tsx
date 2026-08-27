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

  // C5 identity contract: anonymous device-scoped UUID (localStorage, never PII)
  const getDeviceId = (): string => {
    let id = localStorage.getItem('aiva_device_id');
    if (!id) {
      id = (crypto.randomUUID ? crypto.randomUUID() : 'dev-' + Math.random().toString(36).slice(2) + Date.now().toString(36));
      localStorage.setItem('aiva_device_id', id);
    }
    return id;
  };

  const resetMemory = useCallback(() => {
    localStorage.removeItem('aiva_device_id');
    alert('Memory reset. A fresh identity will be used on your next conversation.');
  }, []);

  const startConversation = useCallback(async () => {
    try {
      setConnecting(true);
      const randomRoom = 'room-' + Math.random().toString(36).substring(7);
      const res = await fetch(`http://localhost:3001/token?room=${randomRoom}&device=${getDeviceId()}`);
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

      <div style={{ height: '50px', marginTop: '20px', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px' }}>
        {audioTrack ? (
          <BarVisualizer state={state} trackRef={audioTrack} />
        ) : (
          <span style={{ color: '#666', fontSize: '14px' }}>
            {state === 'speaking' ? '🔊 speaking…' : state === 'thinking' ? '🤔 thinking…' : '👂 listening…'}
          </span>
        )}
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
