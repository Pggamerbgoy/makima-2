import React, { useState } from 'react';
import { ShieldAlert, CheckCircle2, XCircle, Loader2 } from 'lucide-react';

export const ActionConfirmationCard: React.FC<{
  action: string;
  description: string;
  riskLevel?: string;
  status?: 'pending' | 'approved' | 'rejected';
  onApprove: () => void;
  onReject: () => void;
}> = ({ action, description, riskLevel = 'medium', status = 'pending', onApprove, onReject }) => {
  const [localStatus, setLocalStatus] = useState<'pending' | 'approving' | 'rejecting' | 'approved' | 'rejected'>(status);

  // Sync if parent updates status
  React.useEffect(() => {
    if (status && status !== 'pending') {
      setLocalStatus(status);
    }
  }, [status]);

  const handleApprove = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (localStatus !== 'pending') return;
    setLocalStatus('approving');
    onApprove();
    setTimeout(() => setLocalStatus('approved'), 300);
  };

  const handleReject = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (localStatus !== 'pending') return;
    setLocalStatus('rejecting');
    onReject();
    setTimeout(() => setLocalStatus('rejected'), 300);
  };

  const isResolved = localStatus === 'approved' || localStatus === 'rejected';
  const isBusy = localStatus === 'approving' || localStatus === 'rejecting';

  return (
    <div className={`action-confirm-card ${isResolved ? `action-confirm-${localStatus}` : ''}`}>
      <div className="action-confirm-title">
        <ShieldAlert size={18} />
        <span>{isResolved ? (localStatus === 'approved' ? 'Action Approved' : 'Action Rejected') : 'Makima needs your approval'}</span>
      </div>
      <p>{description}</p>
      <small>Action: <code>{action}</code> · Risk: <span className={`risk-badge risk-${riskLevel.toLowerCase()}`}>{riskLevel}</span></small>
      <div className="action-confirm-footer">
        {localStatus === 'approved' ? (
          <span className="action-status-badge approved">
            <CheckCircle2 size={15} /> Approved
          </span>
        ) : localStatus === 'rejected' ? (
          <span className="action-status-badge rejected">
            <XCircle size={15} /> Rejected
          </span>
        ) : (
          <div className="action-button-group">
            <button
              type="button"
              className="action-btn-reject"
              onClick={handleReject}
              disabled={isBusy}
            >
              {localStatus === 'rejecting' ? <Loader2 size={13} className="spin" /> : <XCircle size={13} />}
              <span>Reject</span>
            </button>
            <button
              type="button"
              className="approve action-btn-approve"
              onClick={handleApprove}
              disabled={isBusy}
            >
              {localStatus === 'approving' ? <Loader2 size={13} className="spin" /> : <CheckCircle2 size={13} />}
              <span>Approve</span>
            </button>
          </div>
        )}
      </div>
    </div>
  );
};
