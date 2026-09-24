import React, { useRef, useState } from 'react';
import { Copy, Download, FileText, Image as ImageIcon, Music2, Pause, Play, Share2, Video } from 'lucide-react';
import type { MediaItem } from '../types/chat';
import { ImageViewerModal } from './ImageViewerModal';

function safeMediaUrl(value: string): string | null {
  try {
    const url = new URL(value, window.location.origin);
    if (url.protocol === 'http:' || url.protocol === 'https:' || url.protocol === 'blob:') return url.toString();
    return null;
  } catch {
    return null;
  }
}

const kindIcon = (type: MediaItem['type']) => {
  if (type === 'image') return <ImageIcon size={18} />;
  if (type === 'video') return <Video size={18} />;
  if (type === 'audio') return <Music2 size={18} />;
  return <FileText size={18} />;
};

export const MediaCard: React.FC<{ item: MediaItem }> = ({ item }) => {
  const [lightbox, setLightbox] = useState(false);
  const [failed, setFailed] = useState(false);
  const url = safeMediaUrl(item.url);
  const downloadUrl = item.id ? item.url.replace(/\/content(?:\?.*)?$/, '/download') : item.url;

  const share = async () => {
    if (navigator.share) await navigator.share({ title: item.title || 'Makima media', url: item.url }).catch(() => undefined);
    else await navigator.clipboard?.writeText(item.url).catch(() => undefined);
  };

  const copyImage = async () => {
    if (item.type !== 'image' || !url || !navigator.clipboard?.write) return;
    try {
      const response = await fetch(url);
      const blob = await response.blob();
      await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]);
    } catch {
      await navigator.clipboard?.writeText(url);
    }
  };

  if (!url || failed) {
    return <div className="media-card media-card-error">Unable to preview {item.title || 'this media item'}.</div>;
  }

  return (
    <div className="media-card">
      <div className="media-card-header">
        <span className="media-card-title">{kindIcon(item.type)} {item.title || item.type}</span>
        {item.status === 'processing' ? (
          <span className="thinking-dots" aria-label="Processing"><i /><i /><i /></span>
        ) : (
          <span className="media-card-status">{item.mimeType || item.type}</span>
        )}
      </div>
      {item.type === 'image' && (
        <button className="media-image-button" onClick={() => setLightbox(true)} aria-label={`Open ${item.title || 'image'} fullscreen`}>
          <img src={url} alt={item.alt || item.title || 'Attached image'} loading="lazy" decoding="async" onError={() => setFailed(true)} />
        </button>
      )}
      {item.type === 'video' && <video src={url} controls playsInline preload="metadata" onError={() => setFailed(true)} />}
      {item.type === 'audio' && <AudioPlayer url={url} title={item.title} />}
      {item.type === 'document' && (
        <a className="media-document-preview" href={downloadUrl} target="_blank" rel="noreferrer">
          <FileText size={28} /> <span>{item.title || 'Open document'}</span>
        </a>
      )}
      <div className="media-card-actions">
        {item.type === 'image' && <button onClick={copyImage} title="Copy image"><Copy size={15} /></button>}
        <button onClick={share} title="Share media"><Share2 size={15} /></button>
        {item.downloadable !== false && <a href={downloadUrl} download={item.title} title="Download media"><Download size={15} /></a>}
      </div>
      {lightbox && (
        <ImageViewerModal imageUrl={url} imageName={item.title} onClose={() => setLightbox(false)} />
      )}
    </div>
  );
};

export const MediaStrip: React.FC<{ items?: MediaItem[] }> = ({ items = [] }) => (
  items.length ? <div className="media-strip">{items.map((item) => <MediaCard key={item.id} item={item} />)}</div> : null
);

const fmtTime = (s: number): string => {
  if (!Number.isFinite(s) || s < 0) return '0:00';
  return `${Math.floor(s / 60)}:${Math.floor(s % 60).toString().padStart(2, '0')}`;
};

const AudioPlayer: React.FC<{ url: string; title?: string }> = ({ url, title }) => {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);

  const toggle = () => {
    const a = audioRef.current;
    if (!a) return;
    if (a.paused) void a.play().catch(() => setPlaying(false));
    else a.pause();
  };
  const seek = (e: React.MouseEvent<HTMLDivElement>) => {
    const a = audioRef.current;
    if (!a || !Number.isFinite(a.duration) || a.duration <= 0) return;
    const r = e.currentTarget.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    a.currentTime = ratio * a.duration;
  };
  const onKeySeek = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const a = audioRef.current;
    if (!a) return;
    if (e.key === 'ArrowRight') a.currentTime = Math.min(a.duration || 0, a.currentTime + 5);
    else if (e.key === 'ArrowLeft') a.currentTime = Math.max(0, a.currentTime - 5);
    else if (e.key === 'Home') a.currentTime = 0;
    else if (e.key === 'End' && Number.isFinite(a.duration)) a.currentTime = a.duration;
    else return;
    e.preventDefault();
  };

  return (
    <div className="media-audio-player">
      <audio
        ref={audioRef}
        src={url}
        preload="metadata"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
        onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
        onEnded={() => setPlaying(false)}
      />
      <button
        type="button"
        className="media-play-btn"
        onClick={toggle}
        aria-label={playing ? `Pause ${title || 'audio'}` : `Play ${title || 'audio'}`}
        title={playing ? 'Pause' : 'Play'}
      >
        {playing ? <Pause size={15} /> : <Play size={15} />}
      </button>
      <div className="media-track">
        <div className="media-time"><span>{fmtTime(time)}</span><span>{fmtTime(duration)}</span></div>
        <div
          className="media-progress"
          role="slider"
          tabIndex={0}
          aria-label={`Seek ${title || 'audio'}`}
          aria-valuemin={0}
          aria-valuemax={Math.round(duration || 0)}
          aria-valuenow={Math.round(time)}
          onClick={seek}
          onKeyDown={onKeySeek}
          title="Seek (arrow keys work too)"
        >
          <div className="media-progress-fill" style={{ width: duration > 0 ? `${(time / duration) * 100}%` : '0%' }} />
        </div>
      </div>
    </div>
  );
};
