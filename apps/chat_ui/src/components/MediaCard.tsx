import React, { useState } from 'react';
import { Copy, Download, FileText, Image as ImageIcon, Music2, Share2, Video } from 'lucide-react';
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
    else await navigator.clipboard?.writeText(item.url);
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
        <span className="media-card-status">{item.status === 'processing' ? 'Processing…' : item.mimeType || item.type}</span>
      </div>
      {item.type === 'image' && (
        <button className="media-image-button" onClick={() => setLightbox(true)} aria-label={`Open ${item.title || 'image'} fullscreen`}>
          <img src={url} alt={item.alt || item.title || 'Attached image'} onError={() => setFailed(true)} />
        </button>
      )}
      {item.type === 'video' && <video src={url} controls playsInline preload="metadata" onError={() => setFailed(true)} />}
      {item.type === 'audio' && <audio src={url} controls preload="metadata" onError={() => setFailed(true)} />}
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
      <ImageViewerModal imageUrl={lightbox ? url : null} imageName={item.title} onClose={() => setLightbox(false)} />
    </div>
  );
};

export const MediaStrip: React.FC<{ items?: MediaItem[] }> = ({ items = [] }) => (
  items.length ? <div className="media-strip">{items.map((item) => <MediaCard key={item.id} item={item} />)}</div> : null
);
