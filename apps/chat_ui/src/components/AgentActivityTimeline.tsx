import React, { useState } from 'react';
import { ChevronDown, ChevronRight, CircleCheck, CircleX, LoaderCircle, ShieldAlert } from 'lucide-react';
import type { AgentActivityEvent } from '../types/chat';

export const AgentActivityTimeline: React.FC<{ events?: AgentActivityEvent[] }> = ({ events = [] }) => {
  const [open, setOpen] = useState(false);
  if (!events.length) return null;
  return <div className="agent-activity">
    <button className="agent-activity-toggle" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
      {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />} <span>Agent activity</span><span className="agent-activity-count">{events.length}</span>
    </button>
    {open && <div className="agent-activity-list">{events.map((event) => <div className="agent-activity-row" key={event.id}>
      {event.type.includes('error') ? <CircleX size={15} color="#ef6b73" /> : event.type.includes('guardrail') ? <ShieldAlert size={15} color="#f5b942" /> : event.status === 'done' ? <CircleCheck size={15} color="#54c58a" /> : <LoaderCircle size={15} className="spin" />}
      <div><strong>{event.agent || event.type.replaceAll('_', ' ')}</strong><span>{event.message || event.status || 'Working'}</span></div>
      {typeof event.progress === 'number' && <small>{event.progress}%</small>}
    </div>)}</div>}
  </div>;
};

