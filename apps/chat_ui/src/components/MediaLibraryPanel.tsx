import React, { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { Check, FileText, Image as ImageIcon, Music2, Search, Trash2, Video, X } from 'lucide-react';
import type { MediaKind, MediaLibraryEntry } from '../types/chat';
import { deleteMedia, listMedia } from '../services/mediaApi';

interface Props { wsUrl: string; onClose: () => void; onSelect: (item: MediaLibraryEntry) => void; }
const filters: Array<{ label: string; value?: MediaKind }> = [
  { label: 'All' }, { label: 'Images', value: 'image' }, { label: 'Videos', value: 'video' }, { label: 'Audio', value: 'audio' }, { label: 'Docs', value: 'document' },
];
const iconFor = (kind: MediaKind) => kind === 'image' ? <ImageIcon size={18} /> : kind === 'video' ? <Video size={18} /> : kind === 'audio' ? <Music2 size={18} /> : <FileText size={18} />;

export const MediaLibraryPanel: React.FC<Props> = ({ wsUrl, onClose, onSelect }) => {
  const [items, setItems] = useState<MediaLibraryEntry[]>([]);
  const [filter, setFilter] = useState<MediaKind | undefined>();
  const [query, setQuery] = useState('');
  const [error, setError] = useState('');
  useEffect(() => { listMedia(wsUrl, filter, query).then(setItems).catch((e) => setError(e.message)); }, [wsUrl, filter, query]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  const remove = async (item: MediaLibraryEntry) => {
    if (!window.confirm(`Delete ${item.name}?`)) return;
    try { await deleteMedia(wsUrl, item.id); setItems((prev) => prev.filter((entry) => entry.id !== item.id)); } catch (e) { setError(e instanceof Error ? e.message : 'Delete failed'); }
  };
  return (
    <motion.div
      className="modal-backdrop"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.15, ease: [0.16, 1, 0.3, 1] }}
      onClick={onClose}
    >
      <motion.section
        className="media-library-panel"
        initial={{ opacity: 0, scale: 0.96, y: 8 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.97, y: 4 }}
        transition={{ type: 'spring', stiffness: 380, damping: 30 }}
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Media library"
      >
        <header><div><h2>Local library</h2><p>Reuse files uploaded to this device.</p></div><button onClick={onClose} aria-label="Close library"><X size={18} /></button></header>
        <div className="library-search"><Search size={16} /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search files" aria-label="Search files" /></div>
        <div className="library-filters">{filters.map((entry) => <button key={entry.label} className={filter === entry.value ? 'active' : ''} onClick={() => setFilter(entry.value)}>{entry.label}</button>)}</div>
        {error && <p className="inline-error">{error}</p>}
        <div className="library-grid">{items.map((item) => <article key={item.id} className="library-item">
          {item.type === 'image' ? <img src={item.thumbnailUrl || item.url} alt={item.name} loading="lazy" decoding="async" /> : <div className="library-file-icon">{iconFor(item.type)}</div>}
          <div className="library-item-name" title={item.name}>{item.name}</div>
          <div className="library-item-actions"><button onClick={() => onSelect(item)} title="Add attachment"><Check size={15} /></button><button onClick={() => remove(item)} title="Delete"><Trash2 size={15} /></button></div>
        </article>)}</div>
        {!items.length && !error && <div className="library-empty">No media saved yet.</div>}
      </motion.section>
    </motion.div>
  );
};

