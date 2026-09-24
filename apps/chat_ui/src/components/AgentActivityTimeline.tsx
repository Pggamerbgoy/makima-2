import React, { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { ChevronDown, ChevronRight, CircleCheck, CircleX, LoaderCircle, ShieldAlert } from 'lucide-react';
import type { AgentActivityEvent } from '../types/chat';

export const AgentActivityTimeline: React.FC<{ events?: AgentActivityEvent[]; live?: boolean }> = ({ events = [], live = false }) => {
  const [open, setOpen] = useState(false);
  const listRef = useRef<HTMLDivElement | null>(null);
  // Hermes-style: pop open the moment work starts, stay open after.
  useEffect(() => { if (live) setOpen(true); }, [live]);
  // Follow the latest step while open.
  useEffect(() => {
    const el = listRef.current;
    if (el && open) el.scrollTop = el.scrollHeight;
  }, [events.length, open]);
  if (!events.length) return null;
  return (
    <div className="agent-activity">
      <button className="agent-activity-toggle" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />} <span>Agent activity</span>
        {live && <span className="live-badge"><span className="live-dot" />LIVE</span>}
        <span className="agent-activity-count">{events.length}</span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="activity-list"
            className="agent-activity-list agent-activity-rail"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
            style={{ overflow: 'hidden' }}
          >
            <div ref={listRef} style={{ maxHeight: '220px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {events.map((event, index) => (
              <motion.div
                className="agent-activity-row"
                key={event.id}
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: Math.min(index * 0.04, 0.32), duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
              >
                {(event.status === 'error' || event.type.includes('error')) ? <CircleX size={15} color="var(--danger)" /> : event.type.includes('guardrail') ? <ShieldAlert size={15} color="var(--warning)" /> : event.status === 'done' ? <span className="check-pop"><CircleCheck size={15} color="var(--success)" /></span> : <LoaderCircle size={15} className="spin" />}
                <div><strong>{event.agent || event.type.replaceAll('_', ' ')}</strong><span>{event.message || event.status || 'Working'}</span></div>
                {typeof event.progress === 'number' && <small>{event.progress}%</small>}
              </motion.div>
            ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};
