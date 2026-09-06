import React, { useEffect, useState } from 'react';
import { Copy, Download, Minus, Plus, Share2, X } from 'lucide-react';

interface ImageViewerModalProps {
  imageUrl: string | null;
  imageName?: string;
  onClose: () => void;
}

export const ImageViewerModal: React.FC<ImageViewerModalProps> = ({
  imageUrl,
  imageName = 'Image',
  onClose,
}) => {
  const [zoom, setZoom] = useState(1);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); if (event.key === '+') setZoom((value) => Math.min(3, value + 0.25)); if (event.key === '-') setZoom((value) => Math.max(0.5, value - 0.25)); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  useEffect(() => setZoom(1), [imageUrl]);

  if (!imageUrl) return null;

  const handleDownload = () => {
    const a = document.createElement('a');
    a.href = imageUrl;
    a.download = imageName;
    a.click();
  };
  const handleCopy = async () => { try { const response = await fetch(imageUrl); const blob = await response.blob(); await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]); } catch { await navigator.clipboard?.writeText(imageUrl); } };
  const handleShare = async () => { if (navigator.share) await navigator.share({ title: imageName, url: imageUrl }).catch(() => undefined); else await navigator.clipboard?.writeText(imageUrl); };

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(0, 0, 0, 0.85)',
        backdropFilter: 'blur(10px)',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 200,
        padding: '24px',
      }}
      onClick={onClose}
    >
      {/* Top Header */}
      <div
        style={{
          position: 'absolute',
          top: '20px',
          right: '24px',
          display: 'flex',
          gap: '12px',
          zIndex: 210,
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <button onClick={() => setZoom((value) => Math.max(0.5, value - 0.25))} title="Zoom out" style={{ background: 'rgba(255,255,255,.15)', border: 0, color: '#fff', padding: 10, borderRadius: 20 }}><Minus size={18} /></button>
        <button onClick={() => setZoom((value) => Math.min(3, value + 0.25))} title="Zoom in" style={{ background: 'rgba(255,255,255,.15)', border: 0, color: '#fff', padding: 10, borderRadius: 20 }}><Plus size={18} /></button>
        <button onClick={handleCopy} title="Copy image" style={{ background: 'rgba(255,255,255,.15)', border: 0, color: '#fff', padding: 10, borderRadius: 20 }}><Copy size={18} /></button>
        <button onClick={handleShare} title="Share image" style={{ background: 'rgba(255,255,255,.15)', border: 0, color: '#fff', padding: 10, borderRadius: 20 }}><Share2 size={18} /></button>
        <button
          onClick={handleDownload}
          title="Download image"
          style={{
            background: 'rgba(255, 255, 255, 0.15)',
            border: 'none',
            color: '#fff',
            cursor: 'pointer',
            padding: '10px',
            borderRadius: '50%',
            display: 'flex',
          }}
        >
          <Download size={20} />
        </button>
        <button
          onClick={onClose}
          title="Close viewer"
          style={{
            background: 'rgba(255, 255, 255, 0.15)',
            border: 'none',
            color: '#fff',
            cursor: 'pointer',
            padding: '10px',
            borderRadius: '50%',
            display: 'flex',
          }}
        >
          <X size={20} />
        </button>
      </div>

      {/* Image Container */}
      <div
        style={{
          maxWidth: '90vw',
          maxHeight: '85vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          overflow: 'hidden',
          borderRadius: '16px',
          boxShadow: '0 8px 32px rgba(0, 0, 0, 0.6)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <img
          src={imageUrl}
          alt={imageName}
          style={{
            maxWidth: '100%',
            maxHeight: '85vh',
            objectFit: 'contain',
            borderRadius: '16px',
            transform: `scale(${zoom})`,
            transition: 'transform .2s ease',
          }}
        />
      </div>
    </div>
  );
};
