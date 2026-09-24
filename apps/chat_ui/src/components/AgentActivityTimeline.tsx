import React, { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { ChevronDown, ChevronRight, CircleCheck, CircleX, LoaderCircle, ShieldAlert } from 'lucide-react';
import type { AgentActivityEvent } from '../types/chat';

export const AgentActivityTimeline: React.FC<{ events?: AgentActivityEvent[] }> = ({ events = [] }) => {
  const [open, setOpen] = useState(false);
  if (!events.length) return null;
  return (
    <div className="agent-activity">
      <button className="agent-activity-toggle" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />} <span>Agent activity</span><span className="agent-activity-count">{events.length}</span>
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
            {events.map((event, index) => (
              <motion.div
                className="agent-activity-row"
                key={event.id}
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: Math.min(index * 0.04, 0.32), duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
              >
                {(event.status === 'error' || event.type.includes('error')) ? <CircleX size={15} color="#ef6b73" /> : event.type.includes('guardrail') ? <ShieldAlert size={15} color="#f5b942" /> : event.status === 'done' ? <span className="check-pop"><CircleCheck size={15} color="#54c58a" /></span> : <LoaderCircle size={15} className="spin" />}
                <div><strong>{event.agent || event.type.replaceAll('_', ' ')}</strong><span>{event.message || event.status || 'Working'}</span></div>
                {typeof event.progress === 'number' && <small>{event.progress}%</small>}
              </motion.div>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};
