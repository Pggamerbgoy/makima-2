import type { MediaKind, MediaLibraryEntry } from '../types/chat';

function httpBaseFromWs(wsUrl: string): string {
  try {
    const url = new URL(wsUrl);
    url.protocol = url.protocol === 'wss:' ? 'https:' : 'http:';
    url.pathname = '';
    url.search = '';
    url.hash = '';
    return url.toString().replace(/\/$/, '');
  } catch {
    return 'http://127.0.0.1:8080';
  }
}

function normalizeItem(raw: any, base: string): MediaLibraryEntry {
  const type = (raw.kind || raw.type || 'document') as MediaKind;
  const path = raw.url || `/media/${raw.id}/content`;
  return {
    id: raw.id,
    type,
    url: path.startsWith('http') ? path : `${base}${path}`,
    thumbnailUrl: raw.thumbnail_url ? (raw.thumbnail_url.startsWith('http') ? raw.thumbnail_url : `${base}${raw.thumbnail_url}`) : undefined,
    mimeType: raw.mime_type,
    title: raw.name,
    alt: raw.name,
    downloadable: true,
    size: raw.size,
    status: raw.status || 'ready',
    name: raw.name || raw.id,
  };
}

export async function uploadMedia(
  file: File,
  wsUrl: string,
  onProgress?: (progress: number) => void,
): Promise<MediaLibraryEntry> {
  const base = httpBaseFromWs(wsUrl);
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${base}/media/upload`);
    xhr.responseType = 'json';
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress?.(Math.round((event.loaded / event.total) * 100));
    };
    xhr.onerror = () => reject(new Error('Media upload failed. Check the Makima connection.'));
    xhr.onload = () => {
      const body = xhr.response || {};
      if (xhr.status >= 200 && xhr.status < 300) resolve(normalizeItem(body, base));
      else reject(new Error(body.error || `Media upload failed (${xhr.status}).`));
    };
    const form = new FormData();
    form.append('file', file, file.name);
    xhr.send(form);
  });
}

export async function listMedia(wsUrl: string, kind?: MediaKind, query?: string): Promise<MediaLibraryEntry[]> {
  const base = httpBaseFromWs(wsUrl);
  const params = new URLSearchParams();
  if (kind) params.set('kind', kind);
  if (query) params.set('q', query);
  const response = await fetch(`${base}/media${params.toString() ? `?${params}` : ''}`);
  if (!response.ok) throw new Error('Could not load the local media library.');
  const data = await response.json();
  return (data.media || []).map((item: any) => normalizeItem(item, base));
}

export async function deleteMedia(wsUrl: string, mediaId: string): Promise<void> {
  const base = httpBaseFromWs(wsUrl);
  const response = await fetch(`${base}/media/${encodeURIComponent(mediaId)}`, { method: 'DELETE' });
  if (!response.ok) throw new Error('Could not delete this media item.');
}
